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


async def test_a_stream_is_a_retried_request_replayed():
    """A live stream cannot fall back once open, so each agent step is a plain
    request (retried / capped / falling back) replayed as a stream."""
    from pydantic_ai.messages import ModelResponse, PartStartEvent, TextPart, ToolCallPart
    from pydantic_ai.models import ModelRequestParameters

    reply = ModelResponse(parts=[TextPart("hi"), ToolCallPart("final_result", {"a": 1})], model_name="m")
    inner = _Flaky([_rate_limited()])
    inner.request = _counting(inner, reply)
    model, _h, _c, waits = _patient(inner)
    async with model.request_stream([], None, ModelRequestParameters()) as streamed:
        events = [e async for e in streamed]
    assert inner.calls == 2 and len(waits) == 1
    assert [type(e) for e in events if isinstance(e, PartStartEvent)] == [PartStartEvent, PartStartEvent]
    assert streamed.get().parts == reply.parts


def _counting(inner: _Flaky, reply):
    async def request(*args, **kwargs):
        inner.calls += 1
        if inner.failures:
            raise inner.failures.pop(0)
        return reply

    return request


# --- wall-clock cap: a stalled deployment falls through to the next model --------------------------
# live 2026-10-04 MS3-c645523b51: a trickling stream held one request 256s (read timeout resets per
# byte), the mission died with a raw ReadTimeout and restarted from scratch — 658s for one chart.


def _gated(health, key: str):
    from pydantic_ai.providers.openai import OpenAIProvider

    from seleric_swarm.agent.model_health import HealthGatedChatModel

    return HealthGatedChatModel(key, provider=OpenAIProvider(api_key="x"), health=health, health_key=key)


async def test_a_request_past_the_cap_is_a_model_error_that_cools_the_model(monkeypatch):
    import asyncio

    from pydantic_ai.models.openai import OpenAIChatModel

    from seleric_swarm.agent import model_health

    async def stall(self, *args, **kwargs):
        await asyncio.sleep(5)

    monkeypatch.setattr(OpenAIChatModel, "request", stall)
    monkeypatch.setattr(model_health, "AGENT_LLM_REQUEST_CAP_S", 0.05)
    health, _clock = _health()
    gated = _gated(health, "slow")
    _gated(health, "other")
    with pytest.raises(ModelAPIError, match="no response within"):
        await gated.request([], None, None)
    assert health.should_skip("slow")


async def test_an_agent_run_survives_a_stalled_first_model(monkeypatch):
    """End to end through the agent graph with an event handler (the path the
    mission uses): the stalled model is abandoned at the cap and the next one
    answers; tool progress events still arrive."""
    import asyncio

    from pydantic_ai import Agent
    from pydantic_ai.messages import FunctionToolCallEvent, ModelResponse, TextPart, ToolCallPart
    from pydantic_ai.models.fallback import FallbackModel
    from pydantic_ai.models.function import FunctionModel
    from pydantic_ai.models.openai import OpenAIChatModel

    from seleric_swarm.agent import model_health

    async def stall(self, *args, **kwargs):
        await asyncio.sleep(5)

    monkeypatch.setattr(OpenAIChatModel, "request", stall)
    monkeypatch.setattr(model_health, "AGENT_LLM_REQUEST_CAP_S", 0.05)
    health, _clock = _health()

    def answer(messages, info):
        if len(messages) == 1:
            return ModelResponse(parts=[ToolCallPart("lookup", {})])
        return ModelResponse(parts=[TextPart("done")])

    chain = FallbackModel(_gated(health, "slow"), FunctionModel(answer))
    agent = Agent(PatientModel(chain, health=health))

    @agent.tool_plain
    def lookup() -> str:
        return "42"

    seen: list[str] = []

    async def handler(_ctx, events):
        async for event in events:
            if isinstance(event, FunctionToolCallEvent):
                seen.append(event.part.tool_name)

    run = await agent.run("q", event_stream_handler=handler)
    assert run.output == "done"
    assert seen == ["lookup"]


async def test_a_truncated_empty_response_falls_through_to_the_next_model(monkeypatch):
    """Live MS3-3eeef3a493: finish_reason=length with only reasoning failed the
    mission (UnexpectedModelBehavior) instead of trying the next model."""
    from pydantic_ai import Agent
    from pydantic_ai.messages import ModelResponse, TextPart, ThinkingPart
    from pydantic_ai.models.fallback import FallbackModel
    from pydantic_ai.models.function import FunctionModel
    from pydantic_ai.models.openai import OpenAIChatModel

    async def truncated(self, *args, **kwargs):
        return ModelResponse(parts=[ThinkingPart("...")], finish_reason="length", model_name="m")

    monkeypatch.setattr(OpenAIChatModel, "request", truncated)
    health, _clock = _health()
    chain = FallbackModel(_gated(health, "thinker"), FunctionModel(lambda m, i: ModelResponse(parts=[TextPart("ok")])))
    run = await Agent(PatientModel(chain, health=health)).run("q")
    assert run.output == "ok"


def test_repeated_429s_back_off_and_a_success_resets():
    # 2026-10-06: DeepSeek-V4-Pro's per-minute token quota re-tripped within a
    # flat 45s cooldown, so most missions' first step ate a 429.
    from seleric_swarm.agent.model_health import COOLDOWN_RATE_LIMITED_MAX_S

    health, _ = _health()
    waits = [health.rate_limited("a", COOLDOWN_RATE_LIMITED_S) for _ in range(6)]
    assert waits[:3] == [COOLDOWN_RATE_LIMITED_S, 2 * COOLDOWN_RATE_LIMITED_S, 4 * COOLDOWN_RATE_LIMITED_S]
    assert max(waits) == COOLDOWN_RATE_LIMITED_MAX_S
    health.recover("a")
    assert health.rate_limited("a", COOLDOWN_RATE_LIMITED_S) == COOLDOWN_RATE_LIMITED_S


def test_retry_after_hint_is_a_floor():
    from seleric_swarm.agent.model_health import retry_after_hint

    exc = ModelHTTPError(429, "m", body={}, headers={"Retry-After": "120"})
    assert retry_after_hint(exc) == 120.0
    health, _ = _health()
    assert health.rate_limited("a", COOLDOWN_RATE_LIMITED_S, retry_after_hint(exc)) == 120.0
    assert retry_after_hint(ModelHTTPError(429, "m", body={})) is None
    dated = ModelHTTPError(429, "m", body={}, headers={"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"})
    assert retry_after_hint(dated) is None
