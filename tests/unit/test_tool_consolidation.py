"""Merged tools: ``find_metrics`` (concept resolver + search fallback + listing) and
``analyze`` (one entry point for the six calculations over fetched evidence)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from seleric_swarm.agent.dependencies import ExecutionLimits, SelericDeps
from seleric_swarm.conversations.contracts import ContextBundle, Principal
from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta, CatalogueSnapshot
from seleric_swarm.state.artifacts import InMemoryArtifactStore
from seleric_swarm.toolsets import analytics, semantic


class _Mcp:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def call(self, *, agent_id: str, capability: str, arguments: dict) -> dict:
        self.calls.append((capability, arguments))
        if capability == "seleric.catalogue_resolve_concept":
            if arguments["text"] == "net sales":
                return {"kind": "resolved_concept", "metric_id": "net_sales", "axes": {"basis": "net"}}
            return {"kind": "unknown_concept", "suggestions": []}
        if capability == "seleric.catalogue_search_metrics":
            return {"results": [{"id": "sessions", "name": "Sessions", "score": 0.9}]}
        return {}


def _ctx(mcp: _Mcp) -> SimpleNamespace:
    deps = SelericDeps(
        mission_id="MS3-t",
        as_of=datetime(2026, 10, 7, tzinfo=UTC),
        principal=Principal(principal_id="p", workspace_id="w", user_id="u"),
        thread_id="t",
        run_id="r",
        trace_id="tr",
        context=ContextBundle(),
        mcp_client=mcp,
        artifact_store=InMemoryArtifactStore(),
        limits=ExecutionLimits(),
        catalogue=CatalogueSnapshot(
            metrics=(
                CatalogueMetricMeta(id="net_sales", label="Net Sales", view="commerce"),
                CatalogueMetricMeta(id="sessions", label="Sessions", view="web_sessions"),
            )
        ),
    )
    return SimpleNamespace(deps=deps, run_step=1)


async def test_find_metrics_resolves_every_phrase_in_one_call_and_searches_only_misses():
    mcp = _Mcp()
    out = await semantic.find_metrics(_ctx(mcp), phrases=["net sales", "site visits", "net sales"])
    assert out.success
    assert "net sales -> net_sales" in out.summary
    assert "'site visits' has no single concept" in out.summary
    resolves = [a["text"] for c, a in mcp.calls if c == "seleric.catalogue_resolve_concept"]
    searches = [c for c, _ in mcp.calls if c == "seleric.catalogue_search_metrics"]
    assert sorted(resolves) == ["net sales", "site visits"]  # duplicates collapsed
    assert len(searches) == 1  # only the miss falls back to search


async def test_find_metrics_without_phrases_lists_the_catalogue():
    out = await semantic.find_metrics(_ctx(_Mcp()), domain="commerce")
    assert out.success and "net_sales" in out.summary and "sessions" not in out.summary


async def test_analyze_dispatches_and_validates_its_arguments(monkeypatch):
    seen: list[tuple[str, tuple]] = []

    def _stub(name):
        async def _fn(ctx, *args):
            seen.append((name, args))
            return SimpleNamespace(name=name)

        return _fn

    for name in ("compare_periods", "detect_anomalies", "contribution_analysis", "segment_decomposition",
                 "funnel_decomposition", "cohort_analysis"):
        monkeypatch.setattr(analytics, name, _stub(name))
    ctx = _ctx(_Mcp())
    assert (await analytics.analyze(ctx, ["e1"], "compare")).name == "compare_periods"
    assert (await analytics.analyze(ctx, ["e1"], "anomaly")).name == "detect_anomalies"
    assert (await analytics.analyze(ctx, ["e1"], "contribution", ["channel", "x"])).name == "contribution_analysis"
    assert seen[-1] == ("contribution_analysis", (["e1"], "channel"))
    assert (await analytics.analyze(ctx, ["e1"], "segments", ["a", "b"])).name == "segment_decomposition"
    assert (await analytics.analyze(ctx, ["e1"], "funnel")).name == "funnel_decomposition"
    assert (await analytics.analyze(ctx, ["e1"], "cohort")).name == "cohort_analysis"
    refused = await analytics.analyze(ctx, ["e1"], "contribution")
    assert refused.success is False and "dimensions" in refused.summary
