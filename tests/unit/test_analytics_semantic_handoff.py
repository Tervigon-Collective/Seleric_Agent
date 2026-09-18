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

import pytest

from seleric_swarm.agent.artifacts import EvidenceArtifact
from seleric_swarm.agent.dependencies import ExecutionLimits, SelericDeps
from seleric_swarm.conversations.contracts import (
    Artifact,
    ArtifactProvenance,
    ContextBundle,
    Principal,
)
from seleric_swarm.state.artifacts import InMemoryArtifactStore
from seleric_swarm.toolsets import analytics, semantic

_METRIC = "metric.net_sales"
# Cube names a grain time dimension "<view>.<dimension>.<granularity>", and
# services/mcp_query.py::row_date matches on the ".day" suffix. A fixture
# using a bare "report_date" key would silently exercise row_date's None
# fallback instead of the real path — see the module note below.
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


@pytest.mark.asyncio
async def test_day_grain_query_emits_one_artifact_per_day_not_one_per_window():
    """The upstream half of docs/BUG_SHEET.md #14.

    swarm_v2's original bug was one window-aggregate row reaching a
    single-day baseline. The V3 equivalent would be one EvidenceArtifact
    labelled grain="day" but spanning the whole window. B emits one artifact
    per bucket instead, each a single day — so the label and the period
    agree, and C's precondition has nothing to catch.
    """
    store = InMemoryArtifactStore()
    ctx = _ctx(store, _DAILY_ROWS)

    result = await semantic.query_metrics(
        ctx,
        metric_id=_METRIC,
        dimensions={},
        grain="day",
        period_start=datetime(2026, 9, 12, tzinfo=UTC),
        period_end=datetime(2026, 9, 16, tzinfo=UTC),
    )

    assert result.success is True
    assert len(result.artifact_ids) == 5, "one artifact per day, not one for the window"

    payloads = [EvidenceArtifact.model_validate(store.get(aid).payload) for aid in result.artifact_ids]
    assert [p.value for p in payloads] == [4100.0, 4050.0, 4120.0, 4080.0, 900.0]
    for payload in payloads:
        assert payload.grain == "day"
        assert payload.period_start == payload.period_end, "a day-grain artifact must span exactly one day"


@pytest.mark.asyncio
async def test_query_then_detect_finds_the_real_drop_end_to_end():
    """The whole point of the seam: B fetches, C scores, and the 900.0 drop
    is found against its own daily history — no normalization anywhere in
    the path, which is what #14 was ultimately about."""
    store = InMemoryArtifactStore()
    ctx = _ctx(store, _DAILY_ROWS)
    fetched = await semantic.query_metrics(
        ctx,
        metric_id=_METRIC,
        dimensions={},
        grain="day",
        period_start=datetime(2026, 9, 12, tzinfo=UTC),
        period_end=datetime(2026, 9, 16, tzinfo=UTC),
    )

    result = await analytics.detect_anomalies(ctx, fetched.artifact_ids)

    assert result.success is True, result.summary
    assert len(result.artifact_ids) == 1
    metrics = store.get(result.artifact_ids[0]).payload["metrics"]
    assert metrics["observed"] == 900.0
    assert metrics["expected"] == pytest.approx(4090.0)
    assert metrics["deviation_pct"] < 0, "a drop must read as a decline, not a spike"


@pytest.mark.asyncio
async def test_unparseable_date_column_degrades_to_window_labels_and_is_caught():
    """A latent upstream hazard, found while building the fixture above —
    routed to Profile B, defended against here.

    ``services/mcp_query.py::row_date`` identifies a bucket by matching a
    ``.day``-suffixed column. If Cube ever returns a day-grain series whose
    date column doesn't match (renamed view, different granularity suffix,
    an aggregate row mixed in), ``query_metrics`` falls back to
    ``period_start``/``period_end`` for *every* row — emitting N artifacts
    that each claim grain="day" while spanning the whole window. That is
    docs/BUG_SHEET.md #14's shape multiplied by N, and B's own success path
    reports success.

    C's precondition is what stops it becoming a finding. This test pins
    that, so the seam has a guard even when the upstream heuristic misses.
    """
    store = InMemoryArtifactStore()
    unparseable = [{_METRIC: row[_METRIC], "report_date": row[_DATE_KEY]} for row in _DAILY_ROWS]
    ctx = _ctx(store, unparseable)

    fetched = await semantic.query_metrics(
        ctx,
        metric_id=_METRIC,
        dimensions={},
        grain="day",
        period_start=datetime(2026, 9, 12, tzinfo=UTC),
        period_end=datetime(2026, 9, 16, tzinfo=UTC),
    )

    assert fetched.success is True, "upstream reports success — that's the hazard"
    spans = {
        (p.period_start, p.period_end)
        for p in (EvidenceArtifact.model_validate(store.get(aid).payload) for aid in fetched.artifact_ids)
    }
    assert spans == {(datetime(2026, 9, 12, tzinfo=UTC), datetime(2026, 9, 16, tzinfo=UTC))}

    result = await analytics.detect_anomalies(ctx, fetched.artifact_ids)

    assert result.success is False
    assert result.error_code == "EVIDENCE_GRAIN_MISMATCH"


@pytest.mark.asyncio
async def test_analytics_refuses_a_daily_label_on_a_window_aggregate():
    """C's defensive half, independent of B behaving.

    Hand-built rather than fetched: this is the artifact B must never emit.
    If a future change upstream (or a third writer of evidence) produces
    one, C refuses it instead of scoring a 5-day sum against a daily median.
    """
    store = InMemoryArtifactStore()
    evidence = EvidenceArtifact(
        metric_id=_METRIC,
        grain="day",
        as_of=datetime(2026, 9, 18, tzinfo=UTC),
        period_start=datetime(2026, 9, 12, tzinfo=UTC),
        period_end=datetime(2026, 9, 16, tzinfo=UTC),
        value=sum(row[_METRIC] for row in _DAILY_ROWS),
        source_query={"measure": _METRIC},
    )
    artifact = store.put(
        Artifact(
            workspace_id="ws1",
            artifact_type="evidence",
            payload=evidence.model_dump(mode="json"),
            classification="factual",
            evidence_ids=["raw:aggregate"],
            provenance=ArtifactProvenance(query_version="q1"),
            mission_id="mission-1",
        )
    )

    result = await analytics.detect_anomalies(_ctx(store, []), [artifact.id])

    assert result.success is False
    assert result.error_code == "EVIDENCE_GRAIN_MISMATCH"
    assert result.artifact_ids == []
