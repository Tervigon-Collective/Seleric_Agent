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
    """Live OpenAI-compatible model when configured; otherwise the stub TestModel.

    Wraps every configured model (``AZURE_OPENAI_MODELS``, primary first) in a
    ``FallbackModel`` so a rate-limited/erroring model doesn't fail the mission
    outright — pydantic-ai tries the next candidate on any ``ModelAPIError``
    (429s included).

    Every model above shares one ``AsyncOpenAI`` client against
    ``AZURE_OPENAI_ENDPOINT``, so an endpoint-level 429 (the whole resource is
    throttled, not just one deployment) takes all of them out together. If
    ``AZURE_OPENAI_ENDPOINT_2``/``AZURE_OPENAI_API_KEY_2`` are set, that
    second resource's models are appended to the same chain — a genuinely
    separate quota to fall back to once the primary resource is exhausted.
    """
    model_name = configured_chat_model(settings)
    if settings.llm_provider == "fake" or not model_name or not settings.azure_openai_api_key.strip():
        return _stub_test_model()

    from pydantic_ai.models.fallback import FallbackModel
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    from seleric_swarm.llm.adapters.azure_openai_compatible import AzureOpenAICompatibleAdapter

    adapter = AzureOpenAICompatibleAdapter(settings)
    provider = OpenAIProvider(openai_client=adapter.async_client)
    model_names = settings.resolved_models() or [model_name]
    models = [OpenAIChatModel(name, provider=provider) for name in model_names]

    if settings.azure_openai_endpoint_2.strip() and settings.azure_openai_api_key_2.strip():
        settings_2 = settings.model_copy(
            update={
                "azure_openai_endpoint": settings.azure_openai_endpoint_2,
                "azure_openai_api_key": settings.azure_openai_api_key_2,
                "azure_openai_models": settings.azure_openai_models_2,
                "azure_openai_model1": "",
                "azure_openai_model2": "",
                "azure_openai_model": "",
            }
        )
        adapter_2 = AzureOpenAICompatibleAdapter(settings_2)
        provider_2 = OpenAIProvider(openai_client=adapter_2.async_client)
        models.extend(
            OpenAIChatModel(name, provider=provider_2) for name in settings_2.resolved_models()
        )

    if len(models) == 1:
        return models[0]
    return FallbackModel(*models)
