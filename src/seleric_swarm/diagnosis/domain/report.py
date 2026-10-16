"""The diagnosis report and its parts: the event, exact decomposition terms, bridge terms, segment findings and driver
findings, with the four classifications and the verdicts (moved from ``causal/diagnosis.py``, plan P2.2; D-11)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Classification = Literal["supported_cause", "likely_contributor", "correlation", "insufficient_evidence"]


Direction = Literal["down", "up"]


@dataclass
class EventSummary:
    metric: str
    actual: float | None
    reference: float | None
    delta: float | None
    delta_pct: float | None
    expected_by_model: float | None
    z_score: float | None
    unusual: bool
    direction: Direction | None
    # vs_previous_only: the asked direction holds against the day before but not
    # against the usual for that weekday (live 2026-10-05 MS3-fbcf78e410: "why did
    # sales fall yesterday" -> "did not fall, it rose" while sales were 25% below
    # the previous day).
    premise: Literal["confirmed", "contradicted", "not_unusual", "not_stated", "vs_previous_only"]
    strength: Literal["strong", "moderate", "none"]
    previous_period: dict[str, Any]
    reference_kind: str
    reference_days: dict[str, list[str]]
    excluded_outlier_days: list[str]
    aggregation_note: str = ""
    sampling: dict[str, Any] = field(default_factory=dict)
    level_factors: dict[str, dict[str, float]] = field(default_factory=dict)


@dataclass
class DecompositionTerm:
    metric: str
    exponent: float
    event: float
    reference: float
    contribution: float
    share_of_change: float | None
    basis: str = "identity"


@dataclass
class BridgeTerm:
    """One term of the exact additive bridge: ``parent = Σ sign × metric``."""

    metric: str
    parent: str
    sign: float
    depth: int
    event: float
    reference: float
    contribution: float  # sign × (event − reference): its signed share of the parent's change
    share_of_change: float | None  # of the OUTCOME's change vs the reference
    previous: float | None = None
    contribution_vs_previous: float | None = None


@dataclass
class SegmentMove:
    segment: str
    event: float
    reference: float
    delta: float
    share_of_change: float | None
    rate_event: float | None = None
    rate_reference: float | None = None
    rate_effect: float | None = None
    mix_effect: float | None = None
    z_score: float | None = None
    significant: bool | None = None
    volume_share_event: float | None = None
    volume_share_reference: float | None = None
    numerator_event: float | None = None
    numerator_reference: float | None = None


@dataclass
class DimensionFinding:
    dimension: str
    kind: Literal["additive", "rate", "ratio"]
    n_segments: int
    coverage: float | None
    specificity: float | None
    typical_specificity: float | None
    localised: bool
    broad_based: bool
    top: list[SegmentMove]
    mix_effect: float | None = None
    rate_effect: float | None = None
    simpsons_paradox: bool = False
    note: str = ""
    volume_coverage: float | None = None  # share of the outcome's reference the segments add up to
    hot_share: float | None = None  # share of the change carried by segments beyond their own noise
    numerator: str | None = None  # kind "ratio": the metrics the ratio divides
    denominator: str | None = None


@dataclass
class DriverFinding:
    driver: str
    classification: Classification | None
    status: Literal["implicated", "ruled_out", "excluded", "insufficient_evidence"]
    reason: str
    effect_per_unit: float | None = None
    effect_ci: list[float] = field(default_factory=list)
    p_value: float | None = None
    driver_event: float | None = None
    driver_reference: float | None = None
    driver_delta: float | None = None
    driver_z: float | None = None
    driver_moved_before_or_at_event: bool | None = None
    contribution: float | None = None
    contribution_ci: list[float] = field(default_factory=list)
    share_of_change: float | None = None
    causal_path: str = ""
    adjustment_set: list[str] = field(default_factory=list)
    graph_edges: list[list[str]] = field(default_factory=list)  # the DAG DoWhy identified the effect on
    identified_estimand: str = ""
    direction_evidence: dict[str, Any] = field(default_factory=dict)
    refutations: list[dict[str, Any]] = field(default_factory=list)
    naive_association: dict[str, Any] = field(default_factory=dict)
    inseparable_from: list[str] = field(default_factory=list)
    ruled_out_because: Literal["", "no_effect", "did_not_move", "wrong_direction"] = ""
    n_rows: int = 0
    estimator: str = ""
    assumptions: list[str] = field(default_factory=list)


@dataclass
class DiagnosisReport:
    outcome: str
    event_window: list[str]
    verdict: Literal[
        "no_unusual_change", "explained", "partially_explained", "located_cause_not_identified",
        "root_cause_not_identified", "insufficient_data",
    ]
    headline: str
    event: EventSummary | None
    identities: list[str] = field(default_factory=list)
    decomposition: list[DecompositionTerm] = field(default_factory=list)
    # Exact additive bridge (accounting identity), every level, data-verified.
    bridge_identity: list[str] = field(default_factory=list)
    bridge: list[BridgeTerm] = field(default_factory=list)
    bridge_residual: float | None = None
    bridge_residual_vs_previous: float | None = None
    # Next link down: the dominant additive component rebuilt as rate x base
    # (approximate, data-verified), and where that rate moved.
    chain_identity: str = ""
    chain: list[DecompositionTerm] = field(default_factory=list)
    chain_dimensions: list[DimensionFinding] = field(default_factory=list)
    dimensions: list[DimensionFinding] = field(default_factory=list)
    drivers: list[DriverFinding] = field(default_factory=list)
    competing_hypotheses: list[dict[str, Any]] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    data_quality: list[str] = field(default_factory=list)
    unexplained_share: float | None = None
    # Plain-language answer skeleton: every required part, catalogue labels, no ids.
    narrative: list[str] = field(default_factory=list)
    # The day(s) just before the event, for every metric the report cites.
    previous_values: dict[str, float] = field(default_factory=dict)
    # The same metrics on the event day(s). Without them an answer tabulating
    # "yesterday vs the day before" had the day before for every driver and
    # printed "No data available" for yesterday (live 2026-10-05 MS3-5775d28dfd).
    event_values: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        from dataclasses import asdict

        return asdict(self)
