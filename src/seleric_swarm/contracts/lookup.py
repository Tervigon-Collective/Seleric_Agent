from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

QueryClass = Literal["lookup", "comparison", "unsupported"]
MissionStatus = Literal[
    "completed",
    "prototype_completed",
    "partial",
    "blocked",
    "failed",
    "running",
    "cancelled",
]
ErrorCode = Literal[
    "TIMEOUT",
    "LLM_UNAVAILABLE",
    "INSUFFICIENT_EVIDENCE",
    "CLAIM_REJECTED",
    "ROUTING_UNSUPPORTED",
    "BUDGET_EXCEEDED",
    "INVALID_REQUEST",
    "HANDOFF_REJECTED",
]


class TimeRangeV1(BaseModel):
    kind: Literal["absolute", "relative", "comparison", "none"] = "none"
    start: str | None = None
    end: str | None = None
    relative_token: str | None = None


class CoordinatorClassificationV1(BaseModel):
    query_class: QueryClass
    domain_lead: str
    entities: list[str] = Field(default_factory=list)
    time_range: TimeRangeV1 = Field(default_factory=TimeRangeV1)
    metric_hints: list[str] = Field(default_factory=list)
    unsupported_reason: str | None = None


class MetricMappingV1(BaseModel):
    metric_id: str | None = None
    ambiguous: bool = False
    reason: str | None = None


class TraceInfo(BaseModel):
    request_id: str
    session_id: str
    langsmith_run_id: str | None = None
    langsmith_run_url: str | None = None


class MissionError(BaseModel):
    code: str
    message: str


class ClaimView(BaseModel):
    claim_id: str
    claim_type: str
    text: str
    support_refs: list[str] = Field(default_factory=list)
    trust_label: str
    gate_status: str = "pending"


class EvidenceView(BaseModel):
    evidence_id: str
    metric_or_fact: str
    value: Any
    unit: str | None = None
    time_range: dict[str, Any] = Field(default_factory=dict)
    source: str
    freshness: str | None = None
    dimensions: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)


class HandoffView(BaseModel):
    from_agent: str | None = None
    to_agent: str | None = None
    requested_target: str | None = None
    reason: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    unresolved_question: str | None = None
    requested_output: str | None = None
    epoch: int | None = None


class MissionResult(BaseModel):
    mission_id: str
    status: MissionStatus
    query_class: str | None = None
    mission_lead: str | None = None
    initial_mission_lead: str | None = None
    active_specialist: str | None = None
    leadership_epoch: int = 0
    handoff_history: list[HandoffView] = Field(default_factory=list)
    claims: list[ClaimView] = Field(default_factory=list)
    evidence: list[EvidenceView] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    final_response: str | None = None
    error: MissionError | None = None
    trace: TraceInfo
