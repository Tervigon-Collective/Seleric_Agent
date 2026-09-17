"""Unit tests for swarm/specialists/anomaly.py (gap-fix coverage).

1. A baseline-less evidence row must still reach the detector (robust_zscore
   doesn't need a baseline; only the template detector does, and it already
   skips baseline-less readings itself).
2. force_robust_zscore is set in detector context only for diagnostic /
   executive_health missions -- lookup/overview never sets it.
"""

from __future__ import annotations

from typing import Any

import pytest

from seleric_swarm.swarm.artifacts import Evidence
from seleric_swarm.swarm.blackboard import Blackboard
from seleric_swarm.swarm.domain.base import DomainAgent, DomainConfig
from seleric_swarm.swarm.mission import SwarmMission
from seleric_swarm.swarm.providers.base import AnomalyFinding, DataResult, MetricReading
from seleric_swarm.swarm.specialists.anomaly import AnomalyAgent
from seleric_swarm.swarm.specialists.observer import ObserverAgent


class _FakeProviders:
    def __init__(self, detector: Any) -> None:
        self.anomaly = detector


class _CapturingDetector:
    def __init__(self) -> None:
        self.readings: list[MetricReading] = []
        self.context: dict[str, Any] = {}

    async def detect(self, readings: list[MetricReading], *, context: dict[str, Any]) -> list[AnomalyFinding]:
        self.readings = list(readings)
        self.context = dict(context)
        return []


def _post_evidence(blackboard: Blackboard, *, metric: str, value: float, baseline: float | None) -> None:
    blackboard.post(
        Evidence.new(
            mission_id=blackboard.mission_id,
            created_by="observer_agent",
            metric_or_fact=metric,
            value=value,
            baseline=baseline,
        )
    )


@pytest.mark.asyncio
async def test_baseline_less_reading_still_reaches_detector():
    blackboard = Blackboard("MS-anomaly-nobaseline")
    _post_evidence(blackboard, metric="metric.spend", value=100.0, baseline=None)
    detector = _CapturingDetector()
    agent = AnomalyAgent(_FakeProviders(detector))
    mission = SwarmMission(
        mission_id="MS-anomaly-nobaseline",
        query="What is spend?",
        time_range={"start": "2026-09-01", "end": "2026-09-07"},
    )

    await agent.run(blackboard, mission)

    assert len(detector.readings) == 1
    assert detector.readings[0].metric_id == "metric.spend"
    assert detector.readings[0].baseline is None
    assert detector.readings[0].value == 100.0


@pytest.mark.asyncio
async def test_multi_day_evidence_is_normalized_to_daily_average():
    """docs/BUG_SHEET.md #14: Observer answers "sales over the last 5 days"
    with one summed total (no daily-granularity fetch path), which used to
    reach the detector as a raw sum -- comparing a 5-day sum against a
    single-day expected_range band produced a false "+208% spike" for a
    metric that was actually declining. A multi-day reading must be
    normalized to a per-day average before the detector ever sees it."""
    blackboard = Blackboard("MS-anomaly-multiday")
    blackboard.post(
        Evidence.new(
            mission_id=blackboard.mission_id,
            created_by="observer_agent",
            metric_or_fact="metric.net_sales",
            value=251328.21,
            time_range={"start": "2026-09-12", "end": "2026-09-16"},  # 5 days
        )
    )
    detector = _CapturingDetector()
    agent = AnomalyAgent(_FakeProviders(detector))
    mission = SwarmMission(
        mission_id="MS-anomaly-multiday",
        query="why did sales drop from 5 days",
        time_range={"start": "2026-09-12", "end": "2026-09-16"},
    )

    await agent.run(blackboard, mission)

    assert len(detector.readings) == 1
    assert detector.readings[0].value == pytest.approx(251328.21 / 5)


