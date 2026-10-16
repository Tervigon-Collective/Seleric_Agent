"""What each stage of the diagnosis hands to the next (plan P2.1).

The stages run in the order the engine always ran them: IS_REAL (is the change real, how big), WHAT (exact accounting:
decomposition, bridge, chain), WHERE (segment localisation), WHY_EVENT (driver estimates), then the report is
assembled. Each stage reads the results before it and returns its own; none changes another's."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from seleric_swarm.diagnosis.domain.identity import Identity
from seleric_swarm.diagnosis.domain.inputs import DiagnosisInput
from seleric_swarm.diagnosis.domain.lineage import MetricMeta
from seleric_swarm.diagnosis.domain.numeric import Series
from seleric_swarm.diagnosis.domain.report import (
    BridgeTerm,
    DecompositionTerm,
    DiagnosisReport,
    DimensionFinding,
    DriverFinding,
    EventSummary,
)
from seleric_swarm.diagnosis.domain.window import EventWindow


@dataclass
class EventAnalysis:
    """The event, its reference and size: everything the later stages need to know about the question."""

    inp: DiagnosisInput
    y: str
    ys: Series
    event_days: list[date]
    window_label: list[str]
    n_ev: int
    start: date
    history: list[date]
    baseline: list[date]
    meta_y: MetricMeta
    weight_metric: str | None
    window: EventWindow
    same_weekday: bool
    outliers: set[date]
    ref_kind: str
    identities: list[Identity]
    identity: Identity | None
    y_ev: float
    y_rf: float
    delta: float
    unusual: bool
    notable: bool
    prev_days: list[date]
    previous: dict[str, Any]
    quality: list[str]
    event: EventSummary
    assumptions: list[str] = field(default_factory=list)


@dataclass
class EventResult:
    """Either the analysis, or the report that says why there is nothing to analyse (insufficient data)."""

    analysis: EventAnalysis | None = None
    early: DiagnosisReport | None = None


@dataclass
class WhatResult:
    decomposition: list[DecompositionTerm] = field(default_factory=list)
    bridge: list[BridgeTerm] = field(default_factory=list)
    bridge_identity: list[str] = field(default_factory=list)
    bridge_residual: float | None = None
    bridge_residual_vs_previous: float | None = None
    chain_identity: str = ""
    chain: list[DecompositionTerm] = field(default_factory=list)
    chain_dimensions: list[DimensionFinding] = field(default_factory=list)


@dataclass
class WhyResult:
    drivers: list[DriverFinding] = field(default_factory=list)
    implicated: list[DriverFinding] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
