"""Validation routing (spec sec. 16).

Maps ``claim_type`` (+ risk class + which artifacts are attached) to the subset
of type-specific validators to run. Core validators (evidence, provenance,
metric, contradiction) always run and are added by the graph, not here.
"""

from __future__ import annotations

from seleric_swarm.agents.skeptic.context import SkepticContext

_BASE: dict[str, list[str]] = {
    "numeric": ["statistical"],
    "comparison": ["statistical"],
    "anomaly": ["anomaly", "statistical"],
    "correlation": ["statistical", "causal"],
    "causal": ["causal", "statistical"],
    "forecast": ["model", "forecast"],
    "recommendation": ["strategy"],
    "action": ["strategy"],
    "qualitative": [],
}


def select_validators(ctx: SkepticContext) -> list[str]:
    selected = list(_BASE.get(ctx.claim.claim_type, []))

    # attach validators for any artifact that is present regardless of declared type
    if ctx.causal and "causal" not in selected:
        selected.append("causal")
    if (ctx.forecasts or ctx.claim.forecast_refs) and "forecast" not in selected:
        selected.extend([v for v in ("model", "forecast") if v not in selected])
    if (ctx.strategies or ctx.claim.strategy_refs) and "strategy" not in selected:
        selected.append("strategy")
    if ctx.anomalies and "anomaly" not in selected:
        selected.append("anomaly")

    # low-risk trivial claims skip the statistical deep-dive
    if ctx.risk_class in {"R0", "R1"} and ctx.claim.claim_type in {"numeric", "comparison"}:
        selected = [v for v in selected if v != "statistical"]

    # respect the parallel-check budget
    cap = ctx.policies.budget("max_parallel_checks")
    return selected[:cap] if cap else selected
