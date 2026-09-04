"""Sensitivity analysis for causal effect estimates.

A light deterministic proxy for formal sensitivity analysis (e.g. E-value /
partial-R2). Given an effect and CI, report how much unmeasured confounding it
would take to explain the effect away, and whether the CI is robust.
"""

from __future__ import annotations

from dataclasses import dataclass

from seleric_swarm.agents.skeptic.context import SkepticContext


@dataclass
class SensitivityResult:
    robust: bool
    margin: float
    note: str


def run_sensitivity(ctx: SkepticContext) -> SensitivityResult | None:
    if not ctx.causal:
        return None
    c = ctx.causal[0]
    if c.estimated_effect is None or len(c.confidence_interval) != 2:
        return SensitivityResult(False, 0.0, "No effect/CI to test sensitivity on.")
    lo, hi = sorted(c.confidence_interval)
    crosses_zero = lo <= 0 <= hi
    margin = min(abs(lo), abs(hi)) if not crosses_zero else 0.0
    robust = (not crosses_zero) and (len([r for r in c.refutation_results if r.get("passed")]) >= 2)
    note = (
        "CI excludes zero and >=2 refuters pass; effect is moderately robust to unmeasured confounding."
        if robust
        else "CI includes zero or refutation coverage is thin; effect is sensitive to confounding."
    )
    return SensitivityResult(robust=robust, margin=round(margin, 4), note=note)
