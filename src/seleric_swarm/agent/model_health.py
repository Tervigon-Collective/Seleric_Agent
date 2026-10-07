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
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import structlog
from pydantic_ai.exceptions import (
    FallbackExceptionGroup,
    ModelAPIError,
    ModelHTTPError,
    UnexpectedModelBehavior,
)
from pydantic_ai.messages import ModelResponse, ModelResponseStreamEvent, TextPart, ToolCallPart
from pydantic_ai.models import StreamedResponse
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.wrapper import WrapperModel

_log = structlog.get_logger()

COOLDOWN_RATE_LIMITED_S = 45.0
# Consecutive 429s double the cooldown up to this cap; a success resets it. A
# per-minute TOKEN quota smaller than one agent prompt (DeepSeek-V4-Pro,
# southindia, ~50k-token steps) re-trips within a minute, so a flat 45s made
# the first step of nearly every mission eat a 429 (12 of 19 missions on
# 2026-10-06) before falling through.
COOLDOWN_RATE_LIMITED_MAX_S = float(os.getenv("AGENT_LLM_429_COOLDOWN_MAX_S", "600"))
COOLDOWN_TIMEOUT_S = 90.0
COOLDOWN_SERVER_ERROR_S = 30.0
COOLDOWN_UNAVAILABLE_S = 300.0

# Wall-clock cap on ONE model request. The httpx read timeout cannot bound it:
# it resets on every byte, and a stalled deployment trickling bytes held a
# request for 248-292s (live 2026-10-04 MS3-c645523b51 / MS3-63801a5f52, with
# AGENT_LLM_TIMEOUT_S=240), i.e. the whole MISSION_TIMEOUT_S=300 budget. Healthy
# agent steps measured over 3 days of run events: p50 3.5s, p99 22s, max 43s.
# A request past the cap is a ModelAPIError, so the chain falls through to the
# next model instead of the mission dying.
AGENT_LLM_REQUEST_CAP_S = float(os.getenv("AGENT_LLM_REQUEST_CAP_S", "60"))


class ModelHealth:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._until: dict[str, float] = {}
        self._known: set[str] = set()
        self._strikes: dict[str, int] = {}

    def register(self, key: str) -> None:
        self._known.add(key)

    def cool(self, key: str, seconds: float) -> None:
        self._until[key] = self._clock() + seconds

    def recover(self, key: str) -> None:
        self._until.pop(key, None)
        self._strikes.pop(key, None)

    def rate_limited(self, key: str, base: float, hint: float | None = None) -> float:
        """Cooldown for a 429: doubles per consecutive strike (capped), never shorter
        than the provider's own retry-after hint."""
        strikes = self._strikes.get(key, 0) + 1
        self._strikes[key] = strikes
        seconds = min(base * 2 ** (strikes - 1), max(base, COOLDOWN_RATE_LIMITED_MAX_S))
        return max(seconds, hint or 0.0)

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


def retry_after_hint(exc: ModelAPIError) -> float | None:
    """Seconds the provider's standard ``Retry-After`` response header asks for, if
    it sent one in delta-seconds form."""
    headers = getattr(exc, "headers", None) or {}
    raw = next((v for k, v in headers.items() if str(k).lower() == "retry-after"), None)
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _spent_without_output(response: Any) -> bool:
    if getattr(response, "finish_reason", None) != "length":
        return False
    return not any(
        isinstance(part, ToolCallPart) or (isinstance(part, TextPart) and part.content.strip())
        for part in getattr(response, "parts", [])
    )


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
        if isinstance(exc, ModelHTTPError) and exc.status_code == 429:
            seconds = self._health.rate_limited(self._health_key, seconds, retry_after_hint(exc))
        if seconds:
            self._health.cool(self._health_key, seconds)

    async def request(self, *args: Any, **kwargs: Any) -> Any:
        self._gate()
        try:
            try:
                response = await self._capped(super().request(*args, **kwargs))
            except UnexpectedModelBehavior as exc:
                # A completion the client cannot parse (live 2026-10-07 MS3-4f7be7ba30:
                # an OpenRouter tail model answered 200 with choices=null) is this
                # model failing, not the mission: fall through to the next model, and
                # let PatientModel wait for the chain when it was the last one.
                raise ModelAPIError(self.model_name, f"malformed response: {exc}") from exc
            if _spent_without_output(response):
                # Live 2026-10-05 (MS3-3eeef3a493): a reasoning model used its whole
                # output budget thinking over a long thread and returned nothing; the
                # agent graph raised UnexpectedModelBehavior, which is not a model
                # error, so the mission failed instead of trying the next model.
                raise ModelAPIError(self.model_name, "hit its token limit before producing any output")
        except ModelAPIError as exc:
            self._record_failure(exc)
            raise
        self._health.recover(self._health_key)
        return response

    async def _capped(self, call: Awaitable[Any]) -> Any:
        cap = AGENT_LLM_REQUEST_CAP_S
        if cap <= 0:
            return await call
        try:
            return await asyncio.wait_for(call, timeout=cap)
        except TimeoutError as exc:
            raise ModelAPIError(self.model_name, f"no response within {cap:.0f}s") from exc

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
    async def request_stream(
        self, messages: Any, model_settings: Any, model_request_parameters: Any, run_context: Any = None
    ) -> AsyncIterator[StreamedResponse]:
        """The agent streams only so its event handler sees tool calls; the
        answer itself is never shown before validation. A live stream cannot
        fall back or be retried once it is open — pydantic-ai's FallbackModel
        propagates mid-stream failures — so a provider stall became a raw
        ReadTimeout that failed the mission and restarted it from scratch
        (live 2026-10-04: 658s for one CTR chart). Each step is therefore a
        plain request (cap, cooldown, fallback and patience all apply) replayed
        as a stream."""
        response = await self.request(messages, model_settings, model_request_parameters)
        yield ReplayedStreamedResponse(model_request_parameters=model_request_parameters, response=response)


@dataclass
class ReplayedStreamedResponse(StreamedResponse):
    """A finished ``ModelResponse`` presented as a stream: one start event per
    part, so the run's event handler (tool progress, final-result detection)
    behaves as it does on a live stream."""

    response: ModelResponse | None = None

    def __post_init__(self) -> None:
        assert self.response is not None
        self._usage = self.response.usage
        self.provider_response_id = self.response.provider_response_id
        self.provider_details = self.response.provider_details
        self.finish_reason = self.response.finish_reason
        self.metadata = self.response.metadata

    async def _get_event_iterator(self) -> AsyncIterator[ModelResponseStreamEvent]:
        assert self.response is not None
        for index, part in enumerate(self.response.parts):
            yield self._parts_manager.handle_part(vendor_part_id=index, part=part)

    async def close_stream(self) -> None:
        pass

    @property
    def model_name(self) -> str:
        assert self.response is not None
        return self.response.model_name or ""

    @property
    def provider_name(self) -> str | None:
        assert self.response is not None
        return self.response.provider_name

    @property
    def provider_url(self) -> str | None:
        assert self.response is not None
        return self.response.provider_url

    @property
    def timestamp(self) -> datetime:
        assert self.response is not None
        return self.response.timestamp
