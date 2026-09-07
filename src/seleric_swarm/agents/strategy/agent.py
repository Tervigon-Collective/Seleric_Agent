"""StrategyAgent - the stable boundary for the Coordinator / swarm.

    result = await StrategyAgent(...).propose(request)

No diagnosed mechanism -> no options, ``INSUFFICIENT_STRATEGY_EVIDENCE``, same
shape as Prediction's "no model, no baseline -> INSUFFICIENT_PREDICTIVE_EVIDENCE".
Business-constraint checking (inventory, budget, margin, change freeze, ...) is
the Skeptic's job at validation time, not this agent's.
"""

from __future__ import annotations

import time

import structlog

from seleric_swarm.agents.strategy.context import StrategyContext, StrategyDeps
from seleric_swarm.agents.strategy.contracts import StrategyRequest, StrategyResult
from seleric_swarm.agents.strategy.generation import generate_options
from seleric_swarm.agents.strategy.policies import StrategyPolicies

_log = structlog.get_logger("seleric_swarm.agents.strategy")

_CONFIDENCE_BY_FIT = {
    "very_high": "STRONG",
    "high": "STRONG",
    "medium": "MODERATE",
    "low": "WEAK",
}


class StrategyAgent:
    agent_id = "strategy_agent"
    agent_version = "1.0.0"

    def __init__(
        self,
        *,
        deps: StrategyDeps | None = None,
        policies: StrategyPolicies | None = None,
    ) -> None:
        self.deps = deps or StrategyDeps()
        self.policies = policies or StrategyPolicies.load()

    async def propose(self, request: StrategyRequest) -> StrategyResult:
        started = time.perf_counter()
        ctx = StrategyContext(request=request, policies=self.policies, deps=self.deps)

        base = StrategyResult(
            mission_id=request.mission_id,
            owner_domain=request.owner_domain,
            mechanism_ref=request.mechanism_ref,
            evidence_refs=list(request.evidence_refs),
        )

        if not request.diagnosed_mechanism.strip():
            base.limitations.append(
                "INSUFFICIENT_STRATEGY_EVIDENCE - no diagnosed mechanism to act on."
            )
            self._emit(base, elapsed_ms=round((time.perf_counter() - started) * 1000, 2))
            return base

        options = await generate_options(ctx)
        if not options:
            base.limitations.append(
                "INSUFFICIENT_STRATEGY_EVIDENCE - no reasoning model available to generate options."
            )
            self._emit(base, elapsed_ms=round((time.perf_counter() - started) * 1000, 2))
            return base

        ranked = sorted(options, key=lambda o: self.policies.fit_rank(o.mechanism_fit), reverse=True)
        top = ranked[0]
        result = base.model_copy(
            update={
                "source": "mechanism_grounded",
                "confidence": _CONFIDENCE_BY_FIT.get(top.mechanism_fit, "WEAK"),
                "options": ranked,
                "recommended": [top.action],
                "objective": f"Address the diagnosed mechanism: {request.diagnosed_mechanism}",
                "rationale": top.rationale or f"Best mechanism-fit option for: {request.diagnosed_mechanism}",
                "methodology": "LLM-generated interventions constrained to the diagnosed mechanism; ranked by mechanism fit.",
            }
        )
        if not self.policies.meets_min_fit(top.mechanism_fit):
            result.limitations.append(
                f"No option reaches the minimum mechanism fit "
                f"('{self.policies.min_mechanism_fit_to_recommend()}'); best available is '{top.mechanism_fit}'."
            )
        self._emit(result, elapsed_ms=round((time.perf_counter() - started) * 1000, 2))
        return result

    def _emit(self, result: StrategyResult, *, elapsed_ms: float) -> None:
        _log.info(
            "strategy.run",
            mission_id=result.mission_id,
            strategy_run_id=result.strategy_run_id,
            source=result.source,
            confidence=result.confidence,
            options=len(result.options),
            has_options=result.has_options(),
            elapsed_ms=elapsed_ms,
        )
