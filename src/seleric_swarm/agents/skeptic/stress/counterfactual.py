"""Counterfactual / stress testing (spec sec. 33).

Evaluates how the conclusion would change under alternative assumptions. It does
NOT invent financial outputs: when a required value is missing it emits an
:class:`EvidenceGap` instead of a fabricated number.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from seleric_swarm.agents.skeptic.context import SkepticContext
from seleric_swarm.agents.skeptic.contracts import EvidenceGap
from seleric_swarm.agents.skeptic.hypothesis.falsification import falsification_implications


@dataclass
class StressResult:
    scenarios: list[dict] = field(default_factory=list)
    unresolved_alternatives: int = 0
    gaps: list[EvidenceGap] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def run_counterfactual(ctx: SkepticContext, alternatives: list) -> StressResult:
    res = StressResult()
    fset = falsification_implications(ctx)
    res.notes.extend(f"if_true: {i}" for i in fset.if_true)
    res.notes.extend(f"if_false: {i}" for i in fset.if_false)

    open_alts = [a for a in alternatives if getattr(a, "status", "open") == "open"]
    res.unresolved_alternatives = len(open_alts)

    if ctx.claim.claim_type == "forecast":
        fc = ctx.forecasts[0] if ctx.forecasts else None
        if fc and fc.interval and len(fc.interval) == 2:
            res.scenarios = [
                {"name": "base", "value": fc.prediction},
                {"name": "optimistic", "value": fc.interval[0]},
                {"name": "pessimistic", "value": fc.interval[1]},
            ]
        else:
            res.gaps.append(
                EvidenceGap(
                    description="Forecast lacks an interval; optimistic/pessimistic scenarios cannot be bounded.",
                    reason_required="Stress testing a forecast needs a quantified uncertainty band.",
                    capability_required="forecasting",
                    blocking=False,
                    priority=6,
                )
            )

    if ctx.claim.claim_type in {"recommendation", "action"}:
        for name in ("diagnosis_correct", "diagnosis_partially_correct", "diagnosis_incorrect"):
            res.scenarios.append({"name": name, "expected_outcome": _strategy_outcome(name, ctx)})
        if "diagnosis_incorrect" in [s["name"] for s in res.scenarios] and not ctx.claim.metadata.get("fallback_plan"):
            res.notes.append("No fallback plan if the diagnosis is wrong.")

    return res


def _strategy_outcome(scenario: str, ctx: SkepticContext) -> str:
    if scenario == "diagnosis_correct":
        return "action remediates the mechanism; target metric recovers."
    if scenario == "diagnosis_partially_correct":
        return "partial recovery; residual driver remains."
    return "action has no effect on the true driver; wasted cost / opportunity."
