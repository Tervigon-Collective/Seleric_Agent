"""Mechanism-grounded intervention generation.

No mechanism, no generation call, no options — the caller decides whether to
resolve one first (Diagnostic) rather than let Strategy guess. When a
mechanism is present the reasoning model is required: there is no keyword or
template fallback here, matching Prediction's "the LLM is never a fallback,
but here it is the only generator so its absence means insufficient evidence,
not a degraded guess."
"""

from __future__ import annotations

import structlog

from seleric_swarm.agents.strategy.context import StrategyContext
from seleric_swarm.agents.strategy.contracts import InterventionOption, InterventionOptionsLLM
from seleric_swarm.agents.strategy.prompts import options_system, options_user

_log = structlog.get_logger("seleric_swarm.agents.strategy")


async def generate_options(ctx: StrategyContext) -> list[InterventionOption]:
    mechanism = ctx.request.diagnosed_mechanism.strip()
    if not mechanism:
        return []

    max_options = ctx.policies.budget("max_options")
    try:
        result = await ctx.deps.reasoning.generate_structured(
            system=options_system(max_options),
            user=options_user(
                question=ctx.request.question,
                mechanism=mechanism,
                treatment_metric=ctx.request.treatment_metric,
                outcome_metric=ctx.request.outcome_metric,
                owner_domain=ctx.request.owner_domain,
                max_options=max_options,
            ),
            schema=InterventionOptionsLLM,
            tags=["strategy", "generate_options"],
        )
    except Exception as exc:
        _log.warning("strategy.generation_failed", error=str(exc))
        return []

    return list(result.options)[:max_options]
