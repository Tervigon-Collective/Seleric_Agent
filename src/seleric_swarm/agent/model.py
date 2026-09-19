"""Resolve the pydantic-ai model the V3 agent should run with.

Uses the same Azure OpenAI-compatible client the rest of this repo already
talks through. ``llm_provider=fake`` (tests) or missing credentials fall
back to the existing stub ``TestModel`` so the UI path stays exercisable
without a live LLM.
"""

from __future__ import annotations

from pydantic_ai.models import Model

from seleric_swarm.agent.agent import _stub_test_model
from seleric_swarm.config.settings import Settings, configured_chat_model


def resolve_v3_model(settings: Settings) -> Model:
    """Live OpenAI-compatible model when configured; otherwise the stub TestModel."""
    model_name = configured_chat_model(settings)
    if settings.llm_provider == "fake" or not model_name or not settings.azure_openai_api_key.strip():
        return _stub_test_model()

    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    from seleric_swarm.llm.adapters.azure_openai_compatible import AzureOpenAICompatibleAdapter

    adapter = AzureOpenAICompatibleAdapter(settings)
    return OpenAIChatModel(
        model_name,
        provider=OpenAIProvider(openai_client=adapter.async_client),
    )
