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


def resolve_v3_model(settings: Settings, *, prefer_fast: bool = False) -> Model:
    """Live OpenAI-compatible model when configured; otherwise the stub TestModel.

    Wraps every configured model (``AZURE_OPENAI_MODELS``, primary first) in a
    ``FallbackModel`` so a rate-limited/erroring model doesn't fail the mission
    outright — pydantic-ai tries the next candidate on any ``ModelAPIError``
    (429s included).

    ``prefer_fast`` (set by the runner for simple read-only intents) puts
    ``AZURE_OPENAI_FAST_MODEL`` first in the chain when it is configured, so a
    lookup runs on the cheaper/faster deployment while the strong models stay
    behind it as reliability fallbacks. No-op when no fast model is set.

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
    from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
    from pydantic_ai.providers.openai import OpenAIProvider

    from seleric_swarm.llm.adapters.azure_openai_compatible import AzureOpenAICompatibleAdapter

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
    fast_settings = (
        OpenAIChatModelSettings(openai_reasoning_effort="minimal", max_tokens=2048)
        if prefer_fast and fast_model
        else None
    )
    models = [
        OpenAIChatModel(
            name, provider=provider, settings=fast_settings if name == fast_model else None
        )
        for name in model_names
    ]

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

    # Independent-provider tail fallback: after every Azure resource is
    # exhausted, fall through to OpenRouter's separate provider pool. Additive —
    # unset OpenRouter env leaves the chain exactly as above.
    from seleric_swarm.llm.openrouter import (
        build_openrouter_provider,
        resolved_openrouter_models,
    )

    or_models = resolved_openrouter_models(settings)
    if or_models and settings.openrouter_api_key.strip():
        or_provider = build_openrouter_provider(settings)
        models.extend(OpenAIChatModel(name, provider=or_provider) for name in or_models)

    if len(models) == 1:
        return models[0]
    return FallbackModel(*models)
