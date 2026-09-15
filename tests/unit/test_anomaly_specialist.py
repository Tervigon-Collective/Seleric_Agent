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
from seleric_swarm.swarm.mission import SwarmMission
from seleric_swarm.swarm.providers.base import AnomalyFinding, MetricReading
from seleric_swarm.swarm.specialists.anomaly import AnomalyAgent


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
