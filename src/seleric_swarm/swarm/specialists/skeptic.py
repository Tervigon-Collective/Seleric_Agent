"""Skeptic - "Is this really true?" (architecture sec. 13).

Structurally adversarial. It does not write the final answer; it attacks the
candidate conclusion and returns PASS / REVISE / REJECT plus remediation tasks.
Data-quality / confounder attacks route to ``StatsEngine``.
"""

from __future__ import annotations

from seleric_swarm.swarm.artifacts import Skeptic
from seleric_swarm.swarm.blackboard import Blackboard
from seleric_swarm.swarm.mission import SwarmMission
from seleric_swarm.swarm.specialists.base import SpecialistAgent

_ATTACKS = [
    "alternative_explanation",
    "baseline_fairness",
    "attribution_change",
    "seasonality",
    "sample_size",
    "temporal_precedence",
    "uncontrolled_confounder",
    "model_reliability",
    "recommendation_addresses_cause",
]


class SkepticAgent(SpecialistAgent):
    agent_id = "skeptic_agent"
    capability = "challenge"
    produces = "skeptic"

    def policy(self, blackboard: Blackboard, mission: SwarmMission) -> bool:
        return bool(blackboard.by_type("causal") or blackboard.by_type("strategy"))

    async def run(self, blackboard: Blackboard, mission: SwarmMission) -> list[str]:
        causal = (blackboard.by_type("causal") or [{}])[0]
        strategy = (blackboard.by_type("strategy") or [{}])[0]
        target_ref = causal.get("artifact_id") or strategy.get("artifact_id")

        problems: list[dict[str, str]] = []
        followups: list[dict[str, str]] = []

        for attack in _ATTACKS:
            if attack in {"alternative_explanation", "uncontrolled_confounder", "seasonality", "sample_size"}:
                res = await self.providers.stats.check(
                    name=attack,
                    data={"outcome": causal.get("outcome"), "treatment": causal.get("treatment")},
                )
                if not res.passed:
                    problems.append({"type": attack, "description": f"{attack} check failed: {res.detail}"})
                    followups.append({"capability": "causal_diagnosis", "instruction": f"Control for {attack}."})
            elif attack == "temporal_precedence":
                has_event = any(str(e.get("metric_or_fact", "")).startswith("event.") for e in blackboard.by_type("evidence"))
                if not has_event:
                    problems.append({"type": attack, "description": "No event establishes treatment before outcome."})
            elif attack == "model_reliability" and causal and not causal.get("passed"):
                problems.append({"type": attack, "description": "Causal estimate did not pass refutation."})
            elif attack == "recommendation_addresses_cause" and strategy:
                rec = strategy.get("recommended") or []
                fits = [o for o in strategy.get("options", []) if o.get("action") in rec and o.get("mechanism_fit") in {"high", "very_high"}]
                if not fits:
                    problems.append({"type": attack, "description": "Top recommendation does not attack the diagnosed mechanism."})

        verdict = "PASS" if not problems else ("REVISE" if followups or len(problems) < 3 else "REJECT")
        art = Skeptic.new(
            mission_id=blackboard.mission_id,
            created_by=self.agent_id,
            target_ref=target_ref,
            verdict=verdict,  # type: ignore[arg-type]
            attacks_run=list(_ATTACKS),
            problems=problems,
            required_followups=followups,
            evidence_refs=[r for r in (target_ref,) if r],
        )
        if blackboard.has_synthetic_inputs([r for r in (target_ref,) if r]):
            art.mark_synthetic()
        blackboard.record_event("skeptic_done", verdict=verdict, problems=len(problems))
        return [blackboard.post(art)]
