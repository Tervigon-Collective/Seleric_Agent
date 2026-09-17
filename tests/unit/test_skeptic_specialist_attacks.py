"""Skeptic attacks_run must list only attacks that actually executed."""

from __future__ import annotations

import pytest

from seleric_swarm.swarm.artifacts import Causal, Strategy
from seleric_swarm.swarm.blackboard import Blackboard
from seleric_swarm.swarm.mission import SwarmMission
from seleric_swarm.swarm.specialists.skeptic import SkepticAgent, _ATTACKS


class _NoStatsProviders:
    stats = None


@pytest.mark.asyncio
async def test_attacks_run_omits_stats_attacks_when_stats_provider_missing():
    blackboard = Blackboard("MS-skeptic-attacks")
    blackboard.post(
        Causal.new(
            mission_id=blackboard.mission_id,
            created_by="diagnostic_agent",
            treatment="spend",
            outcome="cac",
            effect=1.0,
            passed=True,
        )
    )
    blackboard.post(
        Strategy.new(
            mission_id=blackboard.mission_id,
            created_by="strategy_agent",
            options=[{"action": "cut_spend", "mechanism_fit": "high"}],
            recommended=["cut_spend"],
        )
    )
    agent = SkepticAgent(_NoStatsProviders())
    mission = SwarmMission(
        mission_id=blackboard.mission_id,
        query="why is CAC up?",
        time_range={"start": "2026-09-01", "end": "2026-09-07"},
    )

    refs = await agent.run(blackboard, mission)
    art = blackboard.get(refs[0])
    assert art is not None
    stats_attacks = {
        "alternative_explanation",
        "uncontrolled_confounder",
        "seasonality",
        "sample_size",
    }
    attacks_run = art["attacks_run"]
    assert set(attacks_run).isdisjoint(stats_attacks)
    assert set(attacks_run) <= set(_ATTACKS)
    # Non-stats attacks that always run (or evaluate) are present.
    for name in ("baseline_fairness", "attribution_change", "temporal_precedence", "model_reliability", "recommendation_addresses_cause"):
        assert name in attacks_run
    # Skipped stats attacks must be reported, not silently dropped from the audit trail.
    assert set(art["attacks_skipped"]) == stats_attacks
