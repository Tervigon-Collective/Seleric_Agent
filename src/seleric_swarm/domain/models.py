from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from seleric_swarm.contracts.lookup import TimeRangeV1

TrustLabel = Literal["VERIFIED", "STRONG", "PROBABLE", "WEAK", "INSUFFICIENT"]

# Business State Service contracts (docs/features/business-state-service).
# Frozen at Sprint 0 — no runtime code consumes these yet.
Freshness = Literal["CURRENT", "LATE", "STALE", "UNKNOWN"]
Finality = Literal["INTRADAY", "PROVISIONAL", "FINAL"]
MetricStateStatus = Literal["OK", "PARTIAL", "UNAVAILABLE"]
StateNeed = Literal["actual", "features", "anomaly", "forecast"]
QualityFlag = Literal[
    "MISSING_DATA",
    "STALE",
    "LATE",
    "SPARSE_HISTORY",
    "CATALOGUE_MISS",
    "BINDING_UNSUPPORTED_DIM",
    "PARTIAL_SERIES",
    "MCP_ERROR",
    "INSUFFICIENT_EVIDENCE",
    "CROSS_AXIS_RATIO_UNSUPPORTED",
]


class SeriesPoint(BaseModel):
    ts: str
    value: float | None
    finality: Finality | None = None


class FeatureValue(BaseModel):
    value: float | None
    window: str | None = None
    strategy_version: str | None = None


class StateRequest(BaseModel):
    """Input contract for BusinessStateService.get_metric_state (03 §2)."""

    metric_id: str
    catalogue_metric_id: str | None = None
    time_range: TimeRangeV1
    dimensions: dict[str, Any] = Field(default_factory=dict)
    agent_id: str
    as_of: str | None = None
    profile_id: str = "default_v1"
    need: list[StateNeed] = Field(default_factory=lambda: ["actual"])


class MetricState(BaseModel):
    """Core output type of BusinessStateService.get_metric_state (03 §1)."""

    model_config = ConfigDict(protected_namespaces=())

    metric_id: str
    catalogue_metric_id: str
    as_of: str
    window: dict[str, Any] = Field(default_factory=dict)  # {start, end, grain, timezone}
    dimensions: dict[str, Any] = Field(default_factory=dict)
    actual: float | None = None
    series: list[SeriesPoint] = Field(default_factory=list)
    features: dict[str, FeatureValue] = Field(default_factory=dict)
    baseline: float | None = None
    anomaly: dict[str, Any] | None = None
    forecast: dict[str, Any] | None = None
    freshness: Freshness = "UNKNOWN"
    finality: Finality | None = None
    confidence: float | None = None
    direction_bad: Literal["up", "down"] | None = None
    quality_flags: list[QualityFlag] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
    status: MetricStateStatus = "UNAVAILABLE"
    error_code: str | None = None


class EvidenceArtifact(BaseModel):
    evidence_id: str
    source: str
    metric_or_fact: str
    value: Any
    unit: str | None = None
    dimensions: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)
    quality_flags: list[str] = Field(default_factory=list)
    retrieved_at: str | None = None
    time_range: dict[str, Any] = Field(default_factory=dict)
    freshness: str | None = None


class LeadershipTransfer(BaseModel):
    mission_id: str
    from_agent: str
    requested_target: str
    reason: str
    evidence_refs: list[str] = Field(min_length=1)
    unresolved_question: str
    requested_output: str | None = None


class Claim(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    claim_id: str
    claim_type: Literal["numeric", "causal", "forecast", "recommendation", "qualitative"]
    text: str
    support_refs: list[str] = Field(default_factory=list)
    contradiction_refs: list[str] = Field(default_factory=list)
    trust_label: TrustLabel
    gate_status: str = "pending"
    model_ref: str | None = None
    causal_ref: str | None = None
