"""swarm_v2 budget hard-stop tests."""

from __future__ import annotations

import pytest

from seleric_swarm.coordinator.contracts import MissionBudget
from seleric_swarm.coordinator.governance.budget import check_swarm_budget
from seleric_swarm.coordinator.governance.completion_gate import decide_completion
from seleric_swarm.coordinator.graph import run_swarm_v2_mission


def test_check_swarm_budget_agent_calls():
    budgets = MissionBudget(max_agent_calls=4)
    assert check_swarm_budget({"usage": {"agent_calls": 2}}, budgets).ok
    assert not check_swarm_budget({"usage": {"agent_calls": 4}}, budgets).ok
    assert not check_swarm_budget(
        {"usage": {"agent_calls": 3}}, budgets, agent_calls_needed=2
    ).ok
    v = check_swarm_budget({"usage": {"agent_calls": 4}}, budgets)
    assert v.exhausted_key == "agent_calls"
    assert v.error_code == "BUDGET_EXCEEDED"


def test_check_swarm_budget_leadership_and_remediation():
    budgets = MissionBudget(max_leadership_transfers=2, max_remediation_rounds=1)
    assert not check_swarm_budget(
        {"handoff_history": [{"epoch": 1}, {"epoch": 2}], "usage": {}},
        budgets,
    ).ok
    assert not check_swarm_budget({"remediation_round": 1, "usage": {}}, budgets).ok


def test_completion_gate_leadership_budget_key():
    decision = decide_completion(
        {
            "objectives": [
                {"objective_id": "O1", "status": "satisfied"},
                {"objective_id": "O2", "status": "unresolved"},
            ],
            "validated_claim_refs": [],
            "challenged_claim_refs": [],
            "rejected_claim_refs": [],
            "evidence_gaps": [],
            "conflicts": [],
            "tasks": [],
            "budgets": {"max_leadership_transfers": 1},
            "usage": {"leadership_transfers": 1},
            "handoff_history": [{"epoch": 1}],
            "evidence": [{"x": 1}],
            "claims": [{"gate_status": "passed"}],
            "status": "partial",
        }
    )
    assert decision.status == "partial"
    assert any("leadership_transfers" in r for r in decision.reasons)


@pytest.mark.asyncio
async def test_swarm_v2_budget_exhaustion_emits_event_and_partial(runtime):
    result = await run_swarm_v2_mission(
        runtime,
        query="Why has CAC increased?",
        timezone="Asia/Kolkata",
        as_of="2026-08-01",
        budget_overrides={"max_agent_calls": 2},
    )
    assert result.status == "partial"
    kinds = [e.get("kind") for e in result.events]
    assert "mission_budget_exhausted" in kinds
    assert any("Agent call budget" in lim for lim in (result.limitations or []))
    control = next(e for e in result.events if e.get("kind") == "mission_control_plane")
    assert control.get("budget_exhausted") is True
    assert int((control.get("usage") or {}).get("agent_calls") or 0) >= 2
