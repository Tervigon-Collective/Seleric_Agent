"""Bridge: run the full Diagnostic subsystem inside the two-axis swarm loop.

``SwarmDiagnosticSpecialist`` matches the swarm specialist interface
(``agent_id`` / ``produces`` / ``policy`` / ``run(blackboard, mission)``) but
delegates to ``agents.diagnostic.DiagnosticAgent``, then writes the equivalent
``Hypothesis`` + ``Causal`` Blackboard artifacts the synthesizer, completion gate
and existing tests expect.

Enable per run: ``run_swarm_mission(runtime, query=..., scenario_id=..., full_diagnostic=True)``.
"""

from __future__ import annotations

from typing import Any

from seleric_swarm.agents.diagnostic.agent import DiagnosticAgent, diagnostic_deps_from_blackboard
from seleric_swarm.agents.diagnostic.context import DiagnosticDeps
from seleric_swarm.agents.diagnostic.contracts import DiagnosticRequest, DiagnosticResult
from seleric_swarm.agents.diagnostic.policies import DiagnosticPolicies
from seleric_swarm.agents.diagnostic.registries import (
    TemplateCausalEstimationService,
    causal_graphs_from_yaml,
)
from seleric_swarm.swarm.artifacts import Causal, Hypothesis
from seleric_swarm.swarm.blackboard import Blackboard
from seleric_swarm.swarm.mission import SwarmMission


class SwarmDiagnosticSpecialist:
    agent_class = "specialist"
    agent_id = "diagnostic_agent"
    capability = "causal_diagnosis"
    produces = "causal"

    def __init__(
        self,
        providers: Any = None,
        *,
        scenario: dict[str, Any] | None = None,
        deps: DiagnosticDeps | None = None,
        policies: DiagnosticPolicies | None = None,
        ontology: Any = None,
    ) -> None:
        self.providers = providers
        self._scenario = scenario or {}
        self._deps = deps
        self._policies = policies or DiagnosticPolicies.load()
        self._ontology = ontology

    def policy(self, blackboard: Blackboard, mission: SwarmMission) -> bool:
        return mission.wants("diagnostic") and bool(blackboard.by_type("anomaly"))

    async def run(self, blackboard: Blackboard, mission: SwarmMission) -> list[str]:
        # idempotent re-run: replace this agent's prior output, don't accumulate
        blackboard.discard_by(created_by="diagnostic_agent", artifact_types=("hypothesis", "causal"))
        base = self._deps or DiagnosticDeps(
            causal_graphs=causal_graphs_from_yaml(),
            causal_service=TemplateCausalEstimationService(self._scenario.get("causal_truth", {})),
            ontology=self._ontology,
        )
        deps = diagnostic_deps_from_blackboard(blackboard, base=base)
        agent = DiagnosticAgent(deps=deps, policies=self._policies)

        request = DiagnosticRequest(
            mission_id=blackboard.mission_id,
            question=mission.query,
            primary_metric=str(mission.context.get("primary_metric") or ""),
            lead_domain=blackboard.mission_lead,
            time_range=dict(mission.time_range),
            degradation_started_at=mission.context.get("degradation_started_at"),
            context={
                # fixture/replay mode: the template causal truth is authoritative
                "trust_metadata_causal": True,
            },
        )
        result: DiagnosticResult = await agent.diagnose(request)

        posted, retained_ids = _write_artifacts(blackboard, result)
        blackboard.record_event(
            "diagnostic_done",
            hypotheses=len(result.hypotheses),
            retained=retained_ids,  # Blackboard artifact ids (not the internal ones)
            causal_confidence=result.finding.causal_confidence if result.finding else None,
            incident_type=result.incident_type,
            contradictions=len(result.contradictions),
        )
        if result.leadership_transfer_recommended:
            # Diagnostic only proposes; the Coordinator's Leadership Manager /
            # frontier evaluation independently decides whether to transfer.
            blackboard.record_event(
                "leadership_transfer_recommended",
                current_lead=blackboard.mission_lead,
                recommended_lead=result.recommended_domain_lead,
                reason=result.leadership_transfer_reason,
            )
        return posted


def _write_artifacts(blackboard: Blackboard, result: DiagnosticResult) -> tuple[list[str], list[str]]:
    id_map: dict[str, str] = {}
    posted: list[str] = []
    for h in result.hypotheses:
        art = Hypothesis.new(
            mission_id=blackboard.mission_id,
            created_by="diagnostic_agent",
            statement=h.statement,
            domains=h.domains,
            status="retained" if h.status == "retained" else ("rejected" if h.status == "rejected" else "testing"),
            supporting_evidence=h.supporting_evidence,
            required_tests=h.required_tests,
            score=h.posterior_score,
            evidence_refs=h.supporting_evidence,
        )
        if h.synthetic or result.synthetic:
            art.mark_synthetic()
        hid = blackboard.post(art)
        id_map[h.hypothesis_id] = hid
        posted.append(hid)

    ca = result.causal_artifact
    if ca is not None:
        primary_hyp = next((h for h in result.hypotheses if h.status == "retained"), None) or (
            result.hypotheses[0] if result.hypotheses else None
        )
        causal = Causal.new(
            mission_id=blackboard.mission_id,
            created_by="diagnostic_agent",
            hypothesis_ref=id_map.get(primary_hyp.hypothesis_id) if primary_hyp else None,
            treatment=ca.treatment,
            outcome=ca.outcome,
            common_causes=ca.common_causes,
            graph_id=ca.graph_id,
            estimator=ca.estimator,
            effect=ca.estimated_effect,
            effect_ci=ca.confidence_interval,
            refutations=ca.refutation_results,
            passed=ca.passed and (result.finding.causal_confidence != "REJECTED" if result.finding else ca.passed),
            evidence_refs=[id_map[h.hypothesis_id] for h in result.retained() if h.hypothesis_id in id_map],
        )
        if ca.synthetic or result.synthetic:
            causal.mark_synthetic()
        posted.append(blackboard.post(causal))

    retained_ids = [id_map[h.hypothesis_id] for h in result.retained() if h.hypothesis_id in id_map]
    return posted, retained_ids
