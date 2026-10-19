"""Cross-profile seam: what Profile B's query_metrics hands Profile C.

Analytics tools consume evidence they did not fetch (non-negotiable rule 5),
so ``detect_anomalies`` is only as correct as ``toolsets/semantic.py``'s
labelling of that evidence. The two profiles are developed independently
against a frozen contract, which makes this seam exactly the place a
``docs/BUG_SHEET.md`` #14-shaped defect could reappear without either side's
own unit tests noticing. These tests pin it end to end rather than assuming.

The specific hazard: one ``EvidenceArtifact`` carrying ``grain="day"`` but a
multi-day ``period_start..period_end`` is a window aggregate wearing a daily
label — #14's exact shape. B avoids producing one by emitting a separate
artifact per Cube bucket; C refuses one if it ever arrives anyway. Both
halves are asserted below, because either alone is a single point of
failure.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from seleric_swarm.agent.dependencies import ExecutionLimits, SelericDeps
from seleric_swarm.conversations.contracts import (
    ContextBundle,
    Principal,
)
from seleric_swarm.state.artifacts import InMemoryArtifactStore

_METRIC = "metric.net_sales"
# Cube names a grain time dimension "<view>.<dimension>.<granularity>".
# services/mcp_query.py::row_date matches that suffix (.day, .week, .month, …).
# A fixture using a bare "report_date" key would silently exercise row_date's
# None fallback instead of the real path — see the module note below.
_DATE_KEY = "commerce.report_date.day"
_DAILY_ROWS = [
    {_METRIC: 4100.0, _DATE_KEY: "2026-09-12T00:00:00.000"},
    {_METRIC: 4050.0, _DATE_KEY: "2026-09-13T00:00:00.000"},
    {_METRIC: 4120.0, _DATE_KEY: "2026-09-14T00:00:00.000"},
    {_METRIC: 4080.0, _DATE_KEY: "2026-09-15T00:00:00.000"},
    {_METRIC: 900.0, _DATE_KEY: "2026-09-16T00:00:00.000"},  # the drop the mission is about
]


class FakeMcpClient:
    """Answers the way Cube does for grain=day: one row per bucket."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    async def call(self, *, agent_id: str, capability: str, arguments: dict[str, Any]) -> Any:
        return {"rows": self.rows, "provenance": {"query_id": "q-123"}, "query_id": "q-123"}


class FakeRunContext:
    def __init__(self, deps: SelericDeps) -> None:
        self.deps = deps


def _ctx(store: InMemoryArtifactStore, rows: list[dict[str, Any]]) -> FakeRunContext:
    return FakeRunContext(
        SelericDeps(
            mission_id="mission-1",
            as_of=datetime(2026, 9, 18, tzinfo=UTC),
            principal=Principal(principal_id="p1", workspace_id="ws1", user_id="u1"),
            thread_id="thread-1",
            run_id="run-1",
            trace_id="trace-1",
            context=ContextBundle(),
            mcp_client=FakeMcpClient(rows),
            artifact_store=store,
            limits=ExecutionLimits(),
        )
    )