@pytest.mark.asyncio
async def test_single_day_evidence_is_not_normalized():
    """Same-day time_range (start == end) must pass through unchanged --
    only genuinely multi-day windows get divided."""
    blackboard = Blackboard("MS-anomaly-singleday")
    blackboard.post(
        Evidence.new(
            mission_id=blackboard.mission_id,
            created_by="observer_agent",
            metric_or_fact="metric.net_sales",
            value=4136.66,
            time_range={"start": "2026-09-15", "end": "2026-09-15"},
        )
    )
    detector = _CapturingDetector()
    agent = AnomalyAgent(_FakeProviders(detector))
    mission = SwarmMission(
        mission_id="MS-anomaly-singleday",
        query="What was net sales yesterday?",
        time_range={"start": "2026-09-15", "end": "2026-09-15"},
    )

    await agent.run(blackboard, mission)

    assert detector.readings[0].value == 4136.66


@pytest.mark.asyncio
async def test_observer_per_day_evidence_reaches_anomaly_unnormalized():
    """docs/BUG_SHEET.md #14, end to end (Phase 3+4): once Observer fetches
    a real per-day series (granularity="day"), each day's Evidence row has
    start==end and must reach AnomalyAgent as its own, un-normalized
    reading -- not summed then divided by 5. The sum/normalize path in
    anomaly.py is a fallback for missions that still return one aggregate,
    not the path real daily figures take."""

    class _DailyRec:
        domain = "commerce"

        def __init__(self) -> None:
            self.calls = 0

        async def fetch(self, *, metric_ids, time_range, dimensions=None, limit=None, sort=None):
            self.calls += 1
            # A declining series -- if this were summed and re-divided by 5
            # instead of reaching the detector per-day, the distinct daily
            # values here would be lost.
            value = 1000.0 - (self.calls * 50.0)
            return DataResult(readings=[MetricReading(metric_id=metric_ids[0], value=value, data_origin="MCP")], events=[], missing=[])

        async def events(self, *, time_range):
            return []

    rec = _DailyRec()
    cfg = DomainConfig(agent_id="commerce_agent", domain="commerce", owned_metrics=["metric.net_sales"], probe_metrics=["metric.net_sales"])
    domain_agent = DomainAgent(cfg, data_provider=rec)
    mission = SwarmMission(
        mission_id="MS-e2e-daily",
        query="why did net sales drop over the last 5 days",
        time_range={"start": "2026-09-12", "end": "2026-09-16"},
        context={
            "domain_questions": [{"domain": "commerce", "metrics": ["metric.net_sales"], "grain": []}],
            "granularity": "day",
        },
        intents={"diagnostic"},
    )
    board = Blackboard("MS-e2e-daily")
    board.mission_lead = "commerce_agent"
    observer = ObserverAgent(providers=_FakeProviders(None), domains={"commerce_agent": domain_agent})
    await observer.run(board, mission)

    detector = _CapturingDetector()
    anomaly_agent = AnomalyAgent(_FakeProviders(detector))
    await anomaly_agent.run(board, mission)

    assert len(detector.readings) == 5
    assert [r.value for r in detector.readings] == [950.0, 900.0, 850.0, 800.0, 750.0]


@pytest.mark.asyncio
async def test_force_robust_zscore_set_only_for_diagnostic_missions():
    blackboard = Blackboard("MS-anomaly-force")
    _post_evidence(blackboard, metric="metric.net_sales", value=100.0, baseline=90.0)
    detector = _CapturingDetector()
    agent = AnomalyAgent(_FakeProviders(detector))

    lookup_mission = SwarmMission(
        mission_id="MS-anomaly-force",
        query="What was net sales?",
        time_range={"start": "2026-09-01", "end": "2026-09-07"},
        intents=set(),
    )
    await agent.run(blackboard, lookup_mission)
    assert "force_robust_zscore" not in detector.context

    why_mission = SwarmMission(
        mission_id="MS-anomaly-force",
        query="Why did net sales change?",
        time_range={"start": "2026-09-01", "end": "2026-09-07"},
        intents={"diagnostic"},
    )
    await agent.run(blackboard, why_mission)
    assert detector.context["force_robust_zscore"] is True
