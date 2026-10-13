"""Diagnosis fetches: one multi-measure query per view fills the per-metric cache; failures fall back to single queries."""
from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from seleric_swarm.agent.dependencies import ExecutionLimits, NullMcpClient, SelericDeps
from seleric_swarm.conversations.contracts import ContextBundle, Principal, PrincipalAuthMethod
from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta, CatalogueSnapshot
from seleric_swarm.state.artifacts import InMemoryArtifactStore
from seleric_swarm.toolsets import diagnosis

DAYS = ["2026-10-10T00:00:00.000", "2026-10-11T00:00:00.000"]


def _meta(mid: str, view: str) -> CatalogueMetricMeta:
    return CatalogueMetricMeta(id=mid, label=mid, view=view, supported_dimensions=["brand_id"], raw={"view": view})


def _rows(measures: list[str], scale: float = 1.0) -> list[dict[str, Any]]:
    return [{"order_date.day": d, "order_date": d, **{m: str((i + 1) * 10 * scale) for m in measures}} for i, d in enumerate(DAYS)]


class FakeMcp(NullMcpClient):
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def call(self, *, agent_id: str, capability: str, arguments: dict[str, Any]) -> Any:
        ms = arguments["measures"]
        self.calls.append(ms)
        if "broken" in ms:
            return {"error": "Metric 'broken' cannot be answered: status=broken"}
        if "other_axis" in ms:  # a composed result: one part per time axis
            first = [m for m in ms if m != "other_axis"]
            return {"query_id": "qc", "composed": True, "parts": [
                {"query_id": "qa", "columns": ["order_date", "order_date.day", *first], "rows": _rows(first),
                 "provenance": {"query_id": "qa", "metric_ids": first, "currency": "INR"}},
                {"query_id": "qb", "columns": ["order_date", "order_date.day", "other_axis"], "rows": _rows(["other_axis"], 2),
                 "provenance": {"query_id": "qb", "metric_ids": ["other_axis"]}}]}
        return {"query_id": "q1", "columns": ["order_date", "order_date.day", *ms], "rows": _rows(ms),
                "provenance": {"query_id": "q1", "metric_ids": ms, "currency": "INR"}}


def _ctx(mcp: FakeMcp, metrics: list[CatalogueMetricMeta]) -> Any:
    deps = SelericDeps(
        mission_id="m", as_of=datetime(2026, 10, 12, tzinfo=UTC),
        principal=Principal(principal_id="p", workspace_id="w", user_id="u", authenticated=False, auth_method=PrincipalAuthMethod.ANONYMOUS),
        thread_id="t", run_id="r", trace_id="x", context=ContextBundle(), mcp_client=mcp,
        artifact_store=InMemoryArtifactStore(), limits=ExecutionLimits(), catalogue=CatalogueSnapshot(metrics=tuple(metrics)))
    return SimpleNamespace(deps=deps)


@pytest.mark.asyncio
async def test_one_query_per_view_and_each_metric_gets_its_own_slice():
    mcp = FakeMcp()
    metrics = [_meta("a", "v1"), _meta("b", "v1"), _meta("c", "v1"), _meta("solo", "v2")]
    ctx = _ctx(mcp, metrics)
    jobs = [(m.id, None) for m in metrics]
    out = await diagnosis._fetch_all(ctx, jobs, date(2026, 10, 10), date(2026, 10, 11), {})
    assert sorted(len(c) for c in mcp.calls) == [1, 3]  # v1 batched into one query, the lone v2 metric stays single
    for (m, _), (res, args) in zip(jobs, out, strict=True):
        assert args is not None and not res.get("error")
        assert res["provenance"]["metric_ids"] == [m] and m in res["columns"]
        assert set(res["rows"][0]) == {"order_date", "order_date.day", m}  # only its own measure column
        assert res["_args"]["measures"] == [m]
    assert diagnosis._parse_series(out[0][0], "a")[date(2026, 10, 11)] == 20.0


@pytest.mark.asyncio
async def test_composed_result_is_split_by_part():
    mcp = FakeMcp()
    metrics = [_meta("a", "v1"), _meta("other_axis", "v1")]
    ctx = _ctx(mcp, metrics)
    out = await diagnosis._fetch_all(ctx, [("a", None), ("other_axis", None)], date(2026, 10, 10), date(2026, 10, 11), {})
    assert len(mcp.calls) == 1
    assert diagnosis._parse_series(out[0][0], "a")[date(2026, 10, 11)] == 20.0
    assert diagnosis._parse_series(out[1][0], "other_axis")[date(2026, 10, 11)] == 40.0  # the other axis's own part


@pytest.mark.asyncio
async def test_a_failing_group_falls_back_to_single_queries():
    mcp = FakeMcp()
    metrics = [_meta("a", "v1"), _meta("broken", "v1")]
    ctx = _ctx(mcp, metrics)
    out = await diagnosis._fetch_all(ctx, [("a", None), ("broken", None)], date(2026, 10, 10), date(2026, 10, 11), {})
    assert mcp.calls[0] == ["a", "broken"] and sorted(mcp.calls[1:]) == [["a"], ["broken"]]
    assert out[0][1] is not None and out[1][1] is None  # a is fine, broken keeps its own error
    assert out[1][0].get("error")


@pytest.mark.asyncio
async def test_cached_metrics_are_not_refetched():
    mcp = FakeMcp()
    metrics = [_meta("a", "v1"), _meta("b", "v1")]
    ctx = _ctx(mcp, metrics)
    jobs = [("a", None), ("b", None)]
    await diagnosis._fetch_all(ctx, jobs, date(2026, 10, 10), date(2026, 10, 11), {})
    await diagnosis._fetch_all(ctx, jobs, date(2026, 10, 10), date(2026, 10, 11), {})
    assert len(mcp.calls) == 1
