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
