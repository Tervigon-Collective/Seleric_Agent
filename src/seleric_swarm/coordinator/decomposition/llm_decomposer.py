"""LLM-based mission decomposition — replaces the static TEMPLATES/select_template
keyword dispatch in ``decomposition.templates``. Mirrors
``coordinator.intake.llm_classifier``: returns ``None`` when the LLM path
isn't usable so callers fall back to the offline templates.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

from pydantic import BaseModel, Field

from seleric_swarm.llm.errors import LLMError, LLMStructuredOutputError
from seleric_swarm.llm.port import ChatMessage, LLMRequest, LLMRequestMetadata

if TYPE_CHECKING:
    from seleric_swarm.coordinator.contracts import NormalizedQuery
    from seleric_swarm.runtime import SwarmRuntime

AGENT_VERSION = "0.1.0"


class DecompositionStepV1(BaseModel):
    purpose: str
    question: str
    priority: int = Field(ge=1, le=10, default=5)
    branch: str | None = None


class MissionDecompositionV1(BaseModel):
    steps: list[DecompositionStepV1] = Field(default_factory=list)


async def decompose_mission_via_llm(
    normalized: "NormalizedQuery",
    *,
    runtime: "SwarmRuntime",
    mission_id: str | None = None,
    request_id: str | None = None,
    session_id: str | None = None,
    agent_id: str = "coordinator_agent",
) -> list[dict[str, Any]] | None:
    """Return decomposition steps via the LLM, or ``None`` when unusable —
    callers fall back to the static templates."""
    try:
        spec = runtime.prompts.load("coordinator.decompose_mission")
    except Exception:
        return None

    user = spec.render_user(
        {
            "query": normalized.original_query,
            "intents": ", ".join(normalized.intents) or "none",
            "primary_metric": normalized.primary_metric or "none",
            "entities": ", ".join(e.entity_id for e in normalized.entities) or "none",
            "candidate_domains": ", ".join(normalized.candidate_domains) or "none",
        }
    )
    request = LLMRequest(
        messages=[
            ChatMessage(role="system", content=spec.system),
            ChatMessage(role="user", content=user),
        ],
        model=spec.model,
        temperature=spec.temperature,
        max_tokens=spec.max_tokens,
        timeout_s=runtime.settings.llm_timeout_s,
        metadata=LLMRequestMetadata(
            request_id=request_id or uuid4().hex,
            session_id=session_id or uuid4().hex,
            mission_id=mission_id or uuid4().hex,
            agent_id=agent_id,
            agent_version=runtime.agents.version(agent_id, AGENT_VERSION),
            prompt_id=spec.id,
            prompt_version=spec.version,
            workflow_name=runtime.settings.workflow_name,
            workflow_version=runtime.settings.workflow_version,
            model=spec.model,
        ),
        tags=["coordinator", "decompose_mission", spec.id],
    )
    try:
        result = await runtime.llm.complete_structured(request, MissionDecompositionV1)
    except (LLMStructuredOutputError, LLMError):
        return None

    steps = result.value.steps
    if not steps:
        return None
    return [
        {"purpose": s.purpose, "question": s.question, "priority": s.priority, "branch": s.branch}
        for s in steps
    ]
