"""Resolve the pydantic-ai model the V3 agent should run with.

Uses the same Azure OpenAI-compatible client the rest of this repo already
talks through. ``llm_provider=fake`` (tests) or missing credentials fall
back to the existing stub ``TestModel`` so the UI path stays exercisable
without a live LLM.
"""

from __future__ import annotations

import os
from typing import Any

from pydantic_ai.models import Model

from seleric_swarm.agent.agent import _stub_test_model
from seleric_swarm.agent.model_health import MODEL_HEALTH, HealthGatedChatModel, PatientModel
from seleric_swarm.config.settings import Settings, configured_chat_model

# Read timeout (httpx) for the agent's model client. A hung call costs its
# full timeout (measured), so the default stays short. Reasoning models
# (DeepSeek-V4-Pro, gpt-5-mini) can think silently longer than that and trip
# ReadTimeout; raise AGENT_LLM_TIMEOUT_S when they do. Must stay under
# mission_timeout_s (600).
AGENT_LLM_TIMEOUT_S = float(os.getenv("AGENT_LLM_TIMEOUT_S", "45"))


def resolve_v3_model(
    settings: Settings,
    *,
    prefer_fast: bool = False,
    role_tuning: bool = True,
    reasoning_effort: str = "",
) -> Model:
    """Live OpenAI-compatible model when configured; otherwise the stub TestModel.

    Wraps every configured model (``AZURE_OPENAI_MODELS``, primary first) in a
    ``FallbackModel`` so a rate-limited/erroring model doesn't fail the mission
    outright — pydantic-ai tries the next candidate on any ``ModelAPIError``
    (429s included). With one model (the 2026-10-07 setup: gpt-5-nano only) the
    chain is that model alone, and ``PatientModel`` waits out its 429s / timeouts
    instead of failing the mission.

    ``prefer_fast`` (set by the runner for simple read-only intents) puts
    ``AZURE_OPENAI_FAST_MODEL`` first in the chain when it is configured, so a
    lookup runs on the cheaper/faster deployment while the strong models stay
    behind it as reliability fallbacks. No-op when no fast model is set.

    Every model shares one client against ``AZURE_OPENAI_ENDPOINT`` (one resource,
    one quota); there is no second resource or third-party tail.
    """
    model_name = configured_chat_model(settings)
    if settings.llm_provider == "fake" or not model_name or not settings.azure_openai_api_key.strip():
        return _stub_test_model()

    from pydantic_ai.models.fallback import FallbackModel
    from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
    from pydantic_ai.providers.openai import OpenAIProvider

    from seleric_swarm.llm.adapters.azure_openai_compatible import AzureOpenAICompatibleAdapter

    # Agent calls are non-streaming and carry every tool result so far, so a
    # reasoning model legitimately needs longer than the 30s the short helper
    # calls (summaries, classification) use. Live: "why did CAC go up" died on a
    # 30s read timeout mid-investigation. Longer only for the agent's clients.
    settings = settings.model_copy(
        update={"llm_timeout_s": max(settings.llm_timeout_s, AGENT_LLM_TIMEOUT_S)}
    )
    adapter = AzureOpenAICompatibleAdapter(settings)
    provider = OpenAIProvider(openai_client=adapter.async_client)
    model_names = settings.resolved_models() or [model_name]
    fast_model = getattr(settings, "azure_openai_fast_model", "").strip()
    if prefer_fast and fast_model:
        # Fast deployment first; strong chain stays behind it as fallback.
        model_names = [fast_model, *[m for m in model_names if m != fast_model]]
    # Lean settings (minimal reasoning, bounded output) only on the fast deployment
    # so a lookup doesn't pay unbounded thinking. Strong fallbacks keep provider
    # defaults — an unsupported reasoning_effort can't break the reliability chain.
    # The fast deployment also serves as the fallback for complex missions. At its
    # default reasoning effort every step of a ~16-step investigation spent 700-4500
    # hidden reasoning tokens (8-18s/step, ~200s total, measured live), so it always
    # runs at low effort; the tight output cap stays fast-path-only.
    if not fast_model:
        fast_settings = None
    elif prefer_fast:
        fast_settings = OpenAIChatModelSettings(openai_reasoning_effort="minimal", max_tokens=2048)
    else:
        fast_settings = OpenAIChatModelSettings(openai_reasoning_effort="low")
    # Optionally dial down the strong models' reasoning effort (env-tunable,
    # e.g. "low"/"medium"). DeepSeek-V4-Pro accepts reasoning_effort; unset leaves
    # the provider default so this can't regress reasoning quality or break the
    # fallback chain unless explicitly opted in.
    strong_effort = reasoning_effort.strip() or (
        os.getenv("AZURE_OPENAI_STRONG_REASONING_EFFORT", "").strip() if role_tuning else ""
    )
    strong_settings = (
        OpenAIChatModelSettings(openai_reasoning_effort=strong_effort)
        if strong_effort
        else None
    )
    def chat(name: str, prov: OpenAIProvider, tag: str, model_settings: Any = None) -> OpenAIChatModel:
        return HealthGatedChatModel(
            name,
            provider=prov,
            settings=model_settings,
            health=MODEL_HEALTH,
            health_key=f"{tag}:{name}",
        )

    # The reasoning-effort tuning is the primary's (the deployment it was measured on); fallbacks keep provider
    # defaults. Applying it down the chain sent reasoning_effort to a non-reasoning fallback (gpt-4o), which
    # rejects the parameter — the fallback that should absorb the primary's 429s would have failed every call.
    models: list[OpenAIChatModel] = [
        chat(
            name,
            provider,
            "azure1",
            fast_settings if name == fast_model else (strong_settings if i == 0 else None),
        )
        for i, name in enumerate(model_names)
    ]

    chain: Model = models[0] if len(models) == 1 else FallbackModel(*models)
    return PatientModel(chain, health=MODEL_HEALTH)


def resolve_planner_model(settings: Settings) -> Model:
    """The planner's model: AZURE_OPENAI_PLANNER_MODEL on the same endpoint, else the
    agent's chain. The agent's reasoning-effort tuning is a per-deployment choice and
    is not carried over; AZURE_OPENAI_PLANNER_REASONING_EFFORT sets the planner's own
    (empty = provider default). Measured 2026-10-08 on 27 questions: gpt-5-mini at
    "low" read the same slots as grok-4.20-reasoning in 5.0s mean (max 8.6s) against
    11.8s (max 40.7s); "minimal" misread why-questions and once hung 92s."""
    name = (getattr(settings, "azure_openai_planner_model", "") or "").strip()
    if not name:
        return resolve_v3_model(settings)
    import json

    only = settings.model_copy(
        update={"azure_openai_models": json.dumps([name]), "azure_openai_fast_model": ""}
    )
    effort = (getattr(settings, "azure_openai_planner_reasoning_effort", "") or "").strip()
    return resolve_v3_model(only, role_tuning=False, reasoning_effort=effort)
