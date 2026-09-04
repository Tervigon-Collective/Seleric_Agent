"""Bridge: run the full Skeptic subsystem inside the two-axis swarm loop.

``swarm/specialists/skeptic.py`` is a lightweight in-loop attacker. This bridge
exposes the *same* specialist interface (``agent_id`` / ``produces`` /
``policy`` / ``run(blackboard, mission)``) but delegates to
``agents.skeptic.SkepticAgent``, then writes an equivalent ``Skeptic``
Blackboard artifact so the synthesizer, completion gate and existing tests keep
working unchanged.

Swap it in from ``swarm/orchestrator.py``::

    from seleric_swarm.agents.skeptic.swarm_bridge import SwarmSkepticSpecialist
    skeptic = SwarmSkepticSpecialist(providers, deps=skeptic_deps)
"""

from __future__ import annotations

from typing import Any

from seleric_swarm.agents.skeptic.agent import SkepticAgent
from seleric_swarm.agents.skeptic.context import SkepticDeps
from seleric_swarm.agents.skeptic.contracts import Claim, SkepticValidationRequest, SkepticVerdict
from seleric_swarm.agents.skeptic.policies import SkepticPolicies
from seleric_swarm.agents.skeptic.registries import (
    causal_graphs_from_yaml,
    metric_registry_from_yaml,
    repositories_from_blackboard,
)
from seleric_swarm.swarm.artifacts import Skeptic
from seleric_swarm.swarm.blackboard import Blackboard
from seleric_swarm.swarm.mission import SwarmMission


class SwarmSkepticSpecialist:
    agent_class = "specialist"
    agent_id = "skeptic_agent"
    capability = "challenge"
    produces = "skeptic"

    def __init__(
        self,
        providers: Any = None,
        *,
        deps: SkepticDeps | None = None,
        policies: SkepticPolicies | None = None,
    ) -> None:
        self.providers = providers
        # Wire the registries the causal / metric validators need. Without the
        # causal-graph registry every causal claim REVISEs on
        # "graph '<id>' is not registered".
        self._deps = deps or SkepticDeps(
            metric_registry=metric_registry_from_yaml(),
            causal_graphs=causal_graphs_from_yaml(),
        )
        self._policies = policies or SkepticPolicies.load()

    # -- same policy gate as the lightweight specialist --------------------
    def policy(self, blackboard: Blackboard, mission: SwarmMission) -> bool:
        return bool(blackboard.by_type("causal") or blackboard.by_type("strategy"))

    async def run(self, blackboard: Blackboard, mission: SwarmMission) -> list[str]:
        claim, evidence_refs = _claim_from_blackboard(blackboard, mission)
        evidence_repo, artifact_repo = repositories_from_blackboard(blackboard)
        agent = SkepticAgent(
            evidence_repo=evidence_repo,
            artifact_repo=artifact_repo,
            deps=self._deps,
            policies=self._policies,
        )
        verdict: SkepticVerdict = await agent.validate_claim(
            SkepticValidationRequest(
                mission_id=blackboard.mission_id,
                claim=claim,
                evidence_refs=evidence_refs,
                risk_context={"domain": _lead_domain(blackboard)},
            )
        )
        art = _verdict_to_artifact(verdict, blackboard, claim)
        if blackboard.has_synthetic_inputs(claim.support_refs) or verdict.audit.get("synthetic_inputs"):
            art.mark_synthetic()
        blackboard.record_event(
            "skeptic_done",
            verdict=verdict.verdict,
            problems=len(verdict.challenges),
            trust_label=verdict.trust_label,
            followups=len(verdict.required_followups),
        )
        return [blackboard.post(art)]


def _claim_from_blackboard(blackboard: Blackboard, mission: SwarmMission) -> tuple[Claim, list[str]]:
    causal = blackboard.by_type("causal")
    strategy = blackboard.by_type("strategy")
    retained = [h for h in blackboard.by_type("hypothesis") if h.get("status") == "retained"]
    evidence_refs = list(blackboard.evidence_ledger)

    if strategy:
        s = strategy[0]
        recommended = (s.get("recommended") or [""])[0]
        return (
            Claim(
                mission_id=blackboard.mission_id,
                claim_type="recommendation",
                statement=f"Recommended action: {recommended}",
                origin_agent="strategy_agent",
                support_refs=evidence_refs,
                strategy_refs=[s["artifact_id"]],
                causal_refs=[c["artifact_id"] for c in causal],
                metadata={
                    "diagnosed_mechanism": (
                        f"{causal[0].get('treatment')} -> {causal[0].get('outcome')}" if causal else None
                    ),
                    "domain": _lead_domain(blackboard),
                    "alternatives_ruled_out": True,
                },
            ),
            evidence_refs,
        )

    if causal:
        c = causal[0]
        statement = retained[0]["statement"] if retained else (
            f"{c.get('treatment')} caused a change in {c.get('outcome')}"
        )
        return (
            Claim(
                mission_id=blackboard.mission_id,
                claim_type="causal",
                statement=statement,
                origin_agent="diagnostic_agent",
                support_refs=evidence_refs,
                causal_refs=[c["artifact_id"]],
                metadata={"domain": _lead_domain(blackboard), "alternatives_ruled_out": True},
            ),
            evidence_refs,
        )

    return (
        Claim(
            mission_id=blackboard.mission_id,
            claim_type="qualitative",
            statement=mission.query,
            origin_agent="coordinator_agent",
            support_refs=evidence_refs,
        ),
        evidence_refs,
    )


def _verdict_to_artifact(verdict: SkepticVerdict, blackboard: Blackboard, claim: Claim) -> Skeptic:
    target_ref = next(iter(claim.strategy_refs or claim.causal_refs or []), None)
    problems = [
        {
            "type": ch.category,
            "severity": ch.severity,
            "description": ch.description,
            "evidence_refs": ch.evidence_refs,
        }
        for ch in verdict.challenges
    ]
    followups = [
        {
            "capability": t.requested_capability,
            "instruction": t.objective,
            "question": t.question,
            "preferred_domain": t.preferred_domain,
            "blocking": t.blocking,
            "priority": t.priority,
        }
        for t in verdict.required_followups
    ]
    return Skeptic.new(
        mission_id=blackboard.mission_id,
        created_by="skeptic_agent",
        target_ref=target_ref,
        verdict=verdict.verdict,  # type: ignore[arg-type]
        attacks_run=verdict.audit.get("validators_selected", []) + list(verdict.validator_results),
        problems=problems,
        required_followups=followups,
        evidence_refs=[r for r in (target_ref,) if r],
        quality_flags=[f"trust:{verdict.trust_label}", f"risk:{verdict.risk_class}"],
    )


def _lead_domain(blackboard: Blackboard) -> str | None:
    lead = blackboard.mission_lead or ""
    return lead.removesuffix("_agent") or None
