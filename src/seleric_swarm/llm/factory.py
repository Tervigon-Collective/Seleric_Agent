from __future__ import annotations

from seleric_swarm.config.settings import Settings
from seleric_swarm.llm.errors import FallbackDisabled
from seleric_swarm.llm.port import LLMPort


def build_llm(settings: Settings) -> LLMPort:
    if settings.llm_fallback_model:
        raise FallbackDisabled()
    if settings.llm_provider == "fake":
        from seleric_swarm.llm.adapters.fake import FakeLLMAdapter

        return FakeLLMAdapter(model=settings.azure_openai_model)
    if settings.llm_provider == "azure_openai_compatible":
        from seleric_swarm.llm.adapters.azure_openai_compatible import AzureOpenAICompatibleAdapter

        return AzureOpenAICompatibleAdapter(settings)
    raise ValueError(f"Unknown LLM_PROVIDER={settings.llm_provider}")
