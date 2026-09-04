"""Derived trust scoring (spec sec. 37-38).

Trust is computed from validator ``score_signals`` against a per-claim-type
weighted profile in ``config/skeptic_policies.yaml``. It never reads any
LLM-reported confidence. Source reliability, where present, is treated as one
contextual signal among several, not as ground truth.
"""

from __future__ import annotations

from dataclasses import dataclass

from seleric_swarm.agents.skeptic.context import SkepticContext, ValidatorOutcome
from seleric_swarm.agents.skeptic.contracts import AlternativeHypothesis, TrustLabel
from seleric_swarm.agents.skeptic.policies import SkepticPolicies

# maps profile dimension -> the validator signal(s) that feed it
_DIMENSION_SIGNALS: dict[str, list[str]] = {
    "evidence_quality": ["evidence_quality", "statistical_strength"],
    "provenance_completeness": ["provenance_completeness"],
    "cross_source_agreement": ["cross_source_agreement"],
    "source_reliability": ["provenance_completeness", "cross_source_agreement"],
    "metric_validity": ["metric_validity"],
    "freshness": ["evidence_quality"],
    "temporal_validity": ["temporal_validity"],
    "graph_plausibility": ["graph_plausibility"],
    "confounder_coverage": ["confounder_coverage"],
    "estimator_validity": ["estimator_validity"],
    "refutation_robustness": ["refutation_robustness"],
    "alternative_elimination": ["_alt_elimination"],
    "model_applicability": ["model_applicability"],
    "backtest_quality": ["backtest_quality"],
    "drift_status": ["drift_status"],
    "calibration": ["forecast_quality"],
    "feature_freshness": ["evidence_quality", "forecast_quality"],
    "interval_quality": ["interval_quality"],
}


@dataclass
class TrustResult:
    score: float
    label: TrustLabel
    components: dict[str, float]


def score_trust(
    ctx: SkepticContext,
    outcomes: list[ValidatorOutcome],
    alternatives: list[AlternativeHypothesis],
    *,
    policies: SkepticPolicies,
) -> TrustResult:
    signals: dict[str, float] = {}
    for oc in outcomes:
        for name, value in oc.score_signals.items():
            # take the min when several validators report the same signal (skeptical)
            signals[name] = min(signals.get(name, 1.0), float(value))

    open_alts = [a for a in alternatives if a.status == "open"]
    signals["_alt_elimination"] = 1.0 if not alternatives else max(0.0, 1.0 - len(open_alts) / max(1, len(alternatives)))

    profile = policies.trust_profile(ctx.claim.claim_type)
    components: dict[str, float] = {}
    weighted_sum = 0.0
    weight_total = 0.0
    for dimension, weight in profile.items():
        feeders = _DIMENSION_SIGNALS.get(dimension, [dimension])
        vals = [signals[f] for f in feeders if f in signals]
        if not vals:
            continue
        val = sum(vals) / len(vals)
        components[dimension] = round(val, 3)
        weighted_sum += weight * val
        weight_total += weight

    score = round(weighted_sum / weight_total, 4) if weight_total else 0.0

    # hard caps: a blocking failure can never score "trustworthy"
    if any(oc.has_blocking for oc in outcomes):
        score = min(score, 0.3)
    score = max(0.0, min(1.0, score))

    return TrustResult(score=score, label=_label(score, policies), components=components)


def _label(score: float, policies: SkepticPolicies) -> TrustLabel:
    thresholds = policies.trust_label_thresholds()
    label: TrustLabel = "INSUFFICIENT"
    for name in ("WEAK", "PROBABLE", "STRONG", "VERIFIED"):
        if score >= float(thresholds.get(name, 1.0)):
            label = name  # type: ignore[assignment]
    return label
