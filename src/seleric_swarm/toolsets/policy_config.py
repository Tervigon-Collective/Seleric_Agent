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

# ---- Sprint 4 C: breakdowns, knowledge, experiments -------------------------

# Funnel step order, declared rather than inferred.
#
# There is no declarative funnel ordering anywhere in this repo: no `step`/
# `order` key in config/metric_registry.yaml, nothing in
# config/diagnostic_ontology.yaml (that file is causal-graph wiring), and
# agents/domains/funnel.py is a 24-line stub. So it is written out here.
#
# These five are chosen because they share ONE denominator. Verified in
# config/metric_registry.yaml:148-215 — every rate is session-anchored:
#
#   metric.sessions        formula: count(sessions)            <- absolute base
#   metric.pdp_view_rate   formula: pdp_sessions / sessions
#   metric.atc_rate        formula: atc_sessions / sessions
#   metric.checkout_rate   formula: checkout_sessions / sessions
#   metric.purchase_cvr    formula: purchased_sessions / sessions
#
# That common denominator is what makes step-to-step conversion well defined:
# the survival rate between consecutive steps is rate[i+1] / rate[i], and
# `metric.sessions` enters as an implicit rate of 1.0. Mixing in a metric with
# a different denominator would silently produce meaningless conversions, so
# do not add one without reworking analytics/funnel.py.
#
# Deriving this order at runtime by parsing `formula` strings would
# reintroduce exactly the name/keyword heuristic layer Profile B deleted in
# Sprint 3 (docs/BUG_SHEET.md #8), and `.formula` degrades to the bare metric
# id on catalogue-bound entries anyway. If the funnel changes, change this list.
FUNNEL_STEPS: tuple[str, ...] = (
    "metric.sessions",
    "metric.pdp_view_rate",
    "metric.atc_rate",
    "metric.checkout_rate",
    "metric.purchase_cvr",
)

# Caution for anyone extending FUNNEL_STEPS. Two real traps:
#   * `metric.atc_to_purchase_rate` is MISNAMED — its formula is
#     `purchased_sessions / checkout_sessions` and its catalogue id is
#     `session_checkout_to_purchase_rate`. The name says ATC, the math says
#     checkout. Trust the formula, not the id.
#   * `metric.session_atc_to_checkout_rate` is `checkout_sessions /
#     atc_sessions` — ATC-anchored, not session-anchored. Adding it here would
#     break the shared-denominator property above.

# Shares below this are folded into an explicit "other" bucket rather than
# listed individually — a 200-row channel breakdown is not a finding.
MIN_SEGMENT_SHARE: float = 0.01
MAX_SEGMENTS_REPORTED: int = 20

# Contribution shares are derived by summing drilldown children, because
# toolsets/semantic.py::drilldown discards the parent total it computes. If the
# children disagree with a supplied total by more than this, say so rather than
# presenting the shares as exact.
CONTRIBUTION_RECONCILIATION_TOLERANCE: float = 0.005

WARN_NO_DIMENSION_EVIDENCE: str = "policy:no_dimension_stamped_evidence"
WARN_POOLED_SEGMENTS: str = "policy:pooled_segment_evidence"
WARN_CONTRIBUTION_UNRECONCILED: str = "policy:contribution_unreconciled"
WARN_UNKNOWN_FUNNEL_STEP: str = "policy:unknown_funnel_step"
WARN_NO_FUNNEL_STEPS: str = "policy:no_recognized_funnel_steps"
WARN_SINGLE_COHORT: str = "policy:single_cohort"

# Knowledge toolset (Sprint 4 C). The corpus ships empty; an empty corpus is a
# miss, not an error — CONTRACTS.md §2 permits Knowledge to return
# success=True with zero artifacts.
KNOWLEDGE_CORPUS_DIRNAME: str = "knowledge_corpus"
KNOWLEDGE_MAX_RESULTS: int = 5
KNOWLEDGE_SNIPPET_CHARS: int = 400
WARN_EMPTY_CORPUS: str = "policy:empty_knowledge_corpus"

# Experiment toolset (Sprint 4 C).
# 0.8 power / 0.05 alpha are the conventional defaults; named here so a
# refusal can cite them rather than hiding a literal in the call site.
DEFAULT_POWER: float = 0.8
DEFAULT_ALPHA: float = 0.05
MIN_DETECTABLE_EFFECT: float = 0.001  # below this, sample size explodes meaninglessly
WARN_UNKNOWN_EXPERIMENT: str = "policy:unknown_experiment"
WARN_NO_EXPERIMENT_RECORDS: str = "policy:no_experiment_records"
WARN_MISSING_VARIANT_EVIDENCE: str = "policy:missing_variant_evidence"
WARN_INVALID_RATE: str = "policy:invalid_rate"
