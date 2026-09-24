"""``SelericDeps.query_cache`` wired into ``toolsets/semantic.py``.

Regression for the live incident where a mission issued two byte-for-byte
identical ``seleric.metrics_query`` calls for the same metric/period two
seconds apart, and a ``drilldown()`` call re-fetched its own parent query
that an earlier plain ``query_metrics()`` call had already fetched — both
wasted a full MCP round trip inside the mission's fixed wall-clock budget.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from seleric_swarm.agent.dependencies import ExecutionLimits, SelericDeps
from seleric_swarm.conversations.contracts import ContextBundle, Principal
from seleric_swarm.state.artifacts import InMemoryArtifactStore
from seleric_swarm.toolsets import semantic


class FakeMcpClient:
    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, *, agent_id: str, capability: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((capability, arguments))
        return self.responses[capability]


class FakeRunContext:
    def __init__(self, deps: SelericDeps) -> None:
        self.deps = deps


def _deps(mcp_client: Any) -> SelericDeps:
    return SelericDeps(
        mission_id="mission-1",
        as_of=datetime(2026, 9, 18, tzinfo=UTC),
        principal=Principal(principal_id="p1", workspace_id="ws1", user_id="u1"),
        thread_id="thread-1",
        run_id="run-1",
        trace_id="trace-1",
        context=ContextBundle(),
        mcp_client=mcp_client,
        artifact_store=InMemoryArtifactStore(),
        limits=ExecutionLimits(),
    )


@pytest.mark.asyncio
async def test_identical_query_metrics_calls_hit_mcp_once():
    mcp = FakeMcpClient(
        {"seleric.metrics_query": {"rows": [{"units_sold": "2314"}], "provenance": {}}}
    )
    ctx = FakeRunContext(_deps(mcp))
    kwargs = {
        "metric_id": "units_sold",
        "dimensions": {},
        "grain": "none",
        "period_start": datetime(2026, 7, 1, tzinfo=UTC),
        "period_end": datetime(2026, 7, 31, tzinfo=UTC),
    }
    first = await semantic.query_metrics(ctx, **kwargs)
    second = await semantic.query_metrics(ctx, **kwargs)

    assert first.success is True
    assert second.success is True
    assert len([c for c in mcp.calls if c[0] == "seleric.metrics_query"]) == 1
    # A repeat identical query short-circuits: the raw-fetch cache serves the
    # rows (1 hit), then a counter-neutral ``peek`` finds the prior built
    # ToolResult and returns the stop-nudge before the ToolResult cache layer's
    # own get_or_fetch runs — so one recorded hit, not two.
    assert ctx.deps.query_cache.hits == 1


@pytest.mark.asyncio
async def test_third_identical_query_metrics_call_hard_stops_with_model_retry():
    # Live 2026-09-22 MS3-0bb3863a2e: the model ignored the soft nudge and
    # called the identical query 3x. The 2nd repeat must hard-stop (ModelRetry
    # → forces final_result) instead of handing back another "success".
    from pydantic_ai import ModelRetry

    mcp = FakeMcpClient(
        {"seleric.metrics_query": {"rows": [{"units_sold": "2314"}], "provenance": {}}}
    )
    ctx = FakeRunContext(_deps(mcp))
    kwargs = {
        "metric_id": "units_sold",
        "dimensions": {},
        "grain": "none",
        "period_start": datetime(2026, 7, 1, tzinfo=UTC),
        "period_end": datetime(2026, 7, 31, tzinfo=UTC),
    }
    await semantic.query_metrics(ctx, **kwargs)  # fetch
    second = await semantic.query_metrics(ctx, **kwargs)  # 1st repeat → nudge
    assert second.success is True and "ALREADY FETCHED" in second.summary
    with pytest.raises(ModelRetry):  # 2nd repeat → hard stop
        await semantic.query_metrics(ctx, **kwargs)
    # Still only one real Cube call throughout.
    assert len([c for c in mcp.calls if c[0] == "seleric.metrics_query"]) == 1
    assert "ALREADY FETCHED" in second.summary


@pytest.mark.asyncio
async def test_drilldown_parent_reuses_a_prior_plain_query_metrics_fetch():
    mcp = FakeMcpClient(
        {
            "seleric.metrics_query": {
                "query_id": "q1",
                "rows": [{"units_sold": "2314"}],
                "provenance": {"query_id": "q1"},
            },
            "seleric.metrics_drilldown": {
                "rows": [{"units_sold": "768", "product_title": "Pawveralls Suspender Boots"}],
                "provenance": {},
            },
        }
    )
    ctx = FakeRunContext(_deps(mcp))
    period_start = datetime(2026, 7, 1, tzinfo=UTC)
    period_end = datetime(2026, 7, 31, tzinfo=UTC)

    await semantic.query_metrics(
        ctx,
        metric_id="units_sold",
        dimensions={},
        grain="none",
        period_start=period_start,
        period_end=period_end,
    )
    result = await semantic.drilldown(
        ctx,
        metric_id="units_sold",
        dimension="product_title",
        period_start=period_start,
        period_end=period_end,
    )

    assert result.success is True
    # One metrics_query (the plain total) + one metrics_drilldown — the
    # drilldown's own parent query was served from cache, not refetched.
    query_calls = [c for c in mcp.calls if c[0] == "seleric.metrics_query"]
    drilldown_calls = [c for c in mcp.calls if c[0] == "seleric.metrics_drilldown"]
    assert len(query_calls) == 1
    assert len(drilldown_calls) == 1


@pytest.mark.asyncio
async def test_repeated_breakdown_call_reuses_artifacts_not_rewrites_them():
    """Live incident: a ~200-row product_title breakdown was fetched twice,
    18 seconds apart, via two separate query_metrics() calls with identical
    args — the MCP-level cache avoided the second network round trip, but
    the function still re-ran its per-row evidence-writing loop, doubling
    the artifacts the model had to read on its next turn."""
    mcp = FakeMcpClient(
        {
            "seleric.metrics_query": {
                "rows": [
                    {"product_orders": "216", "product_title": "Pawveralls Suspender Boots"},
                    {"product_orders": "108", "product_title": "Pawveralls Pro Suspender Boots"},
                ],
                "provenance": {},
            }
        }
    )
    ctx = FakeRunContext(_deps(mcp))
    kwargs = {
        "metric_id": "product_orders",
        "dimensions": {"product_title": ""},
        "grain": "none",
        "period_start": datetime(2026, 8, 21, tzinfo=UTC),
        "period_end": datetime(2026, 9, 21, tzinfo=UTC),
    }
    first = await semantic.query_metrics(ctx, **kwargs)
    second = await semantic.query_metrics(ctx, **kwargs)

    assert first.success is True and second.success is True
    assert first.artifact_ids == second.artifact_ids
    assert len([c for c in mcp.calls if c[0] == "seleric.metrics_query"]) == 1
    assert len(ctx.deps.artifact_store.list_for_mission("mission-1")) == 2


@pytest.mark.asyncio
async def test_different_metrics_still_fetch_independently():
    mcp = FakeMcpClient(
        {"seleric.metrics_query": {"rows": [{"units_sold": "2314"}], "provenance": {}}}
    )
    ctx = FakeRunContext(_deps(mcp))
    await semantic.query_metrics(
        ctx,
        metric_id="units_sold",
        dimensions={},
        grain="none",
        period_start=datetime(2026, 7, 1, tzinfo=UTC),
        period_end=datetime(2026, 7, 31, tzinfo=UTC),
    )
    await semantic.query_metrics(
        ctx,
        metric_id="units_sold",
        dimensions={},
        grain="none",
        period_start=datetime(2026, 8, 1, tzinfo=UTC),
        period_end=datetime(2026, 8, 31, tzinfo=UTC),
    )
    assert len([c for c in mcp.calls if c[0] == "seleric.metrics_query"]) == 2
    # Two distinct periods -> both cache layers (raw-fetch + ToolResult) miss
    # for each call.
    assert ctx.deps.query_cache.misses == 4
    assert ctx.deps.query_cache.hits == 0
