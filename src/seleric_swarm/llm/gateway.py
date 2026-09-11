"""LLM Gateway: model-agnostic routing/fallback middleware over one provider adapter.

Wraps a real ``LLMPort`` (e.g. ``AzureOpenAICompatibleAdapter``) and, for each
request, tries a priority-ordered list of ``ModelSpec`` candidates instead of
one fixed model. The wrapped adapter already retries transient failures for a
single model attempt (see its own tenacity loop); this gateway is what
happens once those retries are exhausted for one model, or that model is
capability-mismatched, or its circuit is open.

Conversation history, system instructions, and request metadata (already
provider-independent via ``LLMRequest``/``ChatMessage``) are passed through
unchanged to every candidate — the gateway only swaps ``request.model``, so a
fallback model resumes with full context. A non-retryable error (bad
request, auth, parse failure) is raised immediately rather than tried on
other models, since switching models won't fix it. If every candidate is
either unhealthy (circuit open) or fails, the caller gets a normal
``LLMError`` — the same exception type callers already handle — so an
orchestration-level retry/degrade path (e.g.
``coordinator/execution/retry.py``) can recover without the gateway needing
its own workflow-recovery logic.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

from pydantic import BaseModel

from seleric_swarm.llm.circuit_breaker import CircuitBreaker
from seleric_swarm.llm.errors import LLMError, LLMErrorCode
from seleric_swarm.llm.model_spec import ModelSpec
from seleric_swarm.llm.port import LLMPort, LLMRequest, LLMResponse, StructuredLLMResponse

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)
R = TypeVar("R")


class LLMGateway:
    def __init__(self, inner: LLMPort, models: list[ModelSpec]) -> None:
        if not models:
            raise ValueError("LLMGateway requires at least one ModelSpec")
        self._inner = inner
        self._models = sorted(models, key=lambda m: m.priority)
        self._breakers = {m.id: CircuitBreaker() for m in self._models}

    def _candidates(self, request: LLMRequest) -> list[ModelSpec]:
        return [m for m in self._models if m.supports(request) and self._breakers[m.id].allow()]

    async def _route(self, request: LLMRequest, call: Callable[[LLMRequest], Awaitable[R]]) -> R:
        candidates = self._candidates(request)
        if not candidates:
            raise LLMError(
                LLMErrorCode.UNAVAILABLE,
                "No capable/healthy model available in the gateway routing table",
                retryable=False,
            )
        last_error: LLMError | None = None
        for spec in candidates:
            attempt = request.model_copy(update={"model": spec.id})
            try:
                result = await call(attempt)
            except LLMError as exc:
                self._breakers[spec.id].record_failure()
                logger.warning(
                    "llm_gateway.model_failed model=%s code=%s retryable=%s",
                    spec.id,
                    exc.code,
                    exc.retryable,
                )
                last_error = exc
                if not exc.retryable:
                    raise
                continue
            self._breakers[spec.id].record_success()
            if spec.id != candidates[0].id:
                logger.info("llm_gateway.fallback_served model=%s", spec.id)
            return result
        assert last_error is not None  # candidates is non-empty, so a loop always sets or returns
        raise last_error

    async def complete(self, request: LLMRequest) -> LLMResponse:
        return await self._route(request, self._inner.complete)

    async def complete_structured(self, request: LLMRequest, schema: type[T]) -> StructuredLLMResponse:
        return await self._route(request, lambda r: self._inner.complete_structured(r, schema))
