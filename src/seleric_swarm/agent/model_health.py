"""Per-model cooldowns for the fallback chain.

``FallbackModel`` restarts from the first model on *every* request, so a model
that is rate-limited (429) or hanging (30s timeout) is retried and re-failed on
each step of a mission. That single behaviour turned a ~10s answer into 60-120s.
A failing model is instead skipped instantly until its cooldown ends.

If every model in the chain is cooling down, none is skipped: better to try a
recently-failed model than to fail the mission without a single call.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError
from pydantic_ai.models.openai import OpenAIChatModel

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
