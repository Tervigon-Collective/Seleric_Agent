from __future__ import annotations

import pytest
from pydantic import BaseModel

from seleric_swarm.llm.errors import LLMError, LLMErrorCode
from seleric_swarm.llm.gateway import LLMGateway
from seleric_swarm.llm.model_spec import ModelSpec
from seleric_swarm.llm.port import ChatMessage, LLMRequest, LLMResponse, TokenUsage


class _ScriptedPort:
    """Test double: complete() looks up a canned outcome by request.model."""

    def __init__(self, outcomes: dict[str, Exception | LLMResponse]) -> None:
        self.outcomes = outcomes
        self.calls: list[str] = []

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls.append(request.model)
        outcome = self.outcomes[request.model]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def complete_structured(self, request: LLMRequest, schema):  # noqa: ANN001
        raise NotImplementedError


def _ok(model: str) -> LLMResponse:
    return LLMResponse(text="ok", model=model, usage=TokenUsage(total_tokens=1))


def _request(response_format: str = "text") -> LLMRequest:
    return LLMRequest(
        messages=[ChatMessage(role="user", content="hi")],
        model="primary",
        response_format=response_format,
    )


@pytest.mark.asyncio
async def test_falls_back_to_next_model_on_retryable_error():
    port = _ScriptedPort(
        {
            "primary": LLMError(LLMErrorCode.RATE_LIMIT, "429", retryable=True),
            "backup": _ok("backup"),
        }
    )
    gateway = LLMGateway(
        port, [ModelSpec(id="primary", priority=0), ModelSpec(id="backup", priority=1)]
    )
    response = await gateway.complete(_request())
    assert response.model == "backup"
    assert port.calls == ["primary", "backup"]


@pytest.mark.asyncio
async def test_non_retryable_error_does_not_fall_back():
    port = _ScriptedPort(
        {
            "primary": LLMError(LLMErrorCode.BAD_REQUEST, "400", retryable=False),
            "backup": _ok("backup"),
        }
    )
    gateway = LLMGateway(
        port, [ModelSpec(id="primary", priority=0), ModelSpec(id="backup", priority=1)]
    )
    with pytest.raises(LLMError) as exc_info:
        await gateway.complete(_request())
    assert exc_info.value.code == LLMErrorCode.BAD_REQUEST
    assert port.calls == ["primary"]


@pytest.mark.asyncio
async def test_all_models_failing_raises_last_error():
    port = _ScriptedPort(
        {
            "primary": LLMError(LLMErrorCode.TIMEOUT, "timeout", retryable=True),
            "backup": LLMError(LLMErrorCode.UNAVAILABLE, "down", retryable=True),
        }
    )
    gateway = LLMGateway(
        port, [ModelSpec(id="primary", priority=0), ModelSpec(id="backup", priority=1)]
    )
    with pytest.raises(LLMError) as exc_info:
        await gateway.complete(_request())
    assert exc_info.value.code == LLMErrorCode.UNAVAILABLE
    assert port.calls == ["primary", "backup"]


@pytest.mark.asyncio
async def test_capability_mismatch_skips_model_without_calling_it():
    port = _ScriptedPort({"backup": _ok("backup")})
    gateway = LLMGateway(
        port,
        [
            ModelSpec(id="primary", priority=0, supports_structured_output=False),
            ModelSpec(id="backup", priority=1, supports_structured_output=True),
        ],
    )
    response = await gateway.complete(_request(response_format="json_schema"))
    assert response.model == "backup"
    assert port.calls == ["backup"]


@pytest.mark.asyncio
async def test_circuit_opens_after_threshold_and_routes_around_unhealthy_model():
    port = _ScriptedPort(
        {
            "primary": LLMError(LLMErrorCode.RATE_LIMIT, "429", retryable=True),
            "backup": _ok("backup"),
        }
    )
    gateway = LLMGateway(
        port, [ModelSpec(id="primary", priority=0), ModelSpec(id="backup", priority=1)]
    )
    gateway._breakers["primary"].failure_threshold = 2  # ponytail: reach into internals for a fast test

    await gateway.complete(_request())
    await gateway.complete(_request())
    assert gateway._breakers["primary"].state == "open"

    port.calls.clear()
    await gateway.complete(_request())
    assert port.calls == ["backup"]  # primary skipped entirely — circuit open


@pytest.mark.asyncio
async def test_all_models_unhealthy_raises_unavailable_without_calling_any():
    port = _ScriptedPort({})
    gateway = LLMGateway(port, [ModelSpec(id="primary", priority=0)])
    for _ in range(gateway._breakers["primary"].failure_threshold):
        gateway._breakers["primary"].record_failure()
    assert gateway._breakers["primary"].state == "open"

    with pytest.raises(LLMError) as exc_info:
        await gateway.complete(_request())
    assert exc_info.value.code == LLMErrorCode.UNAVAILABLE
    assert port.calls == []
