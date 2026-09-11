from __future__ import annotations

from seleric_swarm.config.settings import Settings
from seleric_swarm.llm.model_spec import ModelSpec
from seleric_swarm.llm.port import LLMPort


def _fallback_model_ids(settings: Settings) -> list[str]:
    ids = list(settings.numbered_fallback_models())
    for extra in [
        *[m.strip() for m in (settings.llm_fallback_models or "").split(",") if m.strip()],
        settings.llm_fallback_model or "",
    ]:
        if extra and extra not in ids:
            ids.append(extra)
    return ids


def build_llm(settings: Settings) -> LLMPort:
    if settings.llm_provider == "fake":
        from seleric_swarm.llm.adapters.fake import FakeLLMAdapter

        return FakeLLMAdapter(model=settings.primary_model() or "fake-llama")
    if settings.llm_provider == "azure_openai_compatible":
        from seleric_swarm.llm.adapters.azure_openai_compatible import AzureOpenAICompatibleAdapter

        adapter = AzureOpenAICompatibleAdapter(settings)
        fallback_ids = _fallback_model_ids(settings)
        if not fallback_ids:
            return adapter

        from seleric_swarm.llm.gateway import LLMGateway

        models = [ModelSpec(id=settings.primary_model(), priority=0)]
        models += [
            ModelSpec(id=model_id, priority=index + 1) for index, model_id in enumerate(fallback_ids)
        ]
        return LLMGateway(adapter, models)
    raise ValueError(f"Unknown LLM_PROVIDER={settings.llm_provider}")
