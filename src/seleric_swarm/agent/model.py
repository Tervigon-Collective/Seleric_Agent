"""Resolve the pydantic-ai model the V3 agent should run with.

Uses the same Azure OpenAI-compatible client the rest of this repo already
talks through. ``llm_provider=fake`` (tests) or missing credentials fall
back to the existing stub ``TestModel`` so the UI path stays exercisable
without a live LLM.
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
from seleric_swarm.config.settings import Settings, configured_chat_model

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
    return name.lower().startswith(("gpt-5", "o1", "o3", "o4"))


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
    """The loop's model chain: ``AZURE_OPENAI_MODELS`` in order, each with its own request shape.

    A reasoning model gets ``reasoning_effort`` (low by default, raised by ``escalate``) and a completion cap with
    thinking headroom; any other model gets temperature 0 and the plain cap, and never ``reasoning_effort`` (a
    non-reasoning fallback rejects it). Cooldown, per-request cap and patience come from the shared health gate.
    """
    model_name = configured_chat_model(settings)
    if settings.llm_provider == "fake" or not model_name or not settings.azure_openai_api_key.strip():
        return TestModel(call_tools=[])

    from pydantic_ai.models.fallback import FallbackModel
    from pydantic_ai.models.openai import OpenAIChatModelSettings
    from pydantic_ai.providers.openai import OpenAIProvider

    from seleric_swarm.llm.adapters.azure_openai_compatible import AzureOpenAICompatibleAdapter

    settings = settings.model_copy(update={"llm_timeout_s": max(settings.llm_timeout_s, AGENT_LLM_TIMEOUT_S)})
    provider = OpenAIProvider(openai_client=AzureOpenAICompatibleAdapter(settings).async_client)
    models: list[Model] = []
    for name in settings.resolved_models() or [model_name]:
        if is_reasoning_model(name):
            tuning = OpenAIChatModelSettings(
                openai_reasoning_effort=LOOP_REASONING_EFFORT, max_tokens=LOOP_MAX_TOKENS + LOOP_REASONING_BUDGET
            )
        else:
            tuning = OpenAIChatModelSettings(temperature=0, max_tokens=LOOP_MAX_TOKENS)
        chat: Model = HealthGatedChatModel(
            name, provider=provider, settings=tuning, health=MODEL_HEALTH, health_key=f"loop:{name}"
        )
        models.append(EffortModel(chat) if is_reasoning_model(name) else chat)
    chain: Model = models[0] if len(models) == 1 else FallbackModel(*models)
    return PatientModel(chain, health=MODEL_HEALTH)
