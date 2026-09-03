from __future__ import annotations

import time
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncAzureOpenAI,
    AsyncOpenAI,
    AuthenticationError,
    RateLimitError,
)
from pydantic import BaseModel, ValidationError
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from seleric_swarm.config.secrets import resolve_secret
from seleric_swarm.config.settings import Settings
from seleric_swarm.llm.errors import (
    FallbackDisabled,
    LLMError,
    LLMErrorCode,
    LLMStructuredOutputError,
)
from seleric_swarm.llm.port import (
    ChatMessage,
    LLMRequest,
    LLMResponse,
    StructuredLLMResponse,
    TokenUsage,
)
from seleric_swarm.llm.structured import parse_structured, with_schema_instruction
from seleric_swarm.llm.tracing import (
    enforce_llm_run_metadata,
    llm_run_metadata,
    llm_run_name,
)


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, LLMError):
        return exc.retryable
    if isinstance(exc, (APITimeoutError, APIConnectionError, RateLimitError)):
        return True
    if isinstance(exc, APIStatusError):
        return exc.status_code in {408, 409, 429} or exc.status_code >= 500
    return False


def normalize_openai_error(exc: BaseException) -> LLMError:
    if isinstance(exc, LLMError):
        return exc
    if isinstance(exc, AuthenticationError):
        return LLMError(LLMErrorCode.AUTH, str(exc), retryable=False)
    if isinstance(exc, RateLimitError):
        return LLMError(LLMErrorCode.RATE_LIMIT, str(exc), retryable=True)
    if isinstance(exc, APITimeoutError):
        return LLMError(LLMErrorCode.TIMEOUT, str(exc), retryable=True)
    if isinstance(exc, APIConnectionError):
        return LLMError(LLMErrorCode.UNAVAILABLE, str(exc), retryable=True)
    if isinstance(exc, APIStatusError):
        retryable = exc.status_code in {408, 409, 429} or exc.status_code >= 500
        code = LLMErrorCode.RATE_LIMIT if exc.status_code == 429 else (
            LLMErrorCode.UNAVAILABLE if retryable else LLMErrorCode.BAD_REQUEST
        )
        return LLMError(code, str(exc), retryable=retryable)
    return LLMError(LLMErrorCode.UNAVAILABLE, str(exc), retryable=False)


class AzureOpenAICompatibleAdapter:
    """Azure AI Inference / OpenAI-compatible client isolated behind LLMPort."""

    def __init__(self, settings: Settings) -> None:
        api_key = resolve_secret("AZURE_OPENAI_API_KEY", settings, settings.azure_openai_api_key)
        if not api_key:
            raise LLMError(
                LLMErrorCode.AUTH,
                "AZURE_OPENAI_API_KEY is not set. Use env for local or Key Vault in deploy.",
                retryable=False,
            )
        self._settings = settings
        self._model = settings.azure_openai_model
        self._dev = settings.is_dev_surface()
        client = self._build_client(settings, api_key)
        self._client, self._traced = self._wrap_tracing(client)
        self._max_retries = max(0, settings.llm_max_retries)

    @staticmethod
    def _build_client(settings: Settings, api_key: str) -> Any:
        endpoint = settings.azure_openai_endpoint.rstrip("/")
        # `*.services.ai.azure.com` is Azure AI Inference (OpenAI-compatible), which
        # does not use classic Azure "deployment name" routing. Default to the
        # OpenAI-compatible client; opt into classic Azure OpenAI when the endpoint
        # is a real `*.openai.azure.com` deployment resource.
        if settings.azure_auth_style == "azure":
            return AsyncAzureOpenAI(
                azure_endpoint=endpoint,
                api_key=api_key,
                api_version=settings.azure_openai_api_version,
                timeout=settings.llm_timeout_s,
            )
        base_url = endpoint if endpoint.endswith(("/v1", "/models")) else f"{endpoint}/models"
        return AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=settings.llm_timeout_s,
            default_query={"api-version": settings.azure_openai_api_version},
        )

    @staticmethod
    def _wrap_tracing(client: Any) -> tuple[Any, bool]:
        try:
            from langsmith.wrappers import wrap_openai

            return wrap_openai(client), True
        except Exception:
            return client, False

    async def complete(self, request: LLMRequest) -> LLMResponse:
        if request.fallback_model:
            raise FallbackDisabled()
        messages = [{"role": m.role, "content": m.content} for m in request.messages]
        resolved_model = request.model or self._model
        retry_count = 0
        enforce_llm_run_metadata(
            request,
            llm_run_metadata(request, retry_count=0, resolved_model=resolved_model),
            strict=self._dev,
        )
        run_name = llm_run_name(request)
        started = time.perf_counter()
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self._max_retries + 1),
                wait=wait_exponential(multiplier=0.5, min=0.5, max=8),
                retry=retry_if_exception(_is_retryable),
                reraise=True,
            ):
                with attempt:
                    retry_count = max(0, attempt.retry_state.attempt_number - 1)
                    create_kwargs: dict[str, Any] = {
                        "model": resolved_model,
                        "messages": messages,
                        "temperature": request.temperature,
                        "max_tokens": request.max_tokens,
                        "timeout": request.timeout_s,
                    }
                    if self._traced:
                        create_kwargs["langsmith_extra"] = {
                            "name": run_name,
                            "tags": list(request.tags),
                            "metadata": llm_run_metadata(
                                request,
                                retry_count=retry_count,
                                resolved_model=resolved_model,
                            ),
                        }
                    completion = await self._client.chat.completions.create(**create_kwargs)
        except Exception as exc:
            raise normalize_openai_error(exc) from exc

        choice = completion.choices[0]
        usage = completion.usage
        latency_ms = (time.perf_counter() - started) * 1000
        return LLMResponse(
            text=(choice.message.content or "").strip(),
            model=completion.model or request.model or self._model,
            finish_reason=choice.finish_reason,
            usage=TokenUsage(
                prompt_tokens=getattr(usage, "prompt_tokens", None),
                completion_tokens=getattr(usage, "completion_tokens", None),
                total_tokens=getattr(usage, "total_tokens", None),
            ),
            latency_ms=latency_ms,
            retry_count=retry_count,
            provider_request_id=getattr(completion, "id", None),
        )

    async def complete_structured(
        self, request: LLMRequest, schema: type[BaseModel]
    ) -> StructuredLLMResponse:
        prepared = with_schema_instruction(request, schema)
        raw = await self.complete(prepared)
        try:
            value = parse_structured(raw, schema)
            return StructuredLLMResponse(value=value, raw=raw)
        except (LLMStructuredOutputError, ValidationError) as exc:
            repair = prepared.model_copy(
                update={
                    "messages": list(prepared.messages)
                    + [
                        ChatMessage(role="assistant", content=raw.text),
                        ChatMessage(
                            role="user",
                            content=(
                                f"The JSON failed validation: {exc}. "
                                "Return corrected JSON only."
                            ),
                        ),
                    ]
                }
            )
            repaired = await self.complete(repair)
            repaired.retry_count = raw.retry_count + 1 + repaired.retry_count
            try:
                value = parse_structured(repaired, schema)
            except LLMStructuredOutputError as parse_exc:
                parse_exc.retry_count = repaired.retry_count
                raise parse_exc from parse_exc
            return StructuredLLMResponse(value=value, raw=repaired)
