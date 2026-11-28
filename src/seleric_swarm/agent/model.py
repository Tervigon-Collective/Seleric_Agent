"""Resolve the pydantic-ai model the V3 agent should run with.

Uses the same OpenAI-compatible client the rest of this repo already talks
through, pointed at the OpenAI platform or Azure per chain entry.
``llm_provider=fake`` (tests) or no credentials for any entry fall back to
the existing stub ``TestModel`` so the UI path stays exercisable without a
live LLM.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any

from pydantic_ai.messages import ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.models.test import TestModel
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings

from seleric_swarm.agent.model_health import (
    MODEL_HEALTH,
    HealthGatedChatModel,
    PatientModel,
)
from seleric_swarm.config.settings import Settings

# Read timeout (httpx) for the agent's model client. A hung call costs its
# full timeout (measured), so the default stays short. Reasoning models
# (DeepSeek-V4-Pro, gpt-5-mini) can think silently longer than that and trip
# ReadTimeout; raise AGENT_LLM_TIMEOUT_S when they do. Must stay under
# mission_timeout_s (600).
AGENT_LLM_TIMEOUT_S = float(os.getenv("AGENT_LLM_TIMEOUT_S", "45"))


# ---- the single reasoning loop's model ----------------------------------------------------------
LOOP_MAX_TOKENS = int(os.getenv("AGENT_MAX_TOKENS", "2000"))
# Reasoning tokens count toward the completion cap: without headroom a reasoning model spends the whole cap thinking.
LOOP_REASONING_BUDGET = int(os.getenv("AGENT_REASONING_BUDGET", "6000"))
LOOP_REASONING_EFFORT: Any = os.getenv("AGENT_REASONING_EFFORT", "low")
LOOP_ESCALATED_EFFORT = os.getenv("AGENT_ESCALATED_EFFORT", "medium")
# Effort for the steps after a causal analysis helper has returned: its engine already did the reasoning, what is left is
# reporting its figures. Measured 2026-10-14: the final step at medium spends ~1.5k reasoning tokens (15s) vs ~0.3k at low.
LOOP_WRITEUP_EFFORT = os.getenv("AGENT_WRITEUP_EFFORT", LOOP_ESCALATED_EFFORT)

# The mission's effort holder. Tools run in child asyncio tasks, so a ContextVar.set inside a tool would never reach
# the task that makes the next model request; a mutable holder created before the run is shared by every copy.
_EFFORT: ContextVar[dict[str, str] | None] = ContextVar("seleric_loop_reasoning_effort", default=None)


def begin_loop_effort() -> None:
    """Open this mission's effort scope (call in the mission task, before the agent run starts)."""
    _EFFORT.set({})


def set_loop_effort(effort: str | None) -> None:
    """Raise (or reset) the reasoning effort for the rest of the mission. No-op outside a scope."""
    holder = _EFFORT.get()
    if holder is not None:
        if effort:
            holder["effort"] = effort
        else:
            holder.pop("effort", None)


def loop_effort() -> str | None:
    holder = _EFFORT.get()
    return holder.get("effort") if holder else None


def is_reasoning_model(name: str) -> bool:
    """Reasoning deployments take ``reasoning_effort`` and a completion cap that includes their thinking."""
    n = name.lower()
    if n.startswith(("o1", "o3", "o4")):
        return True
    major = n.removeprefix("gpt-").split(".")[0].split("-")[0]  # gpt-6.2-astra -> 6, gpt-4o -> 4o
    return n.startswith("gpt-") and major.isdigit() and int(major) >= 5


