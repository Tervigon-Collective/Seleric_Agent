from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

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
    # Second period for kind="comparison" only — "start"/"end" is period A,
    # "start_b"/"end_b" is period B. Both periods are full ranges (a month,
    # a week, a quarter, ...), not single points; a same-day range (start ==
    # end) still works for a literal day-vs-day ask.
    start_b: str | None = None
    end_b: str | None = None
    relative_token: str | None = None


class CoordinatorClassificationV1(BaseModel):
    query_class: QueryClass
    domain_lead: str
    entities: list[str] = Field(default_factory=list)
    time_range: TimeRangeV1 = Field(default_factory=TimeRangeV1)
    metric_hints: list[str] = Field(default_factory=list)
    unsupported_reason: str | None = None


class MetricMappingV1(BaseModel):
    metric_ids: list[str] = Field(default_factory=list)
    ambiguous: bool = False
    reason: str | None = None


class DimensionMappingV1(BaseModel):
    dimensions: list[str] = Field(default_factory=list)


class TraceInfo(BaseModel):
    # Extra keys (the runner's intent/classification fields) are kept: this
    # model dropped them, and with them ``validation``, so no stored mission
    # showed why a draft was revised (live 2026-10-04: MS3-e18a06b408 revised
    # a correct total away and the record could not say why).
    model_config = ConfigDict(extra="allow")

    request_id: str
    session_id: str
    langfuse_trace_id: str | None = None
    langfuse_trace_url: str | None = None
    langsmith_run_id: str | None = None
    langsmith_run_url: str | None = None
    elapsed_seconds: float | None = None
    # Per-step agent trace (tool calls + args, returns, retries, model text).
    steps: list[dict[str, Any]] | None = None
    # Verdict, trust score and every rejected revision with its reason.
    validation: dict[str, Any] | None = None


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
