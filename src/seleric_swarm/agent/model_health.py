"""Per-model cooldowns for the fallback chain.

``FallbackModel`` restarts from the first model on *every* request, so a model
that is rate-limited (429) or hanging (30s timeout) is retried and re-failed on
each step of a mission. That single behaviour turned a ~10s answer into 60-120s.
A failing model is instead skipped instantly until its cooldown ends.

If every model in the chain is cooling down, none is skipped: better to try a
recently-failed model than to fail the mission without a single call.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

import structlog
from pydantic_ai.exceptions import FallbackExceptionGroup, ModelAPIError, ModelHTTPError
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.wrapper import WrapperModel

_log = structlog.get_logger()

COOLDOWN_RATE_LIMITED_S = 45.0
COOLDOWN_TIMEOUT_S = 90.0
COOLDOWN_SERVER_ERROR_S = 30.0
COOLDOWN_UNAVAILABLE_S = 300.0


class ModelHealth:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._until: dict[str, float] = {}
        self._known: set[str] = set()

    def register(self, key: str) -> None:
        self._known.add(key)

    def cool(self, key: str, seconds: float) -> None:
        self._until[key] = self._clock() + seconds

    def recover(self, key: str) -> None:
        self._until.pop(key, None)

    def _cooling(self, key: str) -> bool:
        return self._until.get(key, 0.0) > self._clock()

    def should_skip(self, key: str) -> bool:
        if not self._cooling(key):
            return False
        return any(not self._cooling(other) for other in self._known if other != key)

    def seconds_until_any_ready(self) -> float:
        """0 when some known model is not cooling, else until the first cooldown ends."""
        now = self._clock()
        remaining = [self._until.get(k, 0.0) - now for k in self._known]
        return max(0.0, min(remaining, default=0.0))


MODEL_HEALTH = ModelHealth()


def cooldown_for(exc: ModelAPIError) -> float:
    if isinstance(exc, ModelHTTPError):
        if exc.status_code == 429:
            return COOLDOWN_RATE_LIMITED_S
        if exc.status_code >= 500:
            return COOLDOWN_SERVER_ERROR_S
        if exc.status_code in (401, 402, 403):
            return COOLDOWN_UNAVAILABLE_S  # out of credits / bad key: will not fix itself in seconds
        return 0.0  # any other 4xx is a request problem, not model health
    return COOLDOWN_TIMEOUT_S  # timeouts / connection errors


class HealthGatedChatModel(OpenAIChatModel):
    """``OpenAIChatModel`` that fails instantly while cooling down."""

    def __init__(self, model_name: str, *, health: ModelHealth, health_key: str, **kwargs: Any):
        super().__init__(model_name, **kwargs)
        self._health = health
        self._health_key = health_key
        health.register(health_key)

    def _gate(self) -> None:
        if self._health.should_skip(self._health_key):
            raise ModelAPIError(self.model_name, "skipped: model is cooling down after a failure")

    def _record_failure(self, exc: ModelAPIError) -> None:
        seconds = cooldown_for(exc)
        if seconds:
            self._health.cool(self._health_key, seconds)

    async def request(self, *args: Any, **kwargs: Any) -> Any:
        self._gate()
        try:
            response = await super().request(*args, **kwargs)
        except ModelAPIError as exc:
            self._record_failure(exc)
            raise
        self._health.recover(self._health_key)
        return response

    @asynccontextmanager
    async def request_stream(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        self._gate()
        try:
            async with super().request_stream(*args, **kwargs) as streamed:
                yield streamed
        except ModelAPIError as exc:
            self._record_failure(exc)
            raise
        self._health.recover(self._health_key)


# How long one model request may wait for the chain to come back after every
# model failed transiently (429 / 5xx / timeout). Without it the mission died and
# the recovery worker re-ran it from scratch, discarding every tool result: live
# thread_e75c2615 restarted twice on LLM_RATE_LIMITED and took 7 minutes.
# Stays well under MISSION_TIMEOUT_S; 0 disables waiting.
AGENT_LLM_WAIT_BUDGET_S = float(os.getenv("AGENT_LLM_WAIT_BUDGET_S", "60"))
_MAX_SINGLE_WAIT_S = 20.0
_MIN_WAIT_S = 2.0


def _causes(exc: BaseException) -> list[BaseException]:
    if isinstance(exc, BaseExceptionGroup):
        return [c for sub in exc.exceptions for c in _causes(sub)]
    return [exc]


def is_transient(exc: BaseException) -> bool:
    """Some model in the chain failed in a way that heals by itself: a 429, a 5xx,
    a timeout / connection error or a cooldown skip. Auth/credit errors and other
    4xx are not — waiting does not change them."""
    for c in _causes(exc):
        if isinstance(c, ModelHTTPError):
            if c.status_code == 429 or c.status_code >= 500:
                return True
        elif isinstance(c, ModelAPIError):
            return True
    return False


class PatientModel(WrapperModel):
    """Retries the SAME model request when the whole chain failed transiently.

    Waits for the first cooldown to end (else a short backoff), within
    ``budget_s`` per request, so the mission keeps its message history and tool
    results instead of failing into a from-scratch run retry."""

    def __init__(
        self,
        wrapped: Any,
        *,
        health: ModelHealth = MODEL_HEALTH,
        budget_s: float | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(wrapped)
        self._health = health
        self._budget_s = AGENT_LLM_WAIT_BUDGET_S if budget_s is None else budget_s
        self._sleep = sleep
        self._clock = clock

    async def _wait_or_raise(self, exc: BaseException, started: float, attempt: int) -> None:
        left = self._budget_s - (self._clock() - started)
        if not is_transient(exc) or left <= 0:
            raise exc
        wait = self._health.seconds_until_any_ready() or _MIN_WAIT_S * (2**attempt)
        wait = max(_MIN_WAIT_S, min(wait, _MAX_SINGLE_WAIT_S, left))
        _log.warning("llm_chain_unavailable_waiting", wait_s=round(wait, 1), attempt=attempt + 1)
        await self._sleep(wait)

    async def request(self, *args: Any, **kwargs: Any) -> Any:
        started, attempt = self._clock(), 0
        while True:
            try:
                return await self.wrapped.request(*args, **kwargs)
            except (ModelAPIError, FallbackExceptionGroup) as exc:
                await self._wait_or_raise(exc, started, attempt)
                attempt += 1

    @asynccontextmanager
    async def request_stream(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        # Only opening the stream is retried; once tokens flow a failure propagates.
        started, attempt = self._clock(), 0
        async with AsyncExitStack() as stack:
            while True:
                try:
                    streamed = await stack.enter_async_context(self.wrapped.request_stream(*args, **kwargs))
                    break
                except (ModelAPIError, FallbackExceptionGroup) as exc:
                    await self._wait_or_raise(exc, started, attempt)
                    attempt += 1
            yield streamed
