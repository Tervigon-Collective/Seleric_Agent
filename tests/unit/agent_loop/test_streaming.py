"""Live token streaming of the answer: deltas while it is written, no repeat, reset when it changes, stall -> unstreamed."""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from seleric_swarm.agent.loop import run_turn
from seleric_swarm.agent.model_health import PatientModel
from seleric_swarm.state.artifacts import InMemoryArtifactStore

from .test_loop import DEFS, FakeGateway, _deps

ANSWER = "Net sales were ₹42,177 yesterday, up from ₹34,377 the day before, a rise of ₹7,800 (+22.7%) on stronger order volume."


def _has_result(messages: list[ModelMessage]) -> bool:
    return any(isinstance(p, ToolReturnPart) for m in messages for p in m.parts)


def _chunks(text: str, n: int = 9) -> list[str]:
    return [text[i:i + n] for i in range(0, len(text), n)]


def _model(*, answer: str = ANSWER, stall_after: int | None = None, fail_open: list[Exception] | None = None) -> PatientModel:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[Any]:
        if fail_open:
            raise fail_open.pop(0)
        if not _has_result(messages):
            yield {0: DeltaToolCall(name="metrics_query", json_args='{"measures": ["net_sales"]}', tool_call_id="c1")}
            return
        for i, piece in enumerate(_chunks(answer)):
            if stall_after is not None and i == stall_after:
                raise RuntimeError("connection reset by peer")
            yield piece

    def plain(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if not _has_result(messages):
            return ModelResponse(parts=[ToolCallPart("metrics_query", {"measures": ["net_sales"]}, tool_call_id="c1")])
        return ModelResponse(parts=[TextPart(answer)])

    async def no_sleep(_: float) -> None:
        return None

    return PatientModel(FunctionModel(plain, stream_function=stream), sleep=no_sleep, budget_s=30)


async def _run(model: PatientModel, events: list[tuple[str, str]]):
    return await run_turn(
        deps=_deps(InMemoryArtifactStore()), model=model, gateway=FakeGateway(), tool_defs=DEFS, system_prompt="sys",
        question="net sales?", history=[], on_stream=lambda kind, text="": events.append((kind, text)),
    )


def _shown(events: list[tuple[str, str]]) -> str:
    """What the user's screen holds after the events (a reset clears it)."""
    shown = ""
    for kind, text in events:
        shown = "" if kind == "reset" else shown + text
    return shown


@pytest.mark.asyncio
async def test_answer_arrives_as_deltas_and_is_not_repeated_at_the_end():
    events: list[tuple[str, str]] = []
    result = await _run(_model(), events)
    deltas = [t for k, t in events if k == "delta"]
    assert len(deltas) >= 2, events  # progressive, not one block
    assert not any(k == "reset" for k, _ in events)
    assert _shown(events).strip() == result.answer.strip() == ANSWER
    assert isinstance(result.stats["answer_ttft_ms"], int) and result.stats["answer_ttft_ms"] < result.stats["elapsed_ms"] + 1


@pytest.mark.asyncio
async def test_a_changed_final_text_replaces_what_was_streamed():
    events: list[tuple[str, str]] = []
    result = await _run(_model(answer="Net sales were ₹99,999 yesterday, a rise on the day before, driven by order volume."), events)
    # the draft carried an unmatched figure: a correction pass ran, so the shown draft was withdrawn
    assert any(k == "reset" for k, _ in events)
    assert _shown(events).strip() == result.answer.strip()


@pytest.mark.asyncio
async def test_a_stream_that_breaks_mid_answer_is_redone_unstreamed_and_the_user_sees_one_answer():
    events: list[tuple[str, str]] = []
    result = await _run(_model(stall_after=10), events)
    assert result.status == "completed" and result.answer == ANSWER
    assert result.stats.get("stream_fallbacks") == 1
    assert any(k == "reset" for k, _ in events)
    assert _shown(events).strip() == ANSWER


@pytest.mark.asyncio
async def test_a_failure_before_the_first_token_is_retried_not_failed():
    events: list[tuple[str, str]] = []
    result = await _run(_model(fail_open=[ModelAPIError("m", "429 rate limited")]), events)
    assert result.status == "completed" and result.answer == ANSWER
    assert not result.stats.get("stream_fallbacks")
