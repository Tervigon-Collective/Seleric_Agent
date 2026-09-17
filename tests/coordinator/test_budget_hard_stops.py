"""swarm_v2 budget hard-stop tests.

Budget enforcement was removed system-wide per explicit request -- see
docs/TASK_SHEET.md's "Re-enable budget/hard-stop enforcement" backlog item
(low priority). ``check_swarm_budget`` is now a no-op; these tests assert
that no-op behavior instead of the old enforcement semantics.
"""

from __future__ import annotations

import pytest

from seleric_swarm.coordinator.contracts import MissionBudget
from seleric_swarm.coordinator.governance.budget import check_swarm_budget
from seleric_swarm.coordinator.governance.completion_gate import decide_completion
from seleric_swarm.coordinator.graph import run_swarm_v2_mission


def test_check_swarm_budget_is_disabled():
    budgets = MissionBudget(max_agent_calls=4, max_leadership_transfers=2, max_remediation_rounds=1)
    assert check_swarm_budget({"usage": {"agent_calls": 999}}, budgets).ok
    assert check_swarm_budget({"usage": {"agent_calls": 3}}, budgets, agent_calls_needed=999).ok
    assert check_swarm_budget(
        {"handoff_history": [{"epoch": 1}, {"epoch": 2}], "usage": {}}, budgets
    ).ok
    assert check_swarm_budget({"remediation_round": 999, "usage": {}}, budgets).ok
    budgets = MissionBudget(token_budget=100)
    assert check_swarm_budget({"usage": {}}, budgets, token_usage=10_000).ok
    budgets = MissionBudget(max_runtime_s=30.0)
    assert check_swarm_budget(
        {"started_at": "2000-01-01T00:00:00Z", "usage": {}},
        budgets,
    ).ok


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
async def test_swarm_v2_agent_call_budget_no_longer_truncates_mission(runtime):
    result = await run_swarm_v2_mission(
        runtime,
        query="Why has CAC increased?",
        timezone="Asia/Kolkata",
        as_of="2026-08-01",
        budget_overrides={"max_agent_calls": 2},
    )
    kinds = [e.get("kind") for e in result.events]
    assert "mission_budget_exhausted" not in kinds
