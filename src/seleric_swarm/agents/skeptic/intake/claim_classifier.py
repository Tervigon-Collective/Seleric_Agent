"""Claim-type classification.

The origin agent usually sets ``claim_type`` explicitly; this module keeps it
honest. It never *upgrades* a claim to ``causal`` from wording alone (that would
let a narrative sentence trigger causal-grade scrutiny with no causal artifact),
but it will *flag* a mismatch so the evidence validator can raise a gap.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from seleric_swarm.agents.skeptic.contracts import Claim, ClaimType

_CAUSAL_RE = re.compile(r"\b(caused|because of|driven by|led to|root cause|due to|resulted in)\b", re.IGNORECASE)
_FORECAST_RE = re.compile(r"\b(will|forecast|projected|expected to|by next|over the next|if this continues)\b", re.IGNORECASE)
_RECOMMEND_RE = re.compile(r"\b(should|recommend|we ought to|the best move|propose to)\b", re.IGNORECASE)
_ACTION_RE = re.compile(r"\b(roll back|increase|decrease|pause|launch|cut|shift budget|reduce spend)\b", re.IGNORECASE)
_ANOMALY_RE = re.compile(r"\b(spik(e|ed)|anomal|unusual|abnormal|out of band|deviat)\b", re.IGNORECASE)
_COMPARISON_RE = re.compile(r"\b(vs\.?|versus|compared to|higher than|lower than|more than|less than)\b", re.IGNORECASE)
_CORRELATION_RE = re.compile(r"\b(correlat|associated with|moves with|tracks with)\b", re.IGNORECASE)
_NUMERIC_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?%?")


@dataclass
class ClaimClassification:
    claim_type: ClaimType
    declared_type: ClaimType
    mismatch: bool
    signals: list[str]


def classify_claim(claim: Claim) -> ClaimClassification:
    text = claim.statement or ""
    declared = claim.claim_type
    signals: list[str] = []

    inferred: ClaimType
    if _CAUSAL_RE.search(text):
        inferred = "causal"
        signals.append("causal_language")
    elif _FORECAST_RE.search(text):
        inferred = "forecast"
        signals.append("forecast_language")
    elif _ACTION_RE.search(text) and _RECOMMEND_RE.search(text):
        inferred = "recommendation"
        signals.append("recommendation_language")
    elif _ACTION_RE.search(text):
        inferred = "action"
        signals.append("action_language")
    elif _RECOMMEND_RE.search(text):
        inferred = "recommendation"
        signals.append("recommendation_language")
    elif _ANOMALY_RE.search(text):
        inferred = "anomaly"
        signals.append("anomaly_language")
    elif _CORRELATION_RE.search(text):
        inferred = "correlation"
        signals.append("correlation_language")
    elif _COMPARISON_RE.search(text):
        inferred = "comparison"
        signals.append("comparison_language")
    elif _NUMERIC_RE.search(text):
        inferred = "numeric"
        signals.append("numeric_literal")
    else:
        inferred = "qualitative"

    # Trust the declared type; only report a mismatch when the wording implies a
    # *stronger* epistemic claim than declared (causal/forecast/action).
    stronger = {"causal": 3, "forecast": 2, "action": 2, "recommendation": 2}
    mismatch = (
        declared != inferred
        and stronger.get(inferred, 0) > stronger.get(declared, 0)
    )
    resolved = declared
    if declared == "qualitative" and inferred != "qualitative":
        resolved = inferred  # a bare "qualitative" default is safe to specialize
    return ClaimClassification(
        claim_type=resolved, declared_type=declared, mismatch=mismatch, signals=signals
    )
