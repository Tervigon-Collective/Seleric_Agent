"""LLM-based small-talk detection + reply, replacing a fixed phrase table.

Whether a message is casual conversation (greeting/thanks/farewell/small
talk/"who are you") rather than a business ask is itself an LLM judgment
here, the same "no keyword table" stance ``coordinator.intake.llm_classifier``
takes for swarm intent -- so "hiya", "yo what's up", "appreciate it, thanks"
etc. get recognized like a real assistant would, not only the exact strings a
hardcoded set happened to enumerate. When the message isn't conversational,
or the LLM path is unavailable, this returns ``None`` and the caller falls
through to normal business routing -- no keyword guessing on failure.

Repeated identical messages (a user re-asking "hi", a retry, a flaky client)
are served from a small TTL cache instead of paying for another LLM call.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from pydantic import BaseModel

from seleric_swarm.coordinator.intake.conversation_context import (
    context_fingerprint,
    format_prior_turns,
)
from seleric_swarm.llm.errors import LLMError, LLMStructuredOutputError
from seleric_swarm.llm.port import ChatMessage, LLMRequest, LLMRequestMetadata
from seleric_swarm.utils.ttl_cache import TTLCache

if TYPE_CHECKING:
    from seleric_swarm.runtime import SwarmRuntime

AGENT_VERSION = "0.1.0"

_CACHE: TTLCache[str, str] = TTLCache(maxsize=256, ttl_s=600.0)
_NOT_CONVERSATIONAL = ""


class ConversationalReplyV1(BaseModel):
    is_conversational: bool = False
    reply: str = ""


def _cache_key(query: str, context_bundle: Mapping[str, Any] | None = None) -> str:
    normalized = " ".join(re.sub(r"[^\w\s']", "", query.casefold()).split())
    return f"{normalized}|{context_fingerprint(context_bundle)}"


async def classify_conversational_via_llm(
    query: str,
    *,
    runtime: SwarmRuntime,
    mission_id: str | None = None,
    request_id: str | None = None,
    session_id: str | None = None,
    agent_id: str = "coordinator_agent",
    context_bundle: Mapping[str, Any] | None = None,
) -> str | None:
    """Return a natural reply if ``query`` is casual conversation, else None."""
    key = _cache_key(query, context_bundle)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached or None

    try:
        spec = runtime.prompts.load("coordinator.conversational_reply")
    except Exception:
        return None

    user = spec.render_user({"query": query})
    prior_turns = format_prior_turns(context_bundle)
    if prior_turns:
        user = (
            f"{user}\nPrior user turns (follow-up context only; Message above "
            f"is the current ask — do not treat a metric/time follow-up as small talk):\n"
            f"{prior_turns}"
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
        tags=["coordinator", "conversational_reply", spec.id],
    )
    try:
        result = await runtime.llm.complete_structured(request, ConversationalReplyV1)
    except (LLMStructuredOutputError, LLMError):
        return None

    classification: ConversationalReplyV1 = result.value
    reply = classification.reply.strip() if classification.is_conversational else _NOT_CONVERSATIONAL
    _CACHE.set(key, reply)
    return reply or None
