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

from seleric_swarm.agent.contracts import SelericDeps, ToolResult
from seleric_swarm.agent.contracts import ExecutionLimits
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


# ---- search_semantics / get_metric_definition ---------------------------------------


@pytest.mark.asyncio
async def test_search_semantics_reports_match_count():
    mcp = FakeMcpClient({"seleric.catalogue_search_metrics": {"matches": [{"id": "total_sales"}]}})
    ctx = FakeRunContext(_deps(mcp))
    result = await semantic.search_semantics(ctx, "revenue")
    assert result.success is True
    assert "1 metric(s)" in result.summary


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
