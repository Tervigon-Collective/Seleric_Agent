"""Bridge: run the real Strategy Agent inside the two-axis swarm loop.

``SwarmStrategySpecialist`` matches the swarm specialist interface and writes a
``Strategy`` Blackboard artifact from the ``StrategyResult`` the subsystem
produces, so the Skeptic's ``StrategyValidator``/``StrategyArtifact.from_blackboard``
and the synthesizer are unchanged. There is no lightweight/canned fallback
implementation — when no mechanism has been diagnosed, or no reasoning model
is configured, no options are produced and no artifact is posted.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from seleric_swarm.agents.strategy.agent import StrategyAgent
from seleric_swarm.agents.strategy.context import StrategyDeps
from seleric_swarm.agents.strategy.contracts import StrategyRequest, StrategyResult
from seleric_swarm.agents.strategy.policies import StrategyPolicies
from seleric_swarm.agents.strategy.reasoning import LLMPortReasoningModel, NullReasoningModel
from seleric_swarm.swarm.artifacts import Strategy
from seleric_swarm.swarm.blackboard import Blackboard
from seleric_swarm.swarm.mission import SwarmMission

if TYPE_CHECKING:
    from seleric_swarm.runtime import SwarmRuntime


class SwarmStrategySpecialist:
    agent_class = "specialist"
    agent_id = "strategy_agent"
    capability = "intervention_design"
    produces = "strategy"

    def __init__(
        self,
        providers: Any = None,
        *,
        runtime: SwarmRuntime | None = None,
        deps: StrategyDeps | None = None,
        policies: StrategyPolicies | None = None,
    ) -> None:
        self.providers = providers
        self._runtime = runtime
        self._deps = deps
        self._policies = policies or StrategyPolicies.load()

    def policy(self, blackboard: Blackboard, mission: SwarmMission) -> bool:
        retained = [h for h in blackboard.by_type("hypothesis") if h.get("status") == "retained"]
        return mission.wants("prescriptive") and bool(retained or blackboard.by_type("causal"))

    def _deps_for_run(self) -> StrategyDeps:
        if self._deps is not None:
            return self._deps
        if self._runtime is not None and self._runtime.settings.azure_openai_model:
            reasoning = LLMPortReasoningModel(
                self._runtime.llm,
                model=self._runtime.settings.azure_openai_model,
            )
            return StrategyDeps(reasoning=reasoning)
        return StrategyDeps(reasoning=NullReasoningModel())

    async def run(self, blackboard: Blackboard, mission: SwarmMission) -> list[str]:
        blackboard.discard_by(created_by="strategy_agent", artifact_types=("strategy",))

        retained = [h for h in blackboard.by_type("hypothesis") if h.get("status") == "retained"]
        causal = blackboard.by_type("causal")
        mechanism, mechanism_ref, treatment_metric, outcome_metric = _resolve_mechanism(retained, causal)
        owner_domain = _resolve_owner_domain(retained, blackboard.mission_lead)

        agent = StrategyAgent(deps=self._deps_for_run(), policies=self._policies)
        request = StrategyRequest(
            mission_id=blackboard.mission_id,
            question=mission.query,
            owner_domain=owner_domain,
            diagnosed_mechanism=mechanism,
            treatment_metric=treatment_metric,
            outcome_metric=outcome_metric,
            mechanism_ref=mechanism_ref,
            evidence_refs=blackboard.refs_by_type("causal"),
        )
        result: StrategyResult = await agent.propose(request)

        if not result.has_options():
            blackboard.record_event("strategy_insufficient", reasons=result.limitations)
            return []

        art = _to_artifact(blackboard, result)
        if result.synthetic or blackboard.has_synthetic_inputs(result.evidence_refs):
            art.mark_synthetic()
        blackboard.record_event(
            "strategy_done",
            source=result.source,
            confidence=result.confidence,
            options=len(result.options),
        )
        return [blackboard.post(art)]


def _resolve_mechanism(
    retained: list[dict[str, Any]], causal: list[dict[str, Any]]
) -> tuple[str, str | None, str | None, str | None]:
    if causal:
        c = causal[0]
        treatment, outcome = c.get("treatment") or "", c.get("outcome") or ""
        if treatment and outcome:
            return f"{treatment} -> {outcome}", c.get("artifact_id"), treatment, outcome
    if retained:
        h = retained[0]
        return str(h.get("statement") or ""), h.get("artifact_id"), None, None
    return "", None, None, None


def _resolve_owner_domain(retained: list[dict[str, Any]], mission_lead: str | None) -> str | None:
    if retained:
        domains = retained[0].get("domains") or []
        if domains:
            return str(domains[0])
    if mission_lead:
        return mission_lead.removesuffix("_agent")
    return None


def _to_artifact(blackboard: Blackboard, result: StrategyResult) -> Strategy:
    return Strategy.new(
        mission_id=blackboard.mission_id,
        created_by="strategy_agent",
        problem_ref=result.mechanism_ref,
        objective=result.objective,
        options=[o.model_dump() for o in result.options],
        recommended=result.recommended,
        rationale=result.rationale,
        owner_domain=result.owner_domain,
        evidence_refs=result.evidence_refs,
        quality_flags=[f"confidence:{result.confidence}", f"source:{result.source}"],
    )
