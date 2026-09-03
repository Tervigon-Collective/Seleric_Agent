"""``seleric.swarm.v1`` message envelope (architecture sec. 26-29).

Inter-agent communication is a small set of machine-readable contracts carried
in the A2A structured ``data`` Part - never free-form chat. The same envelope is
used whether the transport is in-process or A2A-over-HTTP.
"""

from __future__ import annotations

from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

PROTOCOL = "seleric.swarm.v1"


class Intent(str, Enum):
    TASK_REQUEST = "task_request"
    EVIDENCE_REQUEST = "evidence_request"
    ARTIFACT_RESPONSE = "artifact_response"
    CHALLENGE = "challenge"
    CLARIFICATION = "clarification"
    HANDOFF_PROPOSAL = "handoff_proposal"
    HANDOFF_ACCEPT = "handoff_accept"
    HANDOFF_REJECT = "handoff_reject"
    MODEL_REQUEST = "model_request"
    COMPLETION_CANDIDATE = "completion_candidate"


class SwarmMessage(BaseModel):
    protocol: str = PROTOCOL
    mission_id: str
    task_id: str = Field(default_factory=lambda: f"T-{uuid4().hex[:8]}")
    message_id: str = Field(default_factory=lambda: f"MSG-{uuid4().hex[:10]}")
    from_agent: str
    to_agent: str | None = None
    intent: Intent
    objective: str = ""
    scope: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    artifact_refs: list[str] = Field(default_factory=list)
    requested_capabilities: list[str] = Field(default_factory=list)
    expected_artifacts: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
    mission_context: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None

    @classmethod
    def request(
        cls,
        *,
        mission_id: str,
        from_agent: str,
        to_agent: str,
        intent: Intent,
        objective: str = "",
        **kw: Any,
    ) -> SwarmMessage:
        return cls(
            mission_id=mission_id,
            from_agent=from_agent,
            to_agent=to_agent,
            intent=intent,
            objective=objective,
            **kw,
        )


class HandoffProposal(BaseModel):
    """A domain agent's evidence-backed request to move mission leadership."""

    from_agent: str
    to_agent: str
    reason: str
    evidence_refs: list[str] = Field(min_length=1)
    unresolved_question: str
    requested_output: str | None = None
    confidence: float = 0.75

    def to_leadership_proposal(self, mission_id: str) -> dict[str, Any]:
        return {
            "mission_id": mission_id,
            "from_agent": self.from_agent,
            "to_agent": self.to_agent,
            "requested_target": self.to_agent,
            "reason": self.reason,
            "evidence_refs": list(self.evidence_refs),
            "unresolved_question": self.unresolved_question,
            "requested_output": self.requested_output,
        }
