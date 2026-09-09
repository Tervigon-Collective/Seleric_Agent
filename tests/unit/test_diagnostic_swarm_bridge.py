"""Unit tests for agents/diagnostic/swarm_bridge.py (gap-fix coverage).

Tests cover:
 1. _fetch_observations extends the mission time range backwards so DoWhy
    always receives at least CAUSAL_EXTRA_HISTORY_DAYS of pre-treatment data
    — even for short user queries (e.g. "last 7 days").
 2. The extended window preserves the original end date unchanged.
 3. A malformed start date falls back gracefully (same behaviour as before).
 4. Leadership transfer wiring: when DiagnosticAgent.diagnose() recommends a
    transfer, SwarmDiagnosticSpecialist.run() calls apply_transfer and
    blackboard.mission_lead is updated.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from seleric_swarm.agents.diagnostic.swarm_bridge import (
    _CAUSAL_EXTRA_HISTORY_DAYS,
    _fetch_observations,
)
from seleric_swarm.swarm.blackboard import Blackboard


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeProviders:
    """Minimal provider bundle that captures the time_range passed to fetch_series."""

    def __init__(self, return_frame=None):
        self._captured: dict[str, Any] = {}
        self._return = return_frame
        self.data = {"performance": self}  # acts as both bundle and provider

    async def fetch_series(self, *, metric_ids: list[str], time_range: dict[str, Any]):
        self._captured = dict(time_range)
        return self._return

    @property
    def captured_time_range(self) -> dict[str, Any]:
        return self._captured


# ---------------------------------------------------------------------------
# _fetch_observations — causal window extension
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_observations_extends_start_by_30_days():
    """The causal observation window must be extended back by _CAUSAL_EXTRA_HISTORY_DAYS."""
    providers = _FakeProviders(return_frame=None)
    mission_start = "2026-09-01"
    mission_end = "2026-09-07"

    await _fetch_observations(
        providers,
        "session_conversion_rate",
        {"start": mission_start, "end": mission_end},
    )

    expected_start = (
        date.fromisoformat(mission_start) - timedelta(days=_CAUSAL_EXTRA_HISTORY_DAYS)
    ).isoformat()
    assert providers.captured_time_range["start"] == expected_start, (
        f"Expected start {expected_start!r}, got {providers.captured_time_range['start']!r}"
    )


@pytest.mark.asyncio
async def test_fetch_observations_preserves_end_date():
    """The end date must not change — only the start is extended."""
    providers = _FakeProviders(return_frame=None)
    mission_end = "2026-09-07"

    await _fetch_observations(
        providers,
        "session_conversion_rate",
        {"start": "2026-09-01", "end": mission_end},
    )

    assert providers.captured_time_range["end"] == mission_end


@pytest.mark.asyncio
async def test_fetch_observations_custom_extra_history_days():
    """extra_history_days kwarg is honoured when the caller overrides the default."""
    providers = _FakeProviders(return_frame=None)
    mission_start = "2026-08-01"

    await _fetch_observations(
        providers,
        "session_conversion_rate",
        {"start": mission_start, "end": "2026-08-07"},
        extra_history_days=14,
    )

    expected_start = (
        date.fromisoformat(mission_start) - timedelta(days=14)
    ).isoformat()
    assert providers.captured_time_range["start"] == expected_start


@pytest.mark.asyncio
async def test_fetch_observations_malformed_start_falls_back_gracefully():
    """A malformed start date must not raise — falls back to the original value."""
    providers = _FakeProviders(return_frame=None)

    # Should not raise; captured start is the raw (malformed) string
    await _fetch_observations(
        providers,
        "session_conversion_rate",
        {"start": "NOT-A-DATE", "end": "2026-09-07"},
    )
    assert providers.captured_time_range.get("start") == "NOT-A-DATE"
    assert providers.captured_time_range.get("end") == "2026-09-07"


@pytest.mark.asyncio
async def test_fetch_observations_none_providers_returns_none():
    result = await _fetch_observations(None, "session_conversion_rate", {"start": "2026-09-01", "end": "2026-09-07"})
    assert result is None


@pytest.mark.asyncio
async def test_fetch_observations_empty_outcome_metric_returns_none():
    result = await _fetch_observations(_FakeProviders(), "", {"start": "2026-09-01", "end": "2026-09-07"})
    assert result is None


# ---------------------------------------------------------------------------
# Leadership transfer wiring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_leadership_transfer_updates_blackboard_mission_lead():
    """When DiagnosticAgent.diagnose() recommends a transfer and LeadershipController
    accepts it, blackboard.mission_lead must be updated to the recommended agent."""
    from seleric_swarm.agents.diagnostic.contracts import DiagnosticResult
    from seleric_swarm.agents.diagnostic.swarm_bridge import SwarmDiagnosticSpecialist
    from seleric_swarm.coordinator.leadership.frontier import LeadershipController
    from seleric_swarm.leadership.manager import LeadershipManager
    from seleric_swarm.swarm.mission import SwarmMission

    mission = SwarmMission(
        mission_id="MS-lt-unit",
        query="Why did conversion drop?",
        time_range={"start": "2026-09-01", "end": "2026-09-07"},
        intents={"diagnostic"},
        context={"primary_metric": "session_conversion_rate"},
    )
    blackboard = Blackboard("MS-lt-unit")
    blackboard.mission_lead = "funnel_agent"

    from seleric_swarm.agents.diagnostic.contracts import DiagnosticHypothesis

    # Minimal DiagnosticResult that recommends a leadership transfer.
    # Must include at least one hypothesis so _write_artifacts produces a non-empty
    # `posted` list — LeadershipController requires evidence_refs when
    # require_new_evidence=True (the default policy).
    fake_hyp = DiagnosticHypothesis(
        statement="Paid CPM surge reduced purchase CVR",
        treatment_metric="metric.cpm",
        outcome_metric="session_conversion_rate",
        domains=["performance"],
        status="testing",
    )
    fake_result = DiagnosticResult(
        mission_id="MS-lt-unit",
        question=mission.query,
        outcome_metric="session_conversion_rate",
        hypotheses=[fake_hyp],
        leadership_transfer_recommended=True,
        recommended_domain_lead="performance_agent",
        leadership_transfer_reason="Paid traffic quality drove the drop",
    )

    leadership = LeadershipController(LeadershipManager())
    specialist = SwarmDiagnosticSpecialist(leadership=leadership)

    with (
        patch(
            "seleric_swarm.agents.diagnostic.swarm_bridge.DiagnosticAgent.diagnose",
            new=AsyncMock(return_value=fake_result),
        ),
        patch(
            "seleric_swarm.agents.diagnostic.swarm_bridge._fetch_observations",
            new=AsyncMock(return_value=None),
        ),
    ):
        await specialist.run(blackboard, mission)

    assert blackboard.mission_lead == "performance_agent", (
        f"Expected 'performance_agent', got {blackboard.mission_lead!r}"
    )
    transfer_events = [e for e in blackboard.events if e["kind"] == "leadership_transfer"]
    assert len(transfer_events) == 1
    assert transfer_events[0]["to_agent"] == "performance_agent"


@pytest.mark.asyncio
async def test_leadership_transfer_without_controller_only_records_event():
    """Without a LeadershipController, the transfer recommendation is recorded as an
    event but blackboard.mission_lead stays unchanged (safe degradation)."""
    from seleric_swarm.agents.diagnostic.contracts import DiagnosticResult
    from seleric_swarm.agents.diagnostic.swarm_bridge import SwarmDiagnosticSpecialist
    from seleric_swarm.swarm.mission import SwarmMission

    mission = SwarmMission(
        mission_id="MS-lt-no-ctrl",
        query="Why did conversion drop?",
        time_range={"start": "2026-09-01", "end": "2026-09-07"},
        intents={"diagnostic"},
        context={"primary_metric": "session_conversion_rate"},
    )
    blackboard = Blackboard("MS-lt-no-ctrl")
    blackboard.mission_lead = "funnel_agent"

    fake_result = DiagnosticResult(
        mission_id="MS-lt-no-ctrl",
        question=mission.query,
        outcome_metric="session_conversion_rate",
        leadership_transfer_recommended=True,
        recommended_domain_lead="performance_agent",
    )

    # No leadership controller passed — the bridge must degrade gracefully
    specialist = SwarmDiagnosticSpecialist(leadership=None)

    with (
        patch(
            "seleric_swarm.agents.diagnostic.swarm_bridge.DiagnosticAgent.diagnose",
            new=AsyncMock(return_value=fake_result),
        ),
        patch(
            "seleric_swarm.agents.diagnostic.swarm_bridge._fetch_observations",
            new=AsyncMock(return_value=None),
        ),
    ):
        await specialist.run(blackboard, mission)

    # Lead must be unchanged — the controller was absent so transfer didn't fire
    assert blackboard.mission_lead == "funnel_agent"
    # But the recommendation event must still be recorded for observability
    rec_events = [e for e in blackboard.events if e["kind"] == "leadership_transfer_recommended"]
    assert len(rec_events) == 1
