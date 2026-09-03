"""Strategy - "Given what we know, what should we change?" (architecture sec. 12).

Options are ranked mechanism-fit first: an intervention that attacks the
diagnosed mechanism beats one that treats a symptom. Ranking is delegated to
``Optimizer``. No business action is executed (autonomy level 6 disabled).
"""

from __future__ import annotations

from typing import Any

from seleric_swarm.swarm.artifacts import Strategy
from seleric_swarm.swarm.blackboard import Blackboard
from seleric_swarm.swarm.mission import SwarmMission
from seleric_swarm.swarm.specialists.base import SpecialistAgent


class StrategyAgent(SpecialistAgent):
    agent_id = "strategy_agent"
    agent_class = "strategy"
    capability = "intervention_design"
    produces = "strategy"

    def policy(self, blackboard: Blackboard, mission: SwarmMission) -> bool:
        retained = [h for h in blackboard.by_type("hypothesis") if h.get("status") == "retained"]
        return mission.wants("prescriptive") and bool(retained)

    def _options(self, blackboard: Blackboard) -> list[dict[str, Any]]:
        event = next(
            (e for e in blackboard.by_type("evidence") if str(e.get("metric_or_fact", "")).startswith("event.")),
            None,
        )
        deploy_id = (event or {}).get("provenance", {}).get("event_id", "the recent deploy")
        return [
            {"action": f"Roll back {deploy_id}", "mechanism_fit": "very_high", "expected_impact": "high", "cost": "low", "risk": "low", "reversibility": "high"},
            {"action": "Hotfix the mobile JS / LCP regression", "mechanism_fit": "very_high", "expected_impact": "high", "cost": "medium", "risk": "medium", "reversibility": "high"},
            {"action": "Reduce paid acquisition spend", "mechanism_fit": "low", "expected_impact": "medium", "cost": "low", "risk": "high", "reversibility": "high"},
            {"action": "Increase discount to lift conversion", "mechanism_fit": "low", "expected_impact": "medium", "cost": "high", "risk": "high", "reversibility": "medium"},
            {"action": "Shift campaigns to other channels", "mechanism_fit": "low", "expected_impact": "low", "cost": "medium", "risk": "medium", "reversibility": "medium"},
        ]

    async def run(self, blackboard: Blackboard, mission: SwarmMission) -> list[str]:
        retained = [h for h in blackboard.by_type("hypothesis") if h.get("status") == "retained"]
        problem_ref = retained[0]["artifact_id"] if retained else None
        options = self._options(blackboard)
        ranked = await self.providers.optimizer.rank(
            problem={"hypothesis": problem_ref, "objective": "restore mobile purchase conversion"},
            options=options,
        )
        art = Strategy.new(
            mission_id=blackboard.mission_id,
            created_by=self.agent_id,
            problem_ref=problem_ref,
            objective="Restore mobile purchase conversion; do not react on the media side.",
            options=ranked.options,
            recommended=ranked.recommended,
            rationale=(
                "Media metrics are within band, so acquisition-side changes treat a symptom. "
                "The validated mechanism is a post-click regression - fix or roll back the deploy."
            ),
            evidence_refs=blackboard.refs_by_type("causal"),
        )
        if ranked.synthetic or blackboard.has_synthetic_inputs(blackboard.refs_by_type("causal")):
            art.mark_synthetic()
        ok, problems = self.validate(art.model_dump())
        if not ok:
            blackboard.record_event("strategy_rejected", problems=problems)
            return []
        return [blackboard.post(art)]
