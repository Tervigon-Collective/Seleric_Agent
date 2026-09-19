"""Sprint 3 (Profile B) exit-criteria regression tests: bug #8 and bug #2
against the NEW consolidated fetch path (`toolsets/semantic.py`).

Both bugs already have regression tests against the OLD swarm_v2 path:
- Bug #8: previously also covered against the OLD swarm_v2 path directly
  (`coordinator/catalogue_grounding.py::dimensions_in_query()`), now removed
  along with that heuristic.
- Bug #2: `tests/skeptic/test_skeptic_agent.py::
  test_03b_alias_spellings_of_one_metric_are_still_compared` — tests
  `MetricRegistry`/`SkepticAgent` canonicalization, still valid until
  `MetricRegistry` retires in Sprint 5.

Neither proves the new path (`toolsets/semantic.py`) is immune to the same
bug class. Profile B's own Exit Criteria 2 and 3
(docs/refactor/02_PROFILE_SEMANTIC_MCP.md) require that proof — this file
is it. Fast unit-level tests only (no live network); the live cross-path
parity check is exit-criterion 1, covered by the gated characterization
suite.
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
        response = self.responses.get(capability)
        if isinstance(response, Exception):
            raise response
        return response


class ByMeasureMcpClient:
    """Routes seleric.metrics_query by the requested `measures[0]` — needed
    to prove two id spellings each reach Cube with their own literal value,
    not a canonicalized/collapsed one."""

    def __init__(self, rows_by_measure: dict[str, dict[str, Any]]) -> None:
        self.rows_by_measure = rows_by_measure
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, *, agent_id: str, capability: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((capability, arguments))
        measure = arguments["measures"][0]
        row = self.rows_by_measure[measure]
        return {"query_id": "q1", "rows": [row], "provenance": {"query_id": "q1"}}


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


# ---- Bug #8: dimension misclassification ("get per day data" -> session_day_of_week) ----


@pytest.mark.asyncio
async def test_dimensions_are_passed_through_verbatim_no_keyword_matching():
    """No dimensions_in_query()-style rewriting exists anywhere in the new
    call path — whatever dimension key the LLM states reaches Cube exactly
    as given."""
    mcp = FakeMcpClient(
        {"seleric.metrics_query": {"rows": [{"net_sales": "100"}], "provenance": {}}}
    )
    ctx = FakeRunContext(_deps(mcp))
    await semantic.query_metrics(
        ctx,
        metric_id="net_sales",
        dimensions={"session_day_of_week": ""},
        grain="none",
        period_start=datetime(2026, 9, 15, tzinfo=UTC),
        period_end=datetime(2026, 9, 15, tzinfo=UTC),
    )
    query_call = next(c for c in mcp.calls if c[0] == "seleric.metrics_query")
    assert query_call[1]["dimensions"] == ["session_day_of_week"]


@pytest.mark.asyncio
async def test_unsupported_dimension_surfaces_mcp_rejection_not_silent_empty_success():
    """Bug #8's failure mode was a silent empty-evidence cascade three
    layers before Cube ever saw the query. The new path has one call site,
    so an unsupported dimension surfaces as a visible ToolResult failure."""
    mcp = FakeMcpClient(
        {
            "seleric.metrics_query": {
                "error": "dimension not supported for measure",
                "rows": [],
            }
        }
    )
    ctx = FakeRunContext(_deps(mcp))
    result = await semantic.query_metrics(
        ctx,
        metric_id="net_sales",
        dimensions={"session_day_of_week": ""},
        grain="none",
        period_start=datetime(2026, 9, 15, tzinfo=UTC),
        period_end=datetime(2026, 9, 15, tzinfo=UTC),
    )
    assert result.success is False
    assert result.error_code == "INSUFFICIENT_EVIDENCE"
    assert result.retryable is True


@pytest.mark.asyncio
async def test_per_day_phrasing_resolves_to_day_grain_not_a_dimension():
    """The original bug's literal repro: "get per day data" was routed to a
    session_day_of_week DIMENSION instead of a grain="day" AGGREGATION.
    Grain and dimension are now structurally separate parameters, so a
    calendar word can't leak into dimension-matching — there's no
    dimension-matching step to leak into."""
    mcp = FakeMcpClient(
        {
            "seleric.metrics_query": {
                "rows": [{"net_sales.day": "2026-09-15", "net_sales": "100"}],
                "provenance": {},
            }
        }
    )
    ctx = FakeRunContext(_deps(mcp))
    await semantic.query_metrics(
        ctx,
        metric_id="net_sales",
        dimensions={},
        grain="day",
        period_start=datetime(2026, 9, 15, tzinfo=UTC),
        period_end=datetime(2026, 9, 15, tzinfo=UTC),
    )
    query_call = next(c for c in mcp.calls if c[0] == "seleric.metrics_query")
    assert query_call[1]["granularity"] == "day"
    assert query_call[1].get("dimensions") is None


# ---- Bug #2: metric-ID canonicalization inconsistency ----


@pytest.mark.asyncio
async def test_two_spellings_of_same_metric_each_produce_their_own_artifact_no_silent_remap():
    """The old bug: lookup_fast_path.py::_canon() called MetricRegistry.get()
    directly, which could return a different id depending on catalogue
    warmth, so two spellings of the same metric silently diverged or
    collapsed downstream. The new path has no canonicalization step:
    metric_id is used as the literal Cube `measures` value, and each
    artifact records exactly the id it was fetched with."""
    mcp = ByMeasureMcpClient(
        {
            "cac": {"cac": "1500"},
            "metric.cac": {"metric.cac": "1500"},
        }
    )
    ctx = FakeRunContext(_deps(mcp))

    result_a = await semantic.query_metrics(
        ctx,
        metric_id="cac",
        dimensions={},
        grain="none",
        period_start=datetime(2026, 9, 15, tzinfo=UTC),
        period_end=datetime(2026, 9, 15, tzinfo=UTC),
    )
    result_b = await semantic.query_metrics(
        ctx,
        metric_id="metric.cac",
        dimensions={},
        grain="none",
        period_start=datetime(2026, 9, 15, tzinfo=UTC),
        period_end=datetime(2026, 9, 15, tzinfo=UTC),
    )

    assert result_a.success is True
    assert result_b.success is True
    measures_sent = [c[1]["measures"][0] for c in mcp.calls if c[0] == "seleric.metrics_query"]
    assert measures_sent == ["cac", "metric.cac"]

    artifact_a = ctx.deps.artifact_store.get(result_a.artifact_ids[0])
    artifact_b = ctx.deps.artifact_store.get(result_b.artifact_ids[0])
    assert artifact_a.payload["metric_id"] == "cac"
    assert artifact_b.payload["metric_id"] == "metric.cac"


def test_metric_id_never_resolved_through_metric_registry():
    """Structural guard against reintroducing bug #2's dependency: the
    semantic toolset module must never reference MetricRegistry."""
    assert "MetricRegistry" not in dir(semantic)
    assert not any("MetricRegistry" in str(getattr(semantic, name)) for name in dir(semantic))
