from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

TrustLabel = Literal["VERIFIED", "STRONG", "PROBABLE", "WEAK", "INSUFFICIENT"]


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
