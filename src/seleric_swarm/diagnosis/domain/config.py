"""The investigation's typed, versioned configuration (plan P1.11, D-19, D-34, D-36).

Every number the engine decides with lives here, is written into the run record, and is fingerprinted, so a replay
can prove it ran with the same settings. The statistical conventions start as copies of ``DIAG_*`` in
``toolsets/policy_config.py`` (a parity test keeps the two equal while both exist); Phase 2 makes this module the
only source."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, fields, replace
from typing import Any

from seleric_swarm.diagnosis.domain.canonical import fingerprint
from seleric_swarm.diagnosis.domain.errors import DiagnosisError

CONFIG_SCHEMA_VERSION = 1
# Calendar words in a catalogue grain (``brand_order_day``): time buckets, not units.
DEFAULT_CALENDAR_GRAIN_TOKENS: tuple[str, ...] = ("hour", "day", "week", "month", "quarter", "year")
MULTIPLE_TESTING_METHODS = frozenset({"fdr_by", "fdr_bh"})


class InvalidConfig(DiagnosisError):
    code = "invalid_config"


@dataclass(frozen=True)
class Bounds:
    """Safety bounds of one investigation (D-19). Hitting one is reported as such, never hidden."""

    max_depth: int = 4
    max_branching: int = 3
    max_operator_runs: int = 60
    max_causal_queries: int = 400
    max_wall_clock_s: float = 1200.0


@dataclass(frozen=True)
class StopRule:
    """Stop when the unexplained share improves by less than ``min_improvement_points`` for ``consecutive_rounds`` rounds."""

    min_improvement_points: float = 5.0
    consecutive_rounds: int = 2


@dataclass(frozen=True)
class DiagnosisConfig:
    schema_version: int = CONFIG_SCHEMA_VERSION
    # statistical conventions (copies of toolsets/policy_config.DIAG_* and MIN_OBSERVATION_ROWS)
    history_days: int = 56
    reference_weeks: int = 4
    event_z: float = 2.0
    event_z_notable: float = 1.5
    outlier_z: float = 3.0
    identity_tolerance: float = 0.01
    bridge_tolerance: float = 0.005
    bridge_max_terms: int = 6
    bridge_budget_s: float = 8.0
    bridge_depth: int = 4
    approx_identity_tolerance: float = 0.1
    comeasure_cv: float = 0.02
    treatment_cluster_r: float = 0.9
    alpha: float = 0.05
    refuter_tolerance: float = 0.25
    max_drivers: int = 8
    max_dimensions: int = 16
    max_segments_reported: int = 5
    lever_depth: int = 2
    lever_max_volumes: int = 10
    lever_max_rates: int = 12
    lever_segments: int = 2
    min_rows_per_covariate: int = 3
    placebo_shifts: int = 60
    broad_based_max: float = 0.3
    comeasure_r: float = 0.95
    unit_count_r: float = 0.9
    min_dimension_coverage: float = 0.9
    calendar_grain_tokens: tuple[str, ...] = DEFAULT_CALENDAR_GRAIN_TOKENS
    min_observation_rows: int = 8  # history rows needed before any fit, identity or estimate is attempted
    # two measurements of one quantity (graph co-measure pairs) have diverged when their ratio left its usual level by
    # more than ``measurement_z`` of its own day-to-day spread AND by more than ``measurement_min_gap`` in relative terms
    # (calibrated on 90 days of a real brand: the noisiest pair's spread is 17%, its largest daily deviation 3.5 spreads)
    measurement_z: float = 5.0
    measurement_min_gap: float = 0.25
    measurement_min_scale: float = 0.02  # floor for the spread of a ratio that is constant on the history (log units)
    # False: a divergence is recorded on the report and the trace but changes no label and says nothing to the user.
    # Off until it is calibrated on live traffic: a co-measure pair fed by a lagging pipeline (the P&L feed) differs on the
    # latest days for a reason that is not a tracking loss (P3.2, plan §11).
    measurement_acts: bool = False
    # investigation
    bounds: Bounds = field(default_factory=Bounds)
    stop: StopRule = field(default_factory=StopRule)
    multiple_testing: str = "fdr_by"
    payload_cap_bytes: int = 4096

    def __post_init__(self) -> None:
        if self.schema_version != CONFIG_SCHEMA_VERSION:
            raise InvalidConfig(f"config schema {self.schema_version}, this engine reads {CONFIG_SCHEMA_VERSION}")
        if not 0.0 < self.alpha < 1.0:
            raise InvalidConfig(f"alpha must be between 0 and 1, got {self.alpha}")
        if self.multiple_testing not in MULTIPLE_TESTING_METHODS:
            raise InvalidConfig(f"multiple_testing must be one of {sorted(MULTIPLE_TESTING_METHODS)}")
        positive = {
            "history_days": self.history_days, "reference_weeks": self.reference_weeks,
            "min_observation_rows": self.min_observation_rows,
            "payload_cap_bytes": self.payload_cap_bytes, "max_depth": self.bounds.max_depth,
            "max_branching": self.bounds.max_branching, "max_operator_runs": self.bounds.max_operator_runs,
            "max_causal_queries": self.bounds.max_causal_queries, "max_wall_clock_s": self.bounds.max_wall_clock_s,
            "consecutive_rounds": self.stop.consecutive_rounds,
        }
        bad = sorted(name for name, value in positive.items() if value <= 0)
        if bad:
            raise InvalidConfig(f"must be positive: {bad}")

    def with_changes(self, **changes: Any) -> DiagnosisConfig:
        return replace(self, **changes)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["calendar_grain_tokens"] = list(self.calendar_grain_tokens)
        return data

    def fingerprint(self) -> str:
        return fingerprint(self.to_dict())

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> DiagnosisConfig:
        known = {f.name for f in fields(cls)}
        unknown = sorted(set(raw) - known)
        if unknown:
            raise InvalidConfig(f"unknown config keys: {unknown}")
        data = dict(raw)
        if "bounds" in data:
            data["bounds"] = Bounds(**data["bounds"])
        if "stop" in data:
            data["stop"] = StopRule(**data["stop"])
        if "calendar_grain_tokens" in data:
            data["calendar_grain_tokens"] = tuple(data["calendar_grain_tokens"])
        return cls(**data)
