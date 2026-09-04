from typing import Any, Literal

from pydantic import BaseModel, Field


class SwarmEnvelope(BaseModel):
    mission_id: str
    task_id: str
    message_id: str
    from_agent: str
    to_agent: str | None = None
    intent: Literal[
        "task_request", "evidence_request", "artifact_response",
        "hypothesis_challenge", "leadership_transfer", "leadership_accept",
        "leadership_reject", "clarification_request", "completion_candidate",
    ]
    evidence_refs: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None
