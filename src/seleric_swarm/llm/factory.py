from __future__ import annotations

from seleric_swarm.config.settings import Settings
from seleric_swarm.llm.errors import LLMError, LLMErrorCode
from seleric_swarm.llm.model_spec import ModelSpec
from seleric_swarm.llm.port import LLMPort, LLMRequest, LLMResponse, StructuredLLMResponse


def _fallback_model_ids(settings: Settings) -> list[str]:
    ids = list(settings.numbered_fallback_models())
    for extra in [
        *[m.strip() for m in (settings.llm_fallback_models or "").split(",") if m.strip()],
        settings.llm_fallback_model or "",
    ]:
        if extra and extra not in ids:
            ids.append(extra)
    return ids


class ProviderRouter:
    """LLMPort over one adapter per provider: ``request.model`` is "provider:model"."""

    def __init__(self, adapters: dict[str, LLMPort]) -> None:
        self._adapters = adapters

    def _split(self, request: LLMRequest) -> tuple[str, LLMPort, LLMRequest]:
        provider, _, model = request.model.partition(":")  # spec ids are always "<connection id>:<model>"
        return provider, self._adapters[provider], request.model_copy(update={"model": model})

    async def complete(self, request: LLMRequest) -> LLMResponse:
        provider, adapter, routed = self._split(request)
        try:
            response = await adapter.complete(routed)
        except LLMError as exc:
            raise _next_provider_may_work(exc) from exc
        response.model = f"{provider}:{response.model}"  # which vendor served (and billed) the call
        return response

    async def complete_structured(self, request: LLMRequest, schema: type) -> StructuredLLMResponse:
        provider, adapter, routed = self._split(request)
        try:
            result = await adapter.complete_structured(routed, schema)
        except LLMError as exc:
            raise _next_provider_may_work(exc) from exc
        result.raw.model = f"{provider}:{result.raw.model}"
        return result


def _next_provider_may_work(exc: LLMError) -> LLMError:
    """A rejected key (revoked, out of credit) is final for that vendor only: let the gateway try the next entry."""
    if exc.code == LLMErrorCode.AUTH and not exc.retryable:
        return LLMError(exc.code, exc.message, retryable=True)
    return exc


def _helper_gateway(settings: Settings, chain: list[tuple[str, str, str | None]]) -> LLMPort:
    from seleric_swarm.llm.adapters.azure_openai_compatible import AzureOpenAICompatibleAdapter
    from seleric_swarm.llm.gateway import LLMGateway
    from seleric_swarm.llm.models_store import connections

    # Entries whose connection is gone or keyless are left out, so a missing key never costs a failed call.
    known = connections(settings)
    chain = [(c, m) for c, m, _ in chain if c in known]
    if not chain:
        raise ValueError("the helper model list has no entry on a connected provider")

    def adapter_for(connection_id: str) -> LLMPort:
        conn = known[connection_id]
        if conn.provider == "chatgpt":
            from seleric_swarm.llm.adapters.subscription import ChatGPTPlanAdapter

            return ChatGPTPlanAdapter(connection_id)
        if conn.provider == "claude-code":
            from seleric_swarm.llm.adapters.subscription import ClaudeCodeAdapter

            return ClaudeCodeAdapter()
        return AzureOpenAICompatibleAdapter(settings, base_url=conn.base_url, api_key=conn.api_key)

    adapters: dict[str, LLMPort] = {c: adapter_for(c) for c in dict.fromkeys(c for c, _ in chain)}
    fallback_timeout = min(15.0, settings.llm_timeout_s)
    specs = [
        ModelSpec(id=f"{c}:{m}", priority=i, timeout_s=settings.llm_timeout_s if i == 0 else fallback_timeout)
        for i, (c, m) in enumerate(chain)
    ]
    return LLMGateway(ProviderRouter(adapters), specs)


def build_llm(settings: Settings) -> LLMPort:
    if settings.llm_provider == "fake":
        from seleric_swarm.llm.adapters.fake import FakeLLMAdapter

        return FakeLLMAdapter(model=settings.primary_model() or "fake-llama")
    from seleric_swarm.llm.models_store import role_chain

    helper_chain = role_chain(settings, "helper")
    if helper_chain:
        return _helper_gateway(settings, helper_chain)
    if settings.llm_provider == "azure_openai_compatible":
        from seleric_swarm.llm.adapters.azure_openai_compatible import AzureOpenAICompatibleAdapter

        adapter = AzureOpenAICompatibleAdapter(settings)
        fallback_ids = _fallback_model_ids(settings)
        if not fallback_ids:
            return adapter

        from seleric_swarm.llm.gateway import LLMGateway

        # Primary model gets full timeout, fallbacks get shorter timeout for faster failover
        primary_timeout = settings.llm_timeout_s
        fallback_timeout = min(15.0, settings.llm_timeout_s)
        models = [ModelSpec(id=settings.primary_model(), priority=0, timeout_s=primary_timeout)]
        models += [
            ModelSpec(id=model_id, priority=index + 1, timeout_s=fallback_timeout)
            for index, model_id in enumerate(fallback_ids)
        ]
        return LLMGateway(adapter, models)
    raise ValueError(f"Unknown LLM_PROVIDER={settings.llm_provider}")
