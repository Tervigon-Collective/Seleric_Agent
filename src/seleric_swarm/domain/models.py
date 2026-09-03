from __future__ import annotations
from typing import Any, Literal
from pydantic import BaseModel, Field

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


class LeadershipTransfer(BaseModel):
    mission_id: str
    from_agent: str
    requested_target: str
    reason: str
    evidence_refs: list[str]
    unresolved_question: str
    requested_output: str | None = None


class Claim(BaseModel):
    claim_id: str
    claim_type: Literal["numeric", "causal", "forecast", "recommendation", "qualitative"]
    text: str
    support_refs: list[str] = Field(default_factory=list)
    contradiction_refs: list[str] = Field(default_factory=list)
    trust_label: TrustLabel
