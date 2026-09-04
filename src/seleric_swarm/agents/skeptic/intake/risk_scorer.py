"""Deterministic risk scoring (spec sec. 17).

Produces a 0..1 ``RiskScore`` from eight configurable dimensions plus a discrete
risk class R0..R5. Risk class sets the *depth* of skepticism: the validation
router and blind-review switch both read it. No LLM involvement.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from seleric_swarm.agents.skeptic.contracts import (
    CausalAnalysisArtifact,
    Claim,
    ForecastArtifact,
)
from seleric_swarm.agents.skeptic.policies import SkepticPolicies

_LEVEL = {"low": 0.2, "medium": 0.55, "high": 0.85, "critical": 1.0}
_REVERSIBLE = {"high": 0.1, "medium": 0.5, "low": 0.9, "none": 1.0}


@dataclass
class RiskAssessment:
    score: float
    risk_class: str
    components: dict[str, float]


def _level(value: Any, default: float = 0.5) -> float:
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return max(0.0, min(1.0, float(value)))
    return _LEVEL.get(str(value).lower(), default)


def score_risk(
    claim: Claim,
    *,
    policies: SkepticPolicies,
    causal: list[CausalAnalysisArtifact] | None = None,
    forecasts: list[ForecastArtifact] | None = None,
    risk_context: dict[str, Any] | None = None,
) -> RiskAssessment:
    ctx = {**(risk_context or {}), **claim.metadata}
    weights = policies.risk_weights()

    claim_impact = _level(ctx.get("impact") or ctx.get("claim_impact"), 0.4)

    claim_type_risk = policies.claim_type_risk(claim.claim_type)

    support = len(claim.support_refs)
    evidence_weakness = 1.0 if support == 0 else max(0.0, 1.0 - min(1.0, support / 3.0))
    if claim.claim_type in {"causal", "forecast"} and not (claim.causal_refs or claim.forecast_refs):
        evidence_weakness = max(evidence_weakness, 0.8)

    causal_complexity = 0.0
    if claim.claim_type in {"causal", "correlation"}:
        cc = causal[0] if causal else None
        n_confounders = len(cc.common_causes) if cc else 0
        graph = 1.0 if (cc and cc.graph_id) else 0.0
        causal_complexity = min(1.0, 0.4 + 0.1 * n_confounders + (0.0 if graph else 0.3))

    model_dependency = 0.0
    if claim.claim_type in {"forecast"} or claim.forecast_refs or claim.model_refs:
        model_dependency = 0.8
        fc = forecasts[0] if forecasts else None
        if fc and fc.model_id and fc.model_version:
            model_dependency = 0.5

    irreversibility = 0.0
    if claim.claim_type in {"action", "recommendation"}:
        irreversibility = _REVERSIBLE.get(str(ctx.get("reversibility", "medium")).lower(), 0.5)

    financial_magnitude = _level(ctx.get("financial_magnitude"), 0.3)
    operational_risk = _level(ctx.get("operational_risk"), 0.3)

    components = {
        "claim_impact": claim_impact,
        "claim_type_risk": claim_type_risk,
        "evidence_weakness": evidence_weakness,
        "causal_complexity": causal_complexity,
        "model_dependency": model_dependency,
        "irreversibility": irreversibility,
        "financial_magnitude": financial_magnitude,
        "operational_risk": operational_risk,
    }
    score = round(sum(weights.get(k, 0.0) * v for k, v in components.items()), 4)
    score = max(0.0, min(1.0, score))

    return RiskAssessment(score=score, risk_class=_risk_class(score, claim.claim_type, policies), components=components)


def _risk_class(score: float, claim_type: str, policies: SkepticPolicies) -> str:
    thresholds = policies.risk_class_thresholds()
    level = "R0"
    for name in ("R1", "R2", "R3", "R4", "R5"):
        if score >= float(thresholds.get(name, 1.0)):
            level = name
    floor = policies.min_class_by_type(claim_type)
    if floor and _rank(floor) > _rank(level):
        level = floor
    return level


def _rank(risk_class: str) -> int:
    return int(risk_class[1:]) if risk_class[1:].isdigit() else 0
