"""``toolsets/_data.py``: the data-access helpers the diagnosis, composition and exploration tools share.

* unknown metric ids come back as a failed result (never a raised ``ModelRetry``: the agent's per-tool retry cap
  would fail the whole turn if the model ignored it);
* identical MCP queries in one turn cost one round trip and one budget unit;
* an evidence row is written once, however often the same fact is fetched;
* fetch failures are classified so the model changes the call instead of replaying it.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from typing import Any

from seleric_swarm.agent.artifacts import EvidenceArtifact
from seleric_swarm.agent.dependencies import ExecutionLimits, NullMcpClient, SelericDeps
from seleric_swarm.conversations.contracts import ArtifactProvenance, ContextBundle, Principal
from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta, CatalogueSnapshot
from seleric_swarm.state.artifacts import InMemoryArtifactStore
from seleric_swarm.toolsets import _data


class Ctx:
    def __init__(self, deps: SelericDeps) -> None:
        self.deps = deps


def _deps(mcp: Any = None, catalogue: CatalogueSnapshot | None = None, **limits: Any) -> SelericDeps:
    return SelericDeps(
        mission_id="m1",
        as_of=datetime(2026, 9, 18, tzinfo=UTC),
        principal=Principal(principal_id="p1", workspace_id="ws1", user_id="u1"),
        thread_id="t1",
        run_id="r1",
        trace_id="tr1",
        context=ContextBundle(),
        mcp_client=mcp or NullMcpClient(),
        artifact_store=InMemoryArtifactStore(),
        limits=ExecutionLimits(**limits),
        catalogue=catalogue or CatalogueSnapshot(),
    )


def _catalogue() -> CatalogueSnapshot:
    return CatalogueSnapshot(
        metrics=(
            CatalogueMetricMeta(id="net_sales", label="Net Sales", raw={"aliases": ["ns"]}),
            CatalogueMetricMeta(id="total_ad_spend", label="Ad Spend"),
        )
    )


# ------------------------------------------------------------------------------------------- unknown metric ids
async def test_a_near_miss_id_is_returned_with_the_closest_real_ids() -> None:
    res = await _data.reject_unknown_metric(Ctx(_deps(catalogue=_catalogue())), "net_sale")  # type: ignore[arg-type]
    assert res is not None and not res.success and res.retryable
    assert "net_sales" in res.summary


async def test_a_known_id_and_an_empty_catalogue_proceed() -> None:
    assert await _data.reject_unknown_metric(Ctx(_deps(catalogue=_catalogue())), "net_sales") is None  # type: ignore[arg-type]
    assert await _data.reject_unknown_metric(Ctx(_deps()), "anything") is None  # type: ignore[arg-type]


async def test_an_unmodelled_concept_is_a_final_not_available() -> None:
    res = await _data.reject_unknown_metric(Ctx(_deps(catalogue=_catalogue())), "zzzzzzz_unrelated")  # type: ignore[arg-type]
    assert res is not None and not res.success and res.retryable is False
    assert res.error_code == "UNSUPPORTED_QUERY"


class _ResolvingMcp(NullMcpClient):
    async def call(self, *, agent_id: str, capability: str, arguments: dict[str, Any]) -> Any:  # type: ignore[override]
        if capability == "seleric.catalogue_resolve_concept" and arguments.get("text") == "ad spend words":
            return {"kind": "resolved_concept", "metric_id": "total_ad_spend", "axes": {}}
        return {}


async def test_a_business_word_passed_as_an_id_is_pointed_at_its_catalogue_id() -> None:
    """Golden Q9 (2026-10-08): "spend" / "revenue" were reported "not modelled" and printed as "No data available"."""
    ctx = Ctx(dataclasses.replace(_deps(catalogue=_catalogue()), mcp_client=_ResolvingMcp()))
    res = await _data.reject_unknown_metric(ctx, "ad_spend_words")  # type: ignore[arg-type]
    assert res is not None and not res.success and "total_ad_spend" in res.summary


# ------------------------------------------------------------------------------------------- the query cache
class _CountingMcp:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls = 0

    async def call(self, *, agent_id: str, capability: str, arguments: dict[str, Any]) -> Any:
        self.calls += 1
        return self.payload


async def test_identical_queries_cost_one_round_trip_and_one_budget_unit() -> None:
    mcp = _CountingMcp({"rows": [{"units_sold": "2314"}], "provenance": {}})
    ctx = Ctx(_deps(mcp))
    args = {"measures": ["units_sold"], "granularity": "none"}
    first = await _data.cached_metrics_query(ctx, args)  # type: ignore[arg-type]
    second = await _data.cached_metrics_query(ctx, dict(args))  # type: ignore[arg-type]
    assert first == second and mcp.calls == 1
    assert ctx.deps.budget.cube_queries == 1


async def test_an_error_result_is_not_cached() -> None:
    class Flaky:
        calls = 0

        async def call(self, *, agent_id: str, capability: str, arguments: dict[str, Any]) -> Any:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("transient")
            return {"rows": [], "provenance": {}}

    mcp = Flaky()
    ctx = Ctx(_deps(mcp))
    args = {"measures": ["units_sold"]}
    assert "error" in await _data.cached_metrics_query(ctx, args)  # type: ignore[arg-type]
    assert "error" not in await _data.cached_metrics_query(ctx, args)  # type: ignore[arg-type]
    assert mcp.calls == 2


async def test_an_exhausted_budget_returns_an_error_not_a_fetch() -> None:
    mcp = _CountingMcp({"rows": [], "provenance": {}})
    ctx = Ctx(_deps(mcp, max_cube_queries=1))
    await _data.cached_metrics_query(ctx, {"measures": ["a"]})  # type: ignore[arg-type]
    res = await _data.cached_metrics_query(ctx, {"measures": ["b"]})  # type: ignore[arg-type]
    assert "budget exhausted" in res["error"] and mcp.calls == 1


# ------------------------------------------------------------------------------------------- evidence
def _evidence(value: float) -> EvidenceArtifact:
    return EvidenceArtifact(
        metric_id="net_sales", dimensions={}, grain="day",
        period_start=datetime(2026, 9, 1, tzinfo=UTC), period_end=datetime(2026, 9, 1, tzinfo=UTC),
        value=value, unit="INR", source_query={}, as_of=datetime(2026, 9, 18, tzinfo=UTC),
    )


def test_the_same_fact_is_written_once() -> None:
    ctx = Ctx(_deps())
    index = _data.evidence_index(ctx)  # type: ignore[arg-type]
    prov = ArtifactProvenance()
    first = _data.put_evidence(ctx, _evidence(100.0), index=index, raw_id="q1", provenance=prov)  # type: ignore[arg-type]
    again = _data.put_evidence(ctx, _evidence(100.0), index=index, raw_id="q1", provenance=prov)  # type: ignore[arg-type]
    other = _data.put_evidence(ctx, _evidence(101.0), index=index, raw_id="q1", provenance=prov)  # type: ignore[arg-type]
    assert first == again and other != first
    # a fresh index (a recovery retry with new deps, same store) finds the rows already written
    assert _data.evidence_index(ctx)[next(iter(_data.evidence_index(ctx)))] in {first, other}  # type: ignore[arg-type]
    assert len(ctx.deps.artifact_store.list_for_mission("m1")) == 2


# ------------------------------------------------------------------------------------------- failures
def test_fetch_failures_are_classified_so_the_model_changes_the_call() -> None:
    memory = _data.fetch_failure("x", "ResponseError: memory limit exceeded")
    assert memory.retryable is False and memory.error_code == "UNSUPPORTED_QUERY"
    invalid = _data.fetch_failure("x", "ValidationError: granularity input should be")
    assert invalid.retryable is False
    transient = _data.fetch_failure("x", "TimeoutError: slow")
    assert transient.retryable is True
    unavailable = _data.fetch_failure("x", "NotImplementedError: no mcp")
    assert unavailable.error_code == "MCP_UNAVAILABLE" and unavailable.retryable is False
