"""Trust signal — ported from ``agents/skeptic/scoring/trust_score.py``.

This is signal **one of two**. ``verdict.py`` is the other, and they must stay
independent: ``docs/BUG_SHEET.md`` #12 records ``trust_label: STRONG`` alongside
``verdict: REVISE`` as intentional, coherent design — "the evidence we have is
solid, but there's an unresolved competing explanation we haven't ruled out
yet". Collapsing them into one number destroys that distinction, which is why
``03_PROFILE_CAPABILITIES.md`` §9 makes the two-signal structure an exit
criterion.

The arithmetic below is a faithful port, not a re-derivation — this is where
the regression risk lives. Preserved exactly:

* duplicate signals across checks merge by **min**, not mean (deliberately
  skeptical: one validator's low reading is not averaged away);
* ``_alt_elimination`` is a synthetic signal, 1.0 when there are no
  alternatives at all;
* a profile dimension with no feeding signal is **skipped and dropped from the
  weight total**, so the remaining weights renormalize rather than the missing
  dimension scoring zero;
* the blocking cap (0.3) is applied *after* the weighted average.

The blocking cap is the only place verdict-shaped information touches trust, and
it is one-directional: blocking → trust capped, never blocking → verdict.

Thresholds and profiles live in ``toolsets/policy_config.py`` (values copied
from ``config/skeptic_policies.yaml``, which stays on disk for
``agents/skeptic/*``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from seleric_swarm.toolsets import policy_config as policy

if TYPE_CHECKING:
    from seleric_swarm.agent.validation.signals import (
        AlternativeHypothesis,
        CheckOutcome,
        ClaimType,
    )

TrustLabel = Literal["VERIFIED", "STRONG", "PROBABLE", "WEAK", "INSUFFICIENT"]

# Profile dimension -> the check signal(s) that feed it. Verbatim from
# trust_score.py:18-37. Dimensions whose feeders no V3 check emits simply never
# contribute; see signals.py on why that is correct rather than a gap.
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
    components: dict[str, float] = field(default_factory=dict)


def score_trust(
    outcomes: list[CheckOutcome],
    alternatives: list[AlternativeHypothesis],
    *,
    claim_type: ClaimType = "default",
) -> TrustResult:
    """Weighted trust over check signals. Never reads a verdict, never reads an
    LLM-reported confidence (the original's docstring rule, kept)."""
    signals: dict[str, float] = {}
    for oc in outcomes:
        for name, value in oc.score_signals.items():
            # min, not mean -- one low reading is not averaged away
            signals[name] = min(signals.get(name, 1.0), float(value))

    open_alts = [a for a in alternatives if a.status == "open"]
    signals["_alt_elimination"] = (
        1.0 if not alternatives else max(0.0, 1.0 - len(open_alts) / max(1, len(alternatives)))
    )

    profile = policy.TRUST_PROFILES.get(claim_type, policy.TRUST_PROFILES["default"])
    components: dict[str, float] = {}
    weighted_sum = 0.0
    weight_total = 0.0
    for dimension, weight in profile.items():
        feeders = _DIMENSION_SIGNALS.get(dimension, [dimension])
        vals = [signals[f] for f in feeders if f in signals]
        if not vals:
            # Dimension has no feeder: drop it AND its weight, so the rest
            # renormalize. Scoring it 0 would punish a claim for a check V3
            # cannot perform.
            continue
        val = sum(vals) / len(vals)
        components[dimension] = round(val, 3)
        weighted_sum += weight * val
        weight_total += weight

    score = round(weighted_sum / weight_total, 4) if weight_total else 0.0

    # Hard cap: a blocking failure can never score "trustworthy".
    if any(oc.has_blocking for oc in outcomes):
        score = min(score, policy.TRUST_BLOCKING_CAP)
    score = max(0.0, min(1.0, score))

    return TrustResult(score=score, label=label_for(score), components=components)


def label_for(score: float) -> TrustLabel:
    """Walk the ladder low->high, keeping the last threshold passed."""
    label: TrustLabel = "INSUFFICIENT"
    for name in ("WEAK", "PROBABLE", "STRONG", "VERIFIED"):
        if score >= float(policy.TRUST_LABEL_THRESHOLDS.get(name, 1.0)):
            label = name  # type: ignore[assignment]
    return label
