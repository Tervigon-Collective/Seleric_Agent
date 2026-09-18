"""Verdict signal — ported from ``agents/skeptic/scoring/verdict_engine.py``.

Signal **two of two**; see ``trust.py`` for why they stay separate.

Exactly three verdicts, deterministic and explainable:

* ``REJECT`` — a blocking challenge or a REJECTED check. The claim contradicts
  its evidence.
* ``REVISE`` — the claim might be true but the evidence is incomplete.
* ``PASS``   — every gate cleared.

Why bug #12's STRONG+REVISE is coherent, in numbers
---------------------------------------------------
``config/skeptic_policies.yaml`` puts the STRONG floor at **0.72** and
``verdict_thresholds.revise_below`` at **0.55**. Any score clearing STRONG
therefore clears this function's trust gate with room to spare, so a REVISE on a
STRONG claim can only come from a gap, an unresolved alternative, or a warning —
never from the trust score itself. That is the mechanism, not a coincidence.

Trust enters here at exactly one place (the floor below) and as a **float
only** — never the label, never the components. The original's signature made
that structural and this one keeps it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from seleric_swarm.toolsets import policy_config as policy

if TYPE_CHECKING:
    from seleric_swarm.agent.validation.signals import (
        AlternativeHypothesis,
        CheckOutcome,
        EvidenceGap,
    )

Verdict = Literal["PASS", "REVISE", "REJECT"]


@dataclass
class VerdictDecision:
    verdict: Verdict
    reasons: list[str] = field(default_factory=list)


def decide_verdict(
    outcomes: list[CheckOutcome],
    gaps: list[EvidenceGap],
    alternatives: list[AlternativeHypothesis],
    trust_score: float,
) -> VerdictDecision:
    reasons: list[str] = []

    # -- REJECT, evaluated first and returned immediately ----------------
    for oc in outcomes:
        if oc.status == "REJECTED":
            reasons.append(f"{oc.check}: REJECTED")
        for ch in oc.challenges:
            if ch.severity == "blocking":
                reasons.append(f"{oc.check}: blocking - {ch.description}")
    if reasons:
        return VerdictDecision("REJECT", reasons)

    # -- REVISE ---------------------------------------------------------
    # (a) blocking evidence gap. Note: gap.blocking only -- never gap.priority.
    blocking_gaps = [g for g in gaps if g.blocking]
    if blocking_gaps:
        reasons += [f"blocking evidence gap: {g.description}" for g in blocking_gaps]

    # (b) unresolved high-priority alternative.
    open_alts = [
        a for a in alternatives if a.status == "open" and a.priority >= policy.ALT_PRIORITY_REVISE_FLOOR
    ]
    if open_alts:
        reasons += [f"unresolved alternative: {a.hypothesis}" for a in open_alts]

    # (c) warning-category challenges. REVISE_CATEGORIES deliberately EXCLUDES
    # evidence / provenance / alternative_hypothesis / strategy -- widening the
    # set silently converts PASSes into REVISEs, so it is copied exactly.
    unresolved_warnings = [
        ch
        for oc in outcomes
        for ch in oc.challenges
        if ch.category in policy.REVISE_CATEGORIES and ch.severity == "warning"
    ]
    if unresolved_warnings:
        reasons += [f"unresolved issue: {ch.description}" for ch in unresolved_warnings]

    unavailable = [oc for oc in outcomes if oc.status in {"UNAVAILABLE", "INSUFFICIENT"}]
    if unavailable:
        reasons += [f"{oc.check}: {oc.status}" for oc in unavailable]

    # The one place trust touches the verdict, as a float floor.
    if trust_score < policy.TRUST_REVISE_BELOW:
        reasons.append(f"trust_score {trust_score:.2f} < {policy.TRUST_REVISE_BELOW:.2f}")

    if any(oc.methodological_issues for oc in outcomes) and (
        trust_score < policy.TRUST_REVISE_BELOW + policy.METHODOLOGY_TRUST_MARGIN
    ):
        reasons.append("nonfatal methodological issues with only moderate trust")

    if reasons:
        return VerdictDecision("REVISE", reasons)

    return VerdictDecision("PASS", [f"all gates passed; trust_score {trust_score:.2f}"])
