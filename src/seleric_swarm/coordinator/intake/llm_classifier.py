"""LLM-based query classification for swarm_v2 — replaces keyword/regex intent
matching. Regex stays only where it's genuinely the right tool: date-token
parsing (services/time_range.window_from_query handles "last 3 days" /
"yesterday" / ISO dates deterministically before the LLM's own time_range
guess is used as a fallback).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from seleric_swarm.contracts.lookup import TimeRangeV1
from seleric_swarm.coordinator.catalogue_grounding import (
    entities_from_catalogue,
    hints_from_catalogue,
)
from seleric_swarm.llm.errors import LLMError, LLMStructuredOutputError
from seleric_swarm.llm.port import ChatMessage, LLMRequest, LLMRequestMetadata
from seleric_swarm.services.time_range import resolve_time_range, window_from_query

if TYPE_CHECKING:
    from seleric_swarm.runtime import SwarmRuntime

AGENT_VERSION = "0.1.0"

SwarmIntent = Literal[
    "lookup", "comparison", "diagnostic", "predictive", "prescriptive", "executive_health"
]


class SwarmClassificationV1(BaseModel):
    intents: list[SwarmIntent] = Field(default_factory=list)
    domain_lead: str = ""
    entities: list[str] = Field(default_factory=list)
    time_range: TimeRangeV1 = Field(default_factory=TimeRangeV1)
    metric_hints: list[str] = Field(default_factory=list)
    unsupported_reason: str | None = None


class LlmClassification(BaseModel):
    """What coordinator/intake.normalize_query needs from the classifier —
    provider-agnostic so normalize_query doesn't depend on LLM plumbing types.
    """

    intents: list[str]
    domain_lead: str
    entities: list[str]
    time_range: TimeRangeV1
    primary_metric: str | None
    secondary_metrics: list[str]
    unresolved: bool


async def classify_query_via_llm(
    query: str,
    *,
    runtime: SwarmRuntime,
    timezone: str,
    as_of: str | None,
    mission_id: str | None = None,
    request_id: str | None = None,
    session_id: str | None = None,
    agent_id: str = "coordinator_agent",
) -> LlmClassification | None:
    """Classify intent/metrics/domain/entities via the LLM + live catalogue.

    Returns None when the LLM path isn't usable (no prompt, no LLM configured,
    or the call failed) — callers fall back to the offline regex classifier.
    """
    try:
        spec = runtime.prompts.load("coordinator.classify_swarm")
    except Exception:
        return None

    user = spec.render_user(
        {
            "query": query,
            "timezone": timezone,
            "as_of": as_of or "none",
            "registry_catalog": runtime.metrics.catalog_prompt(),
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
        tags=["coordinator", "classify_swarm", spec.id],
    )
    try:
        result = await runtime.llm.complete_structured(request, SwarmClassificationV1)
    except (LLMStructuredOutputError, LLMError):
        return None

    classification: SwarmClassificationV1 = result.value

    # Regex is the right tool for date tokens — try it before trusting the
    # LLM's own time_range guess.
    window = window_from_query(query, timezone, as_of)
    try:
        resolved_window = window or resolve_time_range(classification.time_range, timezone, as_of)
    except ValueError:
        resolved_window = TimeRangeV1()

    catalogue_hints = await hints_from_catalogue(query, runtime=runtime, agent_id=agent_id)
    merged_hints = list(dict.fromkeys([*classification.metric_hints, *catalogue_hints]))
    canonical = [m for m in merged_hints if runtime.metrics.get(m) is not None]

    entities = list(classification.entities or [])
    if not entities and canonical:
        entities = await entities_from_catalogue(query, canonical[0], runtime=runtime, agent_id=agent_id)

    # "coordinator_agent" is the orchestrating role, never a domain lead —
    # treat it the same as "no lead determined" so callers fall back safely.
    domain_lead = classification.domain_lead
    if domain_lead in {"coordinator_agent", "coordinator"}:
        domain_lead = ""

    return LlmClassification(
        intents=list(classification.intents) or ["lookup"],
        domain_lead=domain_lead,
        entities=entities,
        time_range=resolved_window,
        primary_metric=canonical[0] if canonical else None,
        secondary_metrics=canonical[1:],
        unresolved=not canonical and classification.unsupported_reason is not None,
    )
