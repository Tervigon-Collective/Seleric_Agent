"""Translate an :class:`LLMRequest` into LangSmith run metadata + a span name.

Agents assemble ``LLMRequestMetadata``; without this the adapter would drop it and
the LLM span would carry none of the required keys (plan section 6).
"""

from __future__ import annotations

from seleric_swarm.llm.port import LLMRequest
from seleric_swarm.observability.tracing import (
    REQUIRED_LLM_RUN_METADATA,
    assert_required_metadata,
    redact_mapping,
)


def llm_run_metadata(
    request: LLMRequest, *, retry_count: int, resolved_model: str
) -> dict[str, object]:
    md = request.metadata
    payload = {
        "request_id": md.request_id,
        "session_id": md.session_id,
        "mission_id": md.mission_id,
        "task_id": md.task_id,
        "agent_id": md.agent_id,
        "agent_name": md.agent_id,
        "agent_version": md.agent_version,
        "prompt_id": md.prompt_id,
        "prompt_version": md.prompt_version,
        "workflow_name": md.workflow_name,
        "workflow_version": md.workflow_version,
        "query_class": md.query_class,
        "model": md.model or resolved_model,
        "retry_count": retry_count,
        "response_format": request.response_format,
    }
    return redact_mapping({k: v for k, v in payload.items() if v is not None})


def llm_run_name(request: LLMRequest) -> str:
    if request.metadata.prompt_id:
        return f"llm.{request.metadata.prompt_id}"
    if request.metadata.agent_id:
        return f"llm.{request.metadata.agent_id}"
    return "llm.call"


def enforce_llm_run_metadata(
    request: LLMRequest, metadata: dict[str, object], *, strict: bool
) -> None:
    """Prompt-backed calls must carry full LLM metadata; dev-only pings are exempt."""
    if not request.metadata.prompt_id:
        return
    assert_required_metadata(
        metadata,
        strict=strict,
        required=REQUIRED_LLM_RUN_METADATA,
        context=llm_run_name(request),
    )
