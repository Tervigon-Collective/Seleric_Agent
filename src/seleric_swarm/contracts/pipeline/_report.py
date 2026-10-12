"""Report-side pipeline contracts (AG-08): multi-section plans and coverage."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from seleric_swarm.contracts.pipeline._plan import AnalysisPlan
from seleric_swarm.contracts.pipeline._question import (
    EntitySpec,
    FilterSpec,
    RankingSpec,
    Ref,
    WindowSpec,
)

PresentationIntent = Literal["kpis", "trend", "funnel", "ranking", "table", "narrative"]
CoverageStatus = Literal[
    "complete",
    "partial",
    "unsupported",
    "query_failed",
    "unavailable",
    "provisional",
]
CriterionStatus = Literal["explicit", "unspecified", "assumed"]
ExplanationKind = Literal["mathematical", "descriptive", "correlational", "causal"]


class SectionSpec(BaseModel):
    """One analytical objective inside a report, with validated catalogue bindings."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    section_id: str
    objective: str
    shape: str
    measure_candidates: tuple[str, ...] = ()
    metric_ids: tuple[str, ...] = ()
    dimensions: tuple[str, ...] = ()
    time_basis: str | None = None
    population: str | None = None
    filters: tuple[FilterSpec, ...] = ()
    grain: str | None = None
    ranking: RankingSpec | None = None
    criterion_status: CriterionStatus = "unspecified"
    ranking_label: str = ""
    required_evidence: tuple[str, ...] = ()
    presentation_intent: PresentationIntent = "narrative"
    completeness: CoverageStatus = "partial"


class ReportSpec(BaseModel):
    """Shared scope plus independently planned analytical sections."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = 1
    windows: tuple[WindowSpec, ...] = ()
    filters: tuple[FilterSpec, ...] = ()
    entities: tuple[EntitySpec, ...] = ()
    sections: tuple[SectionSpec, ...] = ()
    catalogue_version: str = ""
    follow_up_of: str | None = None
    assumptions: tuple[str, ...] = ()


class SectionPlan(BaseModel):
    """One section's AnalysisPlan with a namespaced step-id prefix (``s1.q1``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    section_id: str
    objective: str
    template_id: str
    plan: AnalysisPlan
    presentation_intent: PresentationIntent = "narrative"
    criterion_status: CriterionStatus = "unspecified"
    ranking_label: str = ""
    needs_agent: bool = False
    unsupported_reason: str = ""


class ReportPlan(BaseModel):
    """Multi-section plan: one or more existing templates under a shared scope."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = 1
    sections: tuple[SectionPlan, ...] = ()
    windows: tuple[WindowSpec, ...] = ()
    filters: tuple[FilterSpec, ...] = ()
    needs_agent: bool = False


class AcquisitionAttempt(BaseModel):
    """One catalogue lookup or metrics_query attempt for coverage accounting."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    section_id: str
    metric_id: str
    args_hash: str = ""
    ok: bool = False
    error_code: str = ""
    rows: int = 0
    latency_ms: float | None = None
    recovered_from: str = ""
    batched: bool = False


class SectionCoverage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    section_id: str
    status: CoverageStatus
    objective: str = ""
    acquired_metrics: tuple[str, ...] = ()
    missing_metrics: tuple[str, ...] = ()
    failed_metrics: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    attempts: tuple[AcquisitionAttempt, ...] = ()


class ReportCoverage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: CoverageStatus
    sections: tuple[SectionCoverage, ...] = ()
    query_calls: int = 0
    cache_hits: int = 0
    attempts: tuple[AcquisitionAttempt, ...] = ()
    limitations: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return self.status == "complete" and all(s.status == "complete" for s in self.sections)


class FunnelStage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    metric_id: str
    counting_basis: str = ""
    view: str = ""
    filters: tuple[FilterSpec, ...] = ()
    status: Literal["ok", "unsupported", "incompatible", "missing"] = "ok"
    note: str = ""


class FunnelDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    stages: tuple[FunnelStage, ...] = ()
    kind: Literal["session", "ad_delivery", "mixed", "unsupported"] = "session"
    disclosures: tuple[str, ...] = ()


class Insight(BaseModel):
    """A grounded diagnostic finding (AG08-07). Numbers come from refs, not prose."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    insight_id: str
    movement: str
    supporting_refs: tuple[Ref, ...] = ()
    segment: str = ""
    contribution: float | None = None
    explanation_kind: ExplanationKind = "descriptive"
    limitation: str = ""
    next_check: str = ""
    section_id: str = ""
