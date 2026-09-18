"""Tool precondition thresholds ported from ``config/*_policies.yaml`` (A1.3).

Sprint 2 C disposition: migrate the gate numbers the V3 tools actually
enforce into this module. The five YAML files stay on disk until swarm_v2
callers stop importing them — do not delete YAML as part of the port.
"""

from __future__ import annotations

# From config/diagnostic_policies.yaml — causal budgets / refutation floor.
BASE_CAUSAL_CANDIDATE_CAP: int = 6
HISTORY_DAYS_BASE: int = 30
MIN_REFUTATIONS: int = 2
DEFAULT_CAUSAL_ESTIMATOR: str = "backdoor.linear_regression"
DEFAULT_REFUTERS: tuple[str, ...] = (
    "placebo_treatment_refuter",
    "random_common_cause",
    "data_subset_refuter",
)

# From config/prediction_policies.yaml — history sufficiency (also used by
# anomaly/causal "enough rows" preconditions).
MIN_OBSERVATION_ROWS: int = 8
MIN_HISTORY_DAYS: int = 28

# From config/skeptic_policies.yaml — the EvidenceValidator's two signals
# (Sprint 3 C). An ADDITION, not a move: SkepticPolicies.load() still reads the
# YAML for agents/skeptic/*, so both must change together until that retires.
#
# These two numbers are the reason docs/BUG_SHEET.md #12 is coherent rather than
# a bug: STRONG (0.72) sits well above revise_below (0.55), so a claim can hold
# STRONG trust and still be told to REVISE by a gap/alternative/warning. Moving
# them closer together would quietly couple the two signals.
TRUST_LABEL_THRESHOLDS: dict[str, float] = {
    "INSUFFICIENT": 0.0,
    "WEAK": 0.35,
    "PROBABLE": 0.55,
    "STRONG": 0.72,
    "VERIFIED": 0.9,
}
TRUST_REVISE_BELOW: float = 0.55
TRUST_BLOCKING_CAP: float = 0.3  # trust_score.py:79-82

TRUST_PROFILES: dict[str, dict[str, float]] = {
    "numeric": {
        "source_reliability": 0.25,
        "metric_validity": 0.25,
        "freshness": 0.2,
        "provenance_completeness": 0.2,
        "cross_source_agreement": 0.1,
    },
    "causal": {
        "evidence_quality": 0.15,
        "temporal_validity": 0.15,
        "graph_plausibility": 0.15,
        "confounder_coverage": 0.15,
        "estimator_validity": 0.1,
        "refutation_robustness": 0.15,
        "alternative_elimination": 0.1,
        "cross_source_agreement": 0.05,
    },
    "forecast": {
        "model_applicability": 0.25,
        "backtest_quality": 0.2,
        "drift_status": 0.25,
        "calibration": 0.1,
        "feature_freshness": 0.1,
        "interval_quality": 0.1,
    },
    "default": {
        "evidence_quality": 0.4,
        "provenance_completeness": 0.3,
        "cross_source_agreement": 0.3,
    },
}

# Hard-coded literals in verdict_engine.py (:54, :78) with no YAML backing.
# Naming them here is a small improvement over the original — they are policy,
# and they were invisible.
ALT_PRIORITY_REVISE_FLOOR: int = 6
METHODOLOGY_TRUST_MARGIN: float = 0.15

# verdict_engine.py:58-61, copied exactly. The omissions are deliberate:
# "evidence", "provenance", "alternative_hypothesis" and "strategy" warnings do
# NOT force REVISE. Widening this set silently turns PASSes into REVISEs.
REVISE_CATEGORIES: frozenset[str] = frozenset(
    {
        "metric",
        "source",
        "contradiction",
        "anomaly",
        "statistical",
        "model",
        "forecast",
        "causal",
        "data_quality",
        "temporal",
    }
)

# From config/skeptic_policies.yaml evidence/contradiction blocks.
MAX_FRESHNESS_HOURS: int = 72
CONTRADICTION_TOLERANCE: float = 0.05  # contradiction_validator.py's 5% rule

# Named warnings returned with INSUFFICIENT_EVIDENCE refusals so callers can
# tell *which* gate declined (bug #8's "specialists correctly skipped" property).
WARN_NO_EVIDENCE: str = "policy:no_evidence"
WARN_MISSING_TREATMENT_OUTCOME: str = "policy:missing_treatment_or_outcome_series"
WARN_THIN_HISTORY: str = "policy:thin_history"
WARN_THIN_ROWS: str = "policy:thin_observation_rows"
WARN_MISSING_CAUSAL_ARTIFACT: str = "policy:missing_causal_artifact"

# Model toolset gates (Sprint 3 C).
WARN_NO_APPROVED_MODEL: str = "policy:no_approved_model"
WARN_MODEL_TARGET_MISMATCH: str = "policy:model_target_mismatch"
WARN_MODEL_UNAVAILABLE: str = "policy:model_unavailable"
WARN_MODEL_FIT_FAILED: str = "policy:model_fit_failed"
WARN_INVALID_HORIZON: str = "policy:invalid_horizon"
