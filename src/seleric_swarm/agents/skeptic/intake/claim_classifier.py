"""Claim-type classification.

The origin agent usually sets ``claim_type`` explicitly; this module keeps it
honest. It never *upgrades* a claim to ``causal`` from wording alone (that would
let a narrative sentence trigger causal-grade scrutiny with no causal artifact),
but it will *flag* a mismatch so the evidence validator can raise a gap.

Regex is the always-available deterministic base (and the offline fallback);
when a reasoning model is configured it refines the inferred type from full
sentence meaning instead of keyword hits, same pattern as
``hypothesis/alternative_generator.py``'s LLM enrichment.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, get_args

import structlog
from pydantic import BaseModel

from seleric_swarm.agents.skeptic.contracts import Claim, ClaimType

if TYPE_CHECKING:
    from seleric_swarm.agents.skeptic.context import SkepticContext

_log = structlog.get_logger("seleric_swarm.agents.skeptic")

_CAUSAL_RE = re.compile(r"\b(caused|because of|driven by|led to|root cause|due to|resulted in)\b", re.IGNORECASE)
_FORECAST_RE = re.compile(r"\b(will|forecast|projected|expected to|by next|over the next|if this continues)\b", re.IGNORECASE)
_RECOMMEND_RE = re.compile(r"\b(should|recommend|we ought to|the best move|propose to)\b", re.IGNORECASE)
_ACTION_RE = re.compile(r"\b(roll back|increase|decrease|pause|launch|cut|shift budget|reduce spend)\b", re.IGNORECASE)
_ANOMALY_RE = re.compile(r"\b(spik(e|ed)|anomal|unusual|abnormal|out of band|deviat)\b", re.IGNORECASE)
_COMPARISON_RE = re.compile(r"\b(vs\.?|versus|compared to|higher than|lower than|more than|less than)\b", re.IGNORECASE)
_CORRELATION_RE = re.compile(r"\b(correlat|associated with|moves with|tracks with)\b", re.IGNORECASE)
_NUMERIC_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?%?")

_KNOWN_TYPES = set(get_args(ClaimType))
# only a stronger epistemic type than declared counts as a mismatch worth flagging
_STRENGTH = {"causal": 3, "forecast": 2, "action": 2, "recommendation": 2}


class _ClaimTypeLLM(BaseModel):
    claim_type: str
    signals: list[str] = []


@dataclass
class ClaimClassification:
    claim_type: ClaimType
    declared_type: ClaimType
    mismatch: bool
    signals: list[str]


def _classify_regex(text: str) -> tuple[ClaimType, list[str]]:
    if _CAUSAL_RE.search(text):
        return "causal", ["causal_language"]
    if _FORECAST_RE.search(text):
        return "forecast", ["forecast_language"]
    if _ACTION_RE.search(text) and _RECOMMEND_RE.search(text):
        return "recommendation", ["recommendation_language"]
    if _ACTION_RE.search(text):
        return "action", ["action_language"]
    if _RECOMMEND_RE.search(text):
        return "recommendation", ["recommendation_language"]
    if _ANOMALY_RE.search(text):
        return "anomaly", ["anomaly_language"]
    if _CORRELATION_RE.search(text):
        return "correlation", ["correlation_language"]
    if _COMPARISON_RE.search(text):
        return "comparison", ["comparison_language"]
    if _NUMERIC_RE.search(text):
        return "numeric", ["numeric_literal"]
    return "qualitative", []


def _resolve(declared: ClaimType, inferred: ClaimType, signals: list[str]) -> ClaimClassification:
    mismatch = declared != inferred and _STRENGTH.get(inferred, 0) > _STRENGTH.get(declared, 0)
    resolved = declared
    if declared == "qualitative" and inferred != "qualitative":
        resolved = inferred  # a bare "qualitative" default is safe to specialize
    return ClaimClassification(claim_type=resolved, declared_type=declared, mismatch=mismatch, signals=signals)


async def classify_claim(claim: Claim, *, ctx: SkepticContext | None = None) -> ClaimClassification:
    text = claim.statement or ""
    declared = claim.claim_type
    inferred, signals = _classify_regex(text)

    if ctx is not None and ctx.policies.claim_classification_llm_enabled():
        try:
            from seleric_swarm.agents.skeptic.prompts import CLAIM_TYPE_SYSTEM, claim_type_user

            llm_result = await ctx.deps.reasoning.generate_structured(
                system=CLAIM_TYPE_SYSTEM,
                user=claim_type_user(text),
                schema=_ClaimTypeLLM,
                tags=["skeptic", "claim_classifier"],
            )
            if llm_result.claim_type in _KNOWN_TYPES:
                inferred = llm_result.claim_type  # type: ignore[assignment]
                signals = llm_result.signals or ["llm_classified"]
        except Exception as exc:  # LLM failure must never break classification
            _log.debug("skeptic.claim_classifier.llm_skipped", error=str(exc))

    return _resolve(declared, inferred, signals)
