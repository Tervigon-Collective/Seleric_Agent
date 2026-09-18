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

# Named warnings returned with INSUFFICIENT_EVIDENCE refusals so callers can
# tell *which* gate declined (bug #8's "specialists correctly skipped" property).
WARN_NO_EVIDENCE: str = "policy:no_evidence"
WARN_MISSING_TREATMENT_OUTCOME: str = "policy:missing_treatment_or_outcome_series"
WARN_THIN_HISTORY: str = "policy:thin_history"
WARN_THIN_ROWS: str = "policy:thin_observation_rows"
WARN_MISSING_CAUSAL_ARTIFACT: str = "policy:missing_causal_artifact"
