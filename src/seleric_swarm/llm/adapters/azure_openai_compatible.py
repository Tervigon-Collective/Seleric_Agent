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


def _unsupported_param_fix(exc: APIStatusError) -> str | None:
    """Map an OpenAI 400 'unsupported_parameter' to the adaptation that fixes it,
    or None if the error is unrelated. Reasoning models (gpt-5*, o-series) reject
    ``max_tokens`` (want ``max_completion_tokens``) and a custom ``temperature``."""
    if getattr(exc, "status_code", None) != 400:
        return None
    body = getattr(exc, "body", None)
    err = body.get("error", body) if isinstance(body, dict) else {}
    param = (err.get("param") if isinstance(err, dict) else "") or ""
    message = ((err.get("message") if isinstance(err, dict) else "") or str(exc)).lower()
    if param == "max_tokens" or "max_completion_tokens" in message:
        return "max_completion_tokens"
    if param == "temperature" or ("temperature" in message and "unsupported" in message):
        return "drop_temperature"
    return None


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
        self._model = settings.primary_model()
        self._dev = settings.is_dev_surface()
        client = self._build_client(settings, api_key)
        self._client, self._traced = self._wrap_tracing(client)
        self._max_retries = max(0, settings.llm_max_retries)
        # Per-model learned param quirks, e.g. reasoning models (gpt-5*, o-series)
        # want `max_completion_tokens` not `max_tokens`, and reject a non-default
        # `temperature`. Learned from the API's own 400 (unsupported_parameter)
        # and cached so later calls skip the failed attempt. See
        # `_create_with_param_adaptation`.
        self._param_fixes: dict[str, set[str]] = {}

    @property
    def async_client(self) -> Any:
        """The underlying AsyncOpenAI/Azure client — reused by the V3 pydantic-ai model."""
        return self._client

    @staticmethod
    def _build_client(settings: Settings, api_key: str) -> Any:
        endpoint = settings.azure_openai_endpoint.rstrip("/")
        # `*.services.ai.azure.com` is Azure AI Inference (OpenAI-compatible), which
        # does not use classic Azure "deployment name" routing. Default to the
        # OpenAI-compatible client; opt into classic Azure OpenAI when the endpoint
        # is a real `*.openai.azure.com` deployment resource.
        # max_retries=0: this adapter owns retries via tenacity (see
        # `complete`); the SDK's built-in retries would stack on top of them and
        # multiply the effective attempt budget.
        if settings.azure_auth_style == "azure":
            return AsyncAzureOpenAI(
                azure_endpoint=endpoint,
                api_key=api_key,
                api_version=settings.azure_openai_api_version,
                timeout=settings.llm_timeout_s,
                max_retries=0,
            )
        base_url = endpoint if endpoint.endswith(("/v1", "/models")) else f"{endpoint}/models"
        return AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=settings.llm_timeout_s,
            default_query={"api-version": settings.azure_openai_api_version},
            max_retries=0,
        )

    @staticmethod
    def _wrap_tracing(client: Any) -> tuple[Any, bool]:
        try:
            from langsmith.wrappers import wrap_openai

            return wrap_openai(client), True
        except Exception:
            return client, False

    async def complete(self, request: LLMRequest) -> LLMResponse:
        # Model-to-model fallback is handled by LLMGateway, which swaps
        # request.model per attempt before calling this adapter — this
        # adapter only ever talks to the single model it's asked for.
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
                    base_kwargs: dict[str, Any] = {
                        "model": resolved_model,
                        "messages": messages,
                        "temperature": request.temperature,
                        "max_tokens": request.max_tokens,
                        "timeout": request.timeout_s,
                    }
                    extra: dict[str, Any] = {}
                    if self._traced:
                        extra["langsmith_extra"] = {
                            "name": run_name,
                            "tags": list(request.tags),
                            "metadata": llm_run_metadata(
                                request,
                                retry_count=retry_count,
                                resolved_model=resolved_model,
                            ),
                        }
                    completion = await self._create_with_param_adaptation(
                        resolved_model, base_kwargs, extra
                    )
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

    def _apply_param_fixes(self, kwargs: dict[str, Any], model: str) -> dict[str, Any]:
        fixes = self._param_fixes.get(model, set())
        out = dict(kwargs)
        if "max_completion_tokens" in fixes and "max_tokens" in out:
            out["max_completion_tokens"] = out.pop("max_tokens")
        if "drop_temperature" in fixes:
            out.pop("temperature", None)
        return out

    async def _create_with_param_adaptation(
        self, model: str, base_kwargs: dict[str, Any], extra: dict[str, Any]
    ) -> Any:
        """Call chat.completions.create, learning per-model param quirks from the
        API's own 400s (reasoning models want ``max_completion_tokens`` not
        ``max_tokens``, and reject a custom ``temperature``). Learned fixes are
        cached so subsequent calls skip the failed attempt."""
        # +1 for the initial try, +1 per distinct fixable param (tokens, temperature).
        for _ in range(3):
            kwargs = self._apply_param_fixes(base_kwargs, model)
            try:
                return await self._client.chat.completions.create(**kwargs, **extra)
            except APIStatusError as exc:
                fix = _unsupported_param_fix(exc)
                if fix is None or fix in self._param_fixes.get(model, set()):
                    raise
                self._param_fixes.setdefault(model, set()).add(fix)
        # Exhausted adaptations — do the final attempt so the real error surfaces.
        return await self._client.chat.completions.create(
            **self._apply_param_fixes(base_kwargs, model), **extra
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
