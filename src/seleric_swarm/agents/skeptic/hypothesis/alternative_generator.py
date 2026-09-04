"""Alternative hypothesis generation (spec sec. 22).

Constrained generation. Candidates come from the causal-graph registry, the
incident registry and known parent metrics -- not free-form. An optional
reasoning model only *phrases* extra candidates; the count is capped by policy
and every candidate carries a falsification test.
"""

from __future__ import annotations

from typing import Any

import structlog
from pydantic import BaseModel

from seleric_swarm.agents.skeptic.context import SkepticContext
from seleric_swarm.agents.skeptic.contracts import AlternativeHypothesis

_log = structlog.get_logger("seleric_swarm.agents.skeptic")


class _AltList(BaseModel):
    alternatives: list[AlternativeHypothesis] = []


async def generate_alternatives(ctx: SkepticContext) -> list[AlternativeHypothesis]:
    cap = ctx.policies.budget("max_alternative_hypotheses")
    if ctx.claim.claim_type not in {"causal", "correlation", "anomaly", "recommendation", "action"}:
        return []

    candidates: list[AlternativeHypothesis] = []

    # 1. deterministic candidates from the incident registry
    domain = _domain(ctx)
    keywords = _keywords(ctx)
    for pat in ctx.deps.incident_registry.match(domain=domain, keywords=keywords):
        for conf in pat.known_confounders:
            candidates.append(
                AlternativeHypothesis(
                    hypothesis=f"{conf.capitalize()} explains the observed change rather than the claimed mechanism.",
                    mechanism=pat.typical_mechanism,
                    falsification_test=f"Hold {conf} constant / compare a period without {conf} and check the effect persists.",
                    priority=6,
                )
            )

    # 2. confounders from the causal graph attached to the claim
    for cc in ctx.causal:
        for common in cc.common_causes:
            candidates.append(
                AlternativeHypothesis(
                    hypothesis=f"The association is driven by the common cause '{common}'.",
                    mechanism=f"{common} moves both {cc.treatment} and {cc.outcome}.",
                    falsification_test=f"Adjust for {common}; if the effect vanishes the claim is confounded.",
                    priority=7,
                )
            )

    # 3. explicit alternatives listed on the claim metadata
    for alt in ctx.claim.metadata.get("alternatives_to_test", []) or []:
        candidates.append(
            AlternativeHypothesis(
                hypothesis=str(alt),
                mechanism="declared by upstream agent",
                falsification_test=f"Design a test that isolates '{alt}' from the claimed mechanism.",
                priority=6,
            )
        )

    # 4. optional LLM enrichment (phrasing only, still capped)
    if len(candidates) < cap and not (ctx.deps.reasoning is None):
        try:
            from seleric_swarm.agents.skeptic.prompts import (
                ALTERNATIVE_HYPOTHESIS_SYSTEM,
                alternative_hypothesis_user,
            )

            extra = await ctx.deps.reasoning.generate_structured(
                system=ALTERNATIVE_HYPOTHESIS_SYSTEM,
                user=alternative_hypothesis_user(ctx.claim, _llm_context(ctx, cap)),
                schema=_AltList,
                tags=["skeptic", "alternatives"],
            )
            candidates.extend(extra.alternatives)
        except Exception as exc:  # LLM failure must never break the run
            _log.debug("skeptic.alternatives.llm_skipped", error=str(exc))

    # de-dupe by hypothesis text, keep highest priority, cap
    seen: dict[str, AlternativeHypothesis] = {}
    for cand in candidates:
        key = cand.hypothesis.strip().lower()
        if key not in seen or cand.priority > seen[key].priority:
            seen[key] = cand
    ordered = sorted(seen.values(), key=lambda a: -a.priority)
    return ordered[:cap]


def _domain(ctx: SkepticContext) -> str | None:
    return ctx.claim.metadata.get("domain") or ctx.risk_context.get("domain")


def _keywords(ctx: SkepticContext) -> list[str]:
    words = ctx.claim.statement.lower().replace(",", " ").split()
    return [w for w in words if len(w) > 3][:12]


def _llm_context(ctx: SkepticContext, cap: int) -> dict[str, Any]:
    return {
        "incident_patterns": [p.pattern_id for p in ctx.deps.incident_registry.match(domain=_domain(ctx), keywords=_keywords(ctx))],
        "known_confounders": [c for cc in ctx.causal for c in cc.common_causes],
        "observed_metrics": sorted({e.metric_id for e in ctx.all_evidence() if e.metric_id}),
        "max_hypotheses": cap,
    }
