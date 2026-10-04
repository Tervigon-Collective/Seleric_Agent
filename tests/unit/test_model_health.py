"""Cooldowns keep a rate-limited / hanging model from being re-tried on every step."""

from __future__ import annotations

import pytest
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError
from pydantic_ai.providers.openai import OpenAIProvider

from seleric_swarm.agent.model_health import (
    COOLDOWN_RATE_LIMITED_S,
    COOLDOWN_TIMEOUT_S,
    HealthGatedChatModel,
    ModelHealth,
    cooldown_for,
)


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _health() -> tuple[ModelHealth, _Clock]:
    clock = _Clock()
    health = ModelHealth(clock)
    for key in ("a", "b", "c"):
        health.register(key)
    return health, clock


def test_a_cooling_model_is_skipped_until_its_cooldown_ends():
    health, clock = _health()
    health.cool("a", 45.0)
    assert health.should_skip("a")
    assert not health.should_skip("b")
    clock.now += 46.0
    assert not health.should_skip("a")


def test_recovery_clears_the_cooldown_early():
    health, _ = _health()
    health.cool("a", 45.0)
    health.recover("a")
    assert not health.should_skip("a")


def test_when_every_model_is_cooling_none_is_skipped():
    health, _ = _health()
    for key in ("a", "b", "c"):
        health.cool(key, 60.0)
    assert not any(health.should_skip(key) for key in ("a", "b", "c"))


def test_cooldown_reflects_the_failure_kind():
    rate_limited = ModelHTTPError(429, "m", body={})
    assert cooldown_for(rate_limited) == COOLDOWN_RATE_LIMITED_S
    assert cooldown_for(ModelHTTPError(503, "m", body={})) > 0
    assert cooldown_for(ModelHTTPError(400, "m", body={})) == 0.0
    for status in (401, 402, 403):  # out of credits / bad key must not be re-hit every step
        assert cooldown_for(ModelHTTPError(status, "m", body={})) >= 300.0
    assert cooldown_for(ModelAPIError("m", "Request timed out.")) == COOLDOWN_TIMEOUT_S


@pytest.mark.asyncio
async def test_gated_model_fails_instantly_while_cooling_without_a_network_call():
    health, _ = _health()
    model = HealthGatedChatModel(
        "gpt-x",
        provider=OpenAIProvider(api_key="unused", base_url="http://127.0.0.1:9/v1"),
        health=health,
        health_key="a",
    )
    health.cool("a", 60.0)

    with pytest.raises(ModelAPIError, match="cooling down"):
        await model.request([], None, None)  # type: ignore[arg-type]


# --- PatientModel: a chain-wide transient failure waits instead of killing the mission ---------------
# live thread_e75c2615: LLM_RATE_LIMITED failed the mission twice; each run retry restarted from scratch.

from contextlib import asynccontextmanager  # noqa: E402

from pydantic_ai.exceptions import FallbackExceptionGroup  # noqa: E402
from pydantic_ai.models.test import TestModel  # noqa: E402

from seleric_swarm.agent.model_health import PatientModel, is_transient  # noqa: E402


def _rate_limited() -> FallbackExceptionGroup:
    return FallbackExceptionGroup(
        "All models from FallbackModel failed",
        [ModelHTTPError(429, "a", body="rate"), ModelHTTPError(402, "or", body="credits")],
    )


class _Flaky(TestModel):
    def __init__(self, failures: list[BaseException]) -> None:
        super().__init__()
        self.failures = failures
        self.calls = 0

    async def request(self, *args, **kwargs):
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return "ok"

    @asynccontextmanager
    async def request_stream(self, *args, **kwargs):
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        yield "stream"


def _patient(inner: _Flaky, budget: float = 60.0):
    health, clock = _health()
    waits: list[float] = []

    async def sleep(s: float) -> None:
        waits.append(s)
        clock.now += s

    return PatientModel(inner, health=health, budget_s=budget, sleep=sleep, clock=clock), health, clock, waits


def test_transient_means_something_in_the_chain_can_heal():
    assert is_transient(_rate_limited())
    assert is_transient(ModelAPIError("a", "skipped: model is cooling down after a failure"))
    assert not is_transient(FallbackExceptionGroup("x", [ModelHTTPError(400, "a", body="bad")]))
    assert not is_transient(ModelHTTPError(402, "or", body="credits"))


async def test_same_request_is_retried_after_the_first_cooldown_ends():
    inner = _Flaky([_rate_limited()])
    model, health, _clock, waits = _patient(inner)
    for key in ("a", "b", "c"):
        health.cool(key, 30.0 if key == "a" else COOLDOWN_RATE_LIMITED_S)
    assert await model.request([], None, None) == "ok"
    assert inner.calls == 2 and waits == [20.0]  # capped single wait; the next call finds "a" ready


async def test_gives_up_when_the_budget_is_spent_or_the_failure_is_permanent():
    inner = _Flaky([_rate_limited()] * 10)
    model, _h, _c, waits = _patient(inner, budget=5.0)
    with pytest.raises(FallbackExceptionGroup):
        await model.request([], None, None)
    assert sum(waits) <= 5.0

    bad = _Flaky([ModelHTTPError(401, "a", body="key")])
    model, _h, _c, waits = _patient(bad)
    with pytest.raises(ModelHTTPError):
        await model.request([], None, None)
    assert waits == [] and bad.calls == 1


async def test_opening_a_stream_is_retried():
    inner = _Flaky([_rate_limited()])
    model, _h, _c, waits = _patient(inner)
    async with model.request_stream([], None, None) as streamed:
        assert streamed == "stream"
    assert inner.calls == 2 and len(waits) == 1
