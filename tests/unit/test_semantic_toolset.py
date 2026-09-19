"""Unit + contract tests for toolsets/semantic.py (Sprint 1, Profile B).

Contract test: the ToolResult envelope invariants from
docs/refactor/CONTRACTS.md hold regardless of which tool produced it.
Unit tests: query_metrics/drilldown/search_semantics logic against a fake
MCP client — no live network, no MetricRegistry/resolve_measure heuristic
anywhere in the call path (that's the whole point of this profile).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from seleric_swarm.agent.dependencies import ExecutionLimits, SelericDeps
from seleric_swarm.agent.output import ToolResult
from seleric_swarm.conversations.contracts import ContextBundle, Principal
from seleric_swarm.state.artifacts import InMemoryArtifactStore
from seleric_swarm.toolsets import semantic


class FakeMcpClient:
    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, *, agent_id: str, capability: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((capability, arguments))
        response = self.responses.get(capability)
        if isinstance(response, Exception):
            raise response
        return response


class FakeRunContext:
    def __init__(self, deps: SelericDeps) -> None:
        self.deps = deps


def _deps(mcp_client: FakeMcpClient) -> SelericDeps:
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


# ---- ToolResult envelope contract ------------------------------------------------


def test_tool_result_success_false_requires_error_code():
    with pytest.raises(ValueError, match="error_code"):
        ToolResult(success=False, summary="failed")


def test_tool_result_success_false_forbids_artifact_ids():
    with pytest.raises(ValueError, match="artifact_ids"):
        ToolResult(success=False, summary="failed", error_code="X", artifact_ids=["a1"])


def test_tool_result_success_true_is_permissive():
    result = ToolResult(success=True, summary="ok")
    assert result.artifact_ids == []


# ---- query_metrics -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_metrics_writes_evidence_artifact_on_success():
    mcp = FakeMcpClient(
        {
            "seleric.metrics_query": {
                "query_id": "q1",
                "rows": [{"total_sales": "13638"}],
                "provenance": {"query_id": "q1", "cube_view": "commerce_orders"},
                "warnings": [],
            }
        }
    )
    ctx = FakeRunContext(_deps(mcp))
    result = await semantic.query_metrics(
        ctx,
        metric_id="total_sales",
        dimensions={},
        grain="none",
        period_start=datetime(2026, 9, 17, tzinfo=UTC),
        period_end=datetime(2026, 9, 17, tzinfo=UTC),
    )
    assert result.success is True
    assert len(result.artifact_ids) == 1
    artifact = ctx.deps.artifact_store.get(result.artifact_ids[0])
    assert artifact is not None
    assert artifact.payload["value"] == pytest.approx(13638.0)
    assert artifact.classification == "factual"
    assert mcp.calls[0][0] == "seleric.metrics_query"


@pytest.mark.asyncio
async def test_query_metrics_day_grain_writes_one_artifact_per_row():
    mcp = FakeMcpClient(
        {
            "seleric.metrics_query": {
                "query_id": "q1",
                "rows": [
                    {"total_sales.day": "2026-09-15", "total_sales": "100"},
                    {"total_sales.day": "2026-09-16", "total_sales": "150"},
                    {"total_sales.day": "2026-09-17", "total_sales": "120"},
                ],
                "provenance": {"query_id": "q1"},
            }
        }
    )
    ctx = FakeRunContext(_deps(mcp))
    result = await semantic.query_metrics(
        ctx,
        metric_id="total_sales",
        dimensions={},
        grain="day",
        period_start=datetime(2026, 9, 15, tzinfo=UTC),
        period_end=datetime(2026, 9, 17, tzinfo=UTC),
    )
    assert result.success is True
    assert len(result.artifact_ids) == 3
    values = sorted(
        ctx.deps.artifact_store.get(aid).payload["value"] for aid in result.artifact_ids
    )
    assert values == [100.0, 120.0, 150.0]
    first = ctx.deps.artifact_store.get(result.artifact_ids[0])
    assert first.payload["grain"] == "day"
    assert first.payload["period_start"] == first.payload["period_end"]


@pytest.mark.asyncio
async def test_query_metrics_no_rows_returns_insufficient_evidence():
    mcp = FakeMcpClient({"seleric.metrics_query": {"rows": [], "provenance": {}}})
    ctx = FakeRunContext(_deps(mcp))
    result = await semantic.query_metrics(
        ctx,
        metric_id="total_sales",
        dimensions={},
        grain="none",
        period_start=datetime(2026, 9, 17, tzinfo=UTC),
        period_end=datetime(2026, 9, 17, tzinfo=UTC),
    )
    assert result.success is False
    assert result.error_code == "INSUFFICIENT_EVIDENCE"
    assert result.artifact_ids == []


@pytest.mark.asyncio
async def test_query_metrics_mcp_unavailable_is_retryable():
    # call_metrics_query() (services/mcp_query.py, reused as-is) catches the
    # client exception itself and returns an {"error": ...} dict rather than
    # raising — query_metrics() surfaces that as INSUFFICIENT_EVIDENCE with
    # retryable=True, not a raised exception.
    mcp = FakeMcpClient({"seleric.metrics_query": ConnectionError("boom")})
    ctx = FakeRunContext(_deps(mcp))
    result = await semantic.query_metrics(
        ctx,
        metric_id="total_sales",
        dimensions={},
        grain="none",
        period_start=datetime(2026, 9, 17, tzinfo=UTC),
        period_end=datetime(2026, 9, 17, tzinfo=UTC),
    )
    assert result.success is False
    assert result.error_code == "INSUFFICIENT_EVIDENCE"
    assert result.retryable is True


@pytest.mark.asyncio
async def test_query_metrics_drops_invented_brand_placeholder():
    mcp = FakeMcpClient(
        {
            "seleric.metrics_query": {
                "query_id": "q1",
                "rows": [{"total_sales": "13638"}],
                "provenance": {},
            }
        }
    )
    ctx = FakeRunContext(_deps(mcp))
    result = await semantic.query_metrics(
        ctx,
        metric_id="total_sales",
        dimensions={"brand": "some_brand"},
        grain="none",
        period_start=datetime(2026, 9, 17, tzinfo=UTC),
        period_end=datetime(2026, 9, 17, tzinfo=UTC),
    )
    assert result.success is True
    assert "filters" not in mcp.calls[0][1]
    assert len(mcp.calls) == 1


@pytest.mark.asyncio
async def test_query_metrics_retries_unfiltered_on_unknown_brand():
    class _BrandGate:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, Any]]] = []

        async def call(self, *, agent_id: str, capability: str, arguments: dict[str, Any]) -> Any:
            del agent_id
            self.calls.append((capability, arguments))
            if arguments.get("filters"):
                return {"error": "unknown brand 'nikee'"}
            return {"query_id": "q1", "rows": [{"total_sales": "200"}], "provenance": {}}

    mcp = _BrandGate()
    ctx = FakeRunContext(_deps(mcp))  # type: ignore[arg-type]
    result = await semantic.query_metrics(
        ctx,
        metric_id="total_sales",
        dimensions={"brand_id": "nikee"},
        grain="none",
        period_start=datetime(2026, 9, 17, tzinfo=UTC),
        period_end=datetime(2026, 9, 17, tzinfo=UTC),
    )
    assert result.success is True
    assert len(mcp.calls) == 2
    assert mcp.calls[0][1]["filters"]
    assert "filters" not in mcp.calls[1][1]


@pytest.mark.asyncio
async def test_query_metrics_defaults_period_to_mission_as_of():
    mcp = FakeMcpClient(
        {
            "seleric.metrics_query": {
                "query_id": "q1",
                "rows": [{"total_sales": "10"}],
                "provenance": {},
            }
        }
    )
    ctx = FakeRunContext(_deps(mcp))
    result = await semantic.query_metrics(ctx, metric_id="total_sales")
    assert result.success is True
    time_range = mcp.calls[0][1]["time_range"]
    assert time_range["start"] == "2026-09-18"
    assert time_range["end"] == "2026-09-18"


# ---- search_semantics / get_metric_definition ---------------------------------------


@pytest.mark.asyncio
async def test_search_semantics_reports_match_count():
    mcp = FakeMcpClient({"seleric.catalogue_search_metrics": {"matches": [{"id": "total_sales"}]}})
    ctx = FakeRunContext(_deps(mcp))
    result = await semantic.search_semantics(ctx, "revenue")
    assert result.success is True
    assert "metric(s) matched" in result.summary
    ids = [m["id"] for m in result.provenance.source_metadata["matches"]]
    assert "total_sales" in ids
    assert "commerce_net_revenue_daily" in ids  # declared alias "revenue"


@pytest.mark.asyncio
async def test_search_semantics_resolves_declared_short_aliases_when_mcp_is_empty():
    mcp = FakeMcpClient({"seleric.catalogue_search_metrics": {"matches": []}})
    ctx = FakeRunContext(_deps(mcp))
    result = await semantic.search_semantics(ctx, "ns")
    assert result.success is True
    ids = [m["id"] for m in result.provenance.source_metadata["matches"]]
    assert "commerce_net_revenue_daily" in ids


@pytest.mark.asyncio
async def test_query_metrics_resolves_declared_alias_to_catalogue_id():
    mcp = FakeMcpClient(
        {
            "seleric.metrics_query": {
                "query_id": "q1",
                "rows": [{"commerce_net_revenue_daily": "71727"}],
                "provenance": {},
            }
        }
    )
    ctx = FakeRunContext(_deps(mcp))
    result = await semantic.query_metrics(ctx, metric_id="ns")
    assert result.success is True
    assert mcp.calls[0][1]["measures"] == ["commerce_net_revenue_daily"]


@pytest.mark.asyncio
async def test_get_metric_definition_missing_metric_is_insufficient_evidence():
    mcp = FakeMcpClient({"seleric.catalogue_get_metric": {"error": "not found"}})
    ctx = FakeRunContext(_deps(mcp))
    result = await semantic.get_metric_definition(ctx, "nonexistent_metric")
    assert result.success is False
    assert result.error_code == "INSUFFICIENT_EVIDENCE"


# ---- drilldown -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drilldown_writes_one_artifact_per_row():
    mcp = FakeMcpClient(
        {
            "seleric.metrics_query": {"query_id": "q1", "rows": [{"total_sales": "100"}], "provenance": {}},
            "seleric.metrics_drilldown": {
                "rows": [
                    {"shipping_region": "KA", "total_sales": "60"},
                    {"shipping_region": "MH", "total_sales": "40"},
                ],
                "provenance": {},
            },
        }
    )
    ctx = FakeRunContext(_deps(mcp))
    result = await semantic.drilldown(
        ctx,
        metric_id="total_sales",
        dimension="shipping_region",
        period_start=datetime(2026, 9, 17, tzinfo=UTC),
        period_end=datetime(2026, 9, 17, tzinfo=UTC),
    )
    assert result.success is True
    assert len(result.artifact_ids) == 2
    drilldown_call = next(c for c in mcp.calls if c[0] == "seleric.metrics_drilldown")
    assert drilldown_call[1]["parent_query_id"] == "q1"
    assert drilldown_call[1]["target_dimensions"] == ["shipping_region"]


@pytest.mark.asyncio
async def test_drilldown_parent_query_failure_is_insufficient_evidence():
    mcp = FakeMcpClient({"seleric.metrics_query": {"error": "boom", "rows": []}})
    ctx = FakeRunContext(_deps(mcp))
    result = await semantic.drilldown(
        ctx,
        metric_id="total_sales",
        dimension="shipping_region",
        period_start=datetime(2026, 9, 17, tzinfo=UTC),
        period_end=datetime(2026, 9, 17, tzinfo=UTC),
    )
    assert result.success is False
    assert result.error_code == "INSUFFICIENT_EVIDENCE"