class EffortModel(WrapperModel):
    """Raises the reasoning effort of the wrapped reasoning model once ``escalate`` has set it for this mission."""

    def _with_effort(self, model_settings: ModelSettings | None) -> ModelSettings | None:
        effort = loop_effort()
        if not effort:
            return model_settings
        return {**(model_settings or {}), "openai_reasoning_effort": effort}  # type: ignore[return-value]

    async def request(
        self,
        messages: list[Any],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        return await self.wrapped.request(messages, self._with_effort(model_settings), model_request_parameters)

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[Any],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: Any = None,
    ) -> AsyncIterator[Any]:
        async with self.wrapped.request_stream(
            messages, self._with_effort(model_settings), model_request_parameters, run_context
        ) as streamed:
            yield streamed


def resolve_loop_model(settings: Settings) -> Model:
    """The loop's model chain: Settings → Models "Main model" + fallbacks, else ``LLM_MODELS`` / ``AZURE_OPENAI_MODELS``.

    Each entry runs on its connection (base URL + key); an entry whose connection is gone or keyless is left out.
    A reasoning model (or one given a Thinking level) gets ``reasoning_effort`` and a completion cap with thinking
    headroom; any other model gets temperature 0 and the plain cap, never ``reasoning_effort`` (a non-reasoning
    fallback rejects it). Cooldown and patience come from the shared health gate, so a 429 moves to the next entry.
    """
    from seleric_swarm.llm.models_store import connections, role_chain

    known = connections(settings)
    chain = [(known[c], name, thinking) for c, name, thinking in role_chain(settings, "main") if c in known]
    if settings.llm_provider == "fake" or not chain:
        return TestModel(call_tools=[])

    from pydantic_ai.models.fallback import FallbackModel
    from pydantic_ai.models.openai import OpenAIChatModelSettings, OpenAIResponsesModel
    from pydantic_ai.providers.openai import OpenAIProvider

    from seleric_swarm.llm.adapters.azure_openai_compatible import AzureOpenAICompatibleAdapter

    settings = settings.model_copy(update={"llm_timeout_s": max(settings.llm_timeout_s, AGENT_LLM_TIMEOUT_S)})
    providers: dict[str, OpenAIProvider] = {}

    def provider_for(conn: Any) -> OpenAIProvider:
        if conn.id not in providers:
            adapter = AzureOpenAICompatibleAdapter(settings, base_url=conn.base_url, api_key=conn.api_key)
            providers[conn.id] = OpenAIProvider(openai_client=adapter.async_client)
        return providers[conn.id]

    def tuning_for(name: str, thinking: str | None, reasoning: bool) -> OpenAIChatModelSettings:
        if reasoning:
            return OpenAIChatModelSettings(
                openai_reasoning_effort=thinking or LOOP_REASONING_EFFORT,
                max_tokens=LOOP_MAX_TOKENS + LOOP_REASONING_BUDGET,
            )
        return OpenAIChatModelSettings(temperature=0, max_tokens=LOOP_MAX_TOKENS)

    models: list[Model] = []
    for conn, name, thinking in chain:
        reasoning = bool(thinking) or is_reasoning_model(name)
        tuning = tuning_for(name, thinking, reasoning)
        if conn.provider == "claude-code":
            from seleric_swarm.llm.claude_code import ClaudeCodeModel

            chat = ClaudeCodeModel(name)
        elif conn.provider == "chatgpt":
            from openai import AsyncOpenAI

            from seleric_swarm.llm.chatgpt_plan import RESOURCE, access_token

            client = AsyncOpenAI(api_key=access_token(conn.id), base_url=RESOURCE)
            chat = OpenAIResponsesModel(
                name,
                provider=OpenAIProvider(openai_client=client),
                profile={"openai_responses_requires_streaming": True, "openai_responses_requires_store_false": True},
                settings=tuning,
            )
        else:
            chat = HealthGatedChatModel(
                name, provider=provider_for(conn), settings=tuning, health=MODEL_HEALTH, health_key=f"loop:{conn.id}:{name}"
            )
        models.append(EffortModel(chat) if reasoning and conn.provider != "claude-code" else chat)
    chain_model: Model = models[0] if len(models) == 1 else FallbackModel(*models)
    return PatientModel(chain_model, health=MODEL_HEALTH)
