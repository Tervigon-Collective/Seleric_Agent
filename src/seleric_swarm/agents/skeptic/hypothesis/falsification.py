"""Falsification-first behaviour (spec sec. 23).

For a claim, enumerate the observable implications that should hold if it is true
and what should be seen if it is false, then hand those to the remediation
builder as concrete test requests.
"""

from __future__ import annotations

from dataclasses import dataclass

from seleric_swarm.agents.skeptic.context import SkepticContext


@dataclass
class FalsificationSet:
    if_true: list[str]
    if_false: list[str]
    tests: list[str]


def falsification_implications(ctx: SkepticContext) -> FalsificationSet:
    claim = ctx.claim
    text = claim.statement.lower()
    if_true: list[str] = []
    if_false: list[str] = []
    tests: list[str] = []

    if claim.claim_type in {"causal", "correlation"} and ctx.causal:
        c = ctx.causal[0]
        if_true += [
            f"Timing: {c.treatment} deterioration precedes {c.outcome} deterioration.",
            f"Dose-response: units with worse {c.treatment} show worse {c.outcome}.",
            f"Reversal: restoring {c.treatment} improves {c.outcome}.",
        ]
        if_false += [
            f"{c.outcome} degrades equally where {c.treatment} is unchanged.",
            f"A control segment with healthy {c.treatment} degrades just as much.",
        ]
        tests += [
            f"Compare {c.outcome} for high vs low {c.treatment} sessions in the same window.",
            "Check whether an unaffected control (e.g. desktop) moved as much.",
            f"Verify {c.treatment} change timestamp precedes the {c.outcome} change timestamp.",
        ]

    if "mobile" in text or "device" in text:
        if_true.append("Mobile is affected more than desktop.")
        tests.append("Compare the effect on mobile vs desktop segments.")

    if claim.claim_type == "forecast":
        if_true.append("Backtest error on recent holdout is within the stated interval.")
        tests.append("Backtest the model over the last N periods and compare to the interval.")

    if claim.claim_type in {"recommendation", "action"}:
        if_true.append("The action's target metric responds after the action in a pilot/holdout.")
        if_false.append("The diagnosed mechanism is unaffected by the action.")
        tests.append("Define a measurement plan: metric, pre/post window, control.")

    return FalsificationSet(if_true=if_true, if_false=if_false, tests=tests)
