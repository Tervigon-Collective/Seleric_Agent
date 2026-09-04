"""Verdict engine (spec sec. 36).

Exactly three verdicts. The decision is deterministic and explainable:

* REJECT  -- a blocking challenge or a REJECTED validator (evidence contradicts
             the claim, invalid metric definition, impossible causal ordering,
             drifted/out-of-domain model, strategy-mechanism mismatch, broken
             methodology).
* REVISE  -- the claim might be true but evidence is incomplete: a blocking
             evidence gap, an unresolved high-priority alternative, an
             unreconciled semantic/source conflict, or trust below threshold.
* PASS    -- required evidence present, no blocking issue, methodology
             acceptable, claim strength matches the evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from seleric_swarm.agents.skeptic.context import SkepticContext, ValidatorOutcome
from seleric_swarm.agents.skeptic.contracts import AlternativeHypothesis, EvidenceGap, Verdict


@dataclass
class VerdictDecision:
    verdict: Verdict
    reasons: list[str] = field(default_factory=list)


def decide_verdict(
    ctx: SkepticContext,
    outcomes: list[ValidatorOutcome],
    gaps: list[EvidenceGap],
    alternatives: list[AlternativeHypothesis],
    trust_score: float,
) -> VerdictDecision:
    reasons: list[str] = []

    # -- REJECT --------------------------------------------------------
    for oc in outcomes:
        if oc.status == "REJECTED":
            reasons.append(f"{oc.validator}: REJECTED")
        for ch in oc.challenges:
            if ch.severity == "blocking":
                reasons.append(f"{oc.validator}: blocking - {ch.description}")
    if reasons:
        return VerdictDecision("REJECT", reasons)

    # -- REVISE ------------------------------------------------------
    blocking_gaps = [g for g in gaps if g.blocking]
    if blocking_gaps:
        reasons += [f"blocking evidence gap: {g.description}" for g in blocking_gaps]

    open_alts = [a for a in alternatives if a.status == "open" and a.priority >= 6]
    if open_alts:
        reasons += [f"unresolved alternative: {a.hypothesis}" for a in open_alts]

    _REVISE_CATEGORIES = {
        "metric", "source", "contradiction", "anomaly", "statistical", "model",
        "forecast", "causal", "data_quality", "temporal",
    }
    unresolved_warnings = [
        ch
        for oc in outcomes
        for ch in oc.challenges
        if ch.category in _REVISE_CATEGORIES and ch.severity == "warning"
    ]
    if unresolved_warnings:
        reasons += [f"unresolved issue: {ch.description}" for ch in unresolved_warnings]

    if any(oc.status in {"UNAVAILABLE", "INSUFFICIENT"} for oc in outcomes):
        reasons += [f"{oc.validator}: {oc.status}" for oc in outcomes if oc.status in {"UNAVAILABLE", "INSUFFICIENT"}]

    revise_below = ctx.policies.trust_revise_below()
    if trust_score < revise_below:
        reasons.append(f"trust_score {trust_score:.2f} < {revise_below:.2f}")

    if any(oc.methodological_issues for oc in outcomes) and trust_score < revise_below + 0.15:
        reasons.append("nonfatal methodological issues with only moderate trust")

    if reasons:
        return VerdictDecision("REVISE", reasons)

    return VerdictDecision("PASS", [f"all gates passed; trust_score {trust_score:.2f}"])
