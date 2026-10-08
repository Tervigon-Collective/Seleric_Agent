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

# ---- Diagnosis engine (causal/diagnosis.py) ---------------------------------
# Statistical conventions only. Nothing here names a metric, dimension, channel
# or business threshold: every candidate cause, identity and DAG edge comes from
# catalogue lineage plus the data, and these numbers only decide how much
# evidence is "enough".
DIAG_HISTORY_DAYS: int = 56            # daily history fetched before the event
DIAG_REFERENCE_WEEKS: int = 4          # same-weekday reference days per event day
DIAG_EVENT_Z: float = 2.0              # |z| at/above which the event is unusual
DIAG_EVENT_Z_NOTABLE: float = 1.5      # |z| at/above which it is notable (diagnosed, flagged moderate)
DIAG_OUTLIER_Z: float = 3.0            # robust z marking a history day abnormal (excluded from references)
DIAG_IDENTITY_TOLERANCE: float = 0.01  # relative dispersion allowed in a verified identity
# Additive accounting bridge (outcome = signed sum of same-unit additive metrics):
# every history day must reproduce within this share of the outcome's typical size.
DIAG_BRIDGE_TOLERANCE: float = 0.005
DIAG_BRIDGE_MAX_TERMS: int = 6         # terms in one level of the bridge
DIAG_BRIDGE_BEAM: int = 16             # partial sums kept per search step
DIAG_BRIDGE_BUDGET_S: float = 8.0       # wall-clock cap on the whole bridge search
DIAG_BRIDGE_DEPTH: int = 4             # levels: outcome -> terms -> their terms -> …
DIAG_APPROX_IDENTITY_TOLERANCE: float = 0.1  # ... in an approximate (cross-system) identity
DIAG_COMEASURE_CV: float = 0.02        # X/Y this stable => same quantity, not a cause
DIAG_TREATMENT_CLUSTER_R: float = 0.9  # |r| grouping candidate treatments as inseparable
DIAG_ALPHA: float = 0.05               # CI / test level
DIAG_REFUTER_TOLERANCE: float = 0.25   # relative effect drift allowed by stability refuters
DIAG_MAX_DRIVERS: int = 8              # upstream candidates estimated (search_breadth adds)
DIAG_MAX_DIMENSIONS: int = 16          # dimensions screened for segment localisation
DIAG_MAX_SEGMENTS_REPORTED: int = 5
DIAG_MIN_ROWS_PER_COVARIATE: int = 3   # estimation rows needed per regressor
DIAG_PLACEBO_SHIFTS: int = 60          # circular-shift placebo draws
DIAG_BROAD_BASED_MAX: float = 0.3      # segment-specificity at/below which a change is broad-based
DIAG_COMEASURE_R: float = 0.95         # normal-day lockstep with the outcome => same units, not a cause
DIAG_MIN_DIMENSION_COVERAGE: float = 0.9  # share of the outcome a dimension's segments must account for
# Calendar words in a catalogue grain (``brand_order_day``): time buckets, not units.
DIAG_CALENDAR_GRAIN_TOKENS: tuple[str, ...] = ("hour", "day", "week", "month", "quarter", "year")

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
# agent/limits.py::ExecutionBudgetTracker exhaustion (max_cube_queries/
# max_causal_queries/max_prediction_calls) surfaced as a normal tool refusal.
WARN_EXECUTION_LIMIT_EXCEEDED: str = "policy:execution_limit_exceeded"

# Model toolset gates (Sprint 3 C).
WARN_NO_APPROVED_MODEL: str = "policy:no_approved_model"
WARN_MODEL_TARGET_MISMATCH: str = "policy:model_target_mismatch"
WARN_MODEL_UNAVAILABLE: str = "policy:model_unavailable"
WARN_MODEL_FIT_FAILED: str = "policy:model_fit_failed"
WARN_INVALID_HORIZON: str = "policy:invalid_horizon"

# ---- Sprint 4 C: breakdowns, knowledge, experiments -------------------------

# Funnel membership and order are NOT declared here.
# A funnel's stages belong to the catalogue, which types each metric as a
# count or a ratio; analytics/funnel.py derives the base stage and the step
# order from that typing plus the measured rates. Listing metric ids here
# would pin one funnel's shape into the harness and silently exclude any
# other funnel the catalogue grows.

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

# ---- Exploration engine (exploration/engine.py) -----------------------------
# Statistical conventions for open-ended exploration. As with the diagnosis
# numbers above, nothing here names a metric, dimension or business threshold:
# they decide how much evidence makes a pattern worth reporting.
EXPLORE_FDR: float = 0.10                 # Benjamini-Hochberg level across every test a run performs
EXPLORE_HISTORY_WINDOWS: int = 6          # equal-length windows before the current one (the "normal" variation)
EXPLORE_MAX_SPAN_DAYS: int = 400          # cap on fetched history (windows x window length)
EXPLORE_DEFAULT_WINDOW_DAYS: int = 7      # window when the question names no period
EXPLORE_MAX_WINDOW_DAYS: int = 92
EXPLORE_MIN_RELATIVE_CHANGE: float = 0.03  # smallest window-over-window change worth reporting
EXPLORE_MIN_TREND_PER_WEEK: float = 0.01   # smallest trend (share of level per week) worth reporting
EXPLORE_MIN_SHIFT: float = 0.05            # smallest level shift worth reporting
EXPLORE_MIN_SHARE_MOVE: float = 0.02     # smallest change in a segment's share of the total worth reporting
EXPLORE_MIN_TOP_SHARE: float = 0.3         # an "outstanding" segment must carry this share of the total
EXPLORE_MIN_ABS_CORRELATION: float = 0.4   # weakest day-to-day co-movement worth reporting
EXPLORE_ADTRIBUTOR_T_EEP: float = 0.1      # per-segment share of the change (Adtributor T_EEP)
EXPLORE_ADTRIBUTOR_T_EP: float = 0.67      # cumulative share the set must explain (Adtributor T_EP)
EXPLORE_MAX_SEGMENT_ROWS: int = 6000       # skip a breakdown whose daily rows exceed this
# Ranking prior per insight kind: what moved now outranks a structural fact the
# operator already knows ("the biggest channel is the biggest").
EXPLORE_KIND_PRIOR: dict[str, float] = {
    "period_change": 1.0,
    "distribution_shift": 1.0,
    "change_point": 0.9,
    "trend": 0.8,
    "co_movement": 0.6,
    "outstanding": 0.5,
}
# depth -> (headline metrics, dimensions per metric, insights reported)
EXPLORE_DEPTH: dict[str, tuple[int, int, int]] = {
    "scan": (3, 2, 5),
    "focus": (5, 3, 8),
    "deep": (8, 5, 12),
}
