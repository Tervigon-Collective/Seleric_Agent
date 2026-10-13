"""The single reasoning loop: one PydanticAI agent run over the Core MCP tools and the local helpers.

Shape (test_agent's, in PydanticAI): user question -> model <-> tools -> answer text. Then the answer's figures are
checked deterministically against the turn's tool results; if any fail, ONE corrective pass runs with the exact
problems; whatever still fails ships with an "unverified" note. Budgets (LLM steps, tool calls, wall time) withdraw
the tools so the model answers from the evidence it has instead of looping. Cancellation is checked before every
model request.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from pydantic_ai import Agent, RunContext
from pydantic_ai.capabilities import PrepareTools
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models import Model
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.usage import RunUsage, UsageLimits

from seleric_swarm.agent.answer_text import replace_metric_ids, round_long_decimals
from seleric_swarm.agent.dependencies import SelericDeps
from seleric_swarm.agent.grounding import (
    correction_message,
    grounding,
    lost_substance,
    problems,
    unverified_note,
)
from seleric_swarm.agent.local_tools import build_local_tools
from seleric_swarm.agent.mcp_toolset import TurnState, build_mcp_tools
from seleric_swarm.agent.model import begin_loop_effort
from seleric_swarm.agent.progress import emit_progress, has_progress_sink, progress_handler
from seleric_swarm.cancellation import MissionCancelledError

_log = logging.getLogger("seleric.agent.loop")

MAX_STEPS = int(os.getenv("AGENT_MAX_STEPS", "10"))  # LLM calls per turn, correction pass excluded
MAX_TOOL_CALLS = int(os.getenv("AGENT_MAX_TOOL_CALLS", "20"))
TURN_BUDGET_S = float(os.getenv("AGENT_TURN_BUDGET_S", "150"))  # soft: tools are withdrawn past this
VERIFY_NUMBERS = os.getenv("AGENT_VERIFY_NUMBERS", "1") == "1"
BUDGET_NOTICE = ("Tool budget reached. Write the final answer now from the evidence gathered (no tool calls); "
                 "state what remains unverified.")
DEGENERATE_ANSWER = ("I couldn't produce a reliable answer: the model returned unusable output twice. "
                     "Please retry.")


@dataclass
class LoopResult:
    answer: str
    status: str  # completed | partial | failed
    state: TurnState
    grounding: dict[str, Any]
    issues: list[str]
    stats: dict[str, Any]
    limitations: list[str] = field(default_factory=list)
    forced_final: bool = False


def history_messages(turns: list[tuple[str, str]]) -> list[ModelMessage]:
    """[(role, text)] -> PydanticAI messages ('user' / 'assistant')."""
    out: list[ModelMessage] = []
    for role, text in turns:
        if not text.strip():
            continue
        if role == "user":
            out.append(ModelRequest(parts=[UserPromptPart(content=text)]))
        else:
            out.append(ModelResponse(parts=[TextPart(content=text)]))
    return out


def degenerate(text: str, finish_reason: str | None = None) -> bool:
    """Content-filtered, runaway repetition, or cut off at the length cap: never shipped as an answer."""
    words = "".join(ch if ch.isalnum() else " " for ch in text.lower()).split()
    return finish_reason == "content_filter" or (len(words) > 80 and len(set(words)) / len(words) < 0.3) or \
        finish_reason == "length"


def _finish_reason(messages: list[ModelMessage]) -> str | None:
    for m in reversed(messages):
        if isinstance(m, ModelResponse):
            return m.finish_reason
    return None


class _TimedModel(WrapperModel):
    """Records every model request (wall ms, tokens, tool calls asked for) into the turn's trace.

    Also stamps turn-relative ``at_ms`` so the debug console can show First token /
    Answer starts (test_agent's ``ttft_ms`` / ``answer_ttft_ms``). Non-streaming
    calls treat response-available as first token.
    """

    def __init__(
        self,
        wrapped: Model,
        calls: list[dict[str, Any]],
        *,
        turn_started: float,
        timing: dict[str, Any],
    ) -> None:
        super().__init__(wrapped)
        self._calls = calls
        self._turn_started = turn_started
        self._timing = timing

    def _record(self, *, call_started: float, response: ModelResponse, model_settings: Any) -> None:
        now = time.perf_counter()
        call_ms = int((now - call_started) * 1000)
        at_ms = int((now - self._turn_started) * 1000)
        if self._timing.get("ttft_ms") is None:
            self._timing["ttft_ms"] = at_ms
        self._timing["last_response_ms"] = at_ms
        usage = response.usage
        self._calls.append({
            "ms": call_ms,
            "ttft_ms": call_ms,  # non-stream: first byte ≈ full response
            "at_ms": at_ms,
            "model": response.model_name,
            "prompt_tokens": usage.input_tokens,
            "completion_tokens": usage.output_tokens,
            "cached_tokens": getattr(usage, "cache_read_tokens", None),
            "tool_calls": sum(1 for p in response.parts if isinstance(p, ToolCallPart)),
            "effort": (model_settings or {}).get("openai_reasoning_effort"),
            "finish": response.finish_reason,
        })

    async def request(self, messages: list[ModelMessage], model_settings: Any, params: Any) -> ModelResponse:
        t0 = time.perf_counter()
        response = await self.wrapped.request(messages, model_settings, params)
        self._record(call_started=t0, response=response, model_settings=model_settings)
        return response

    @asynccontextmanager
    async def request_stream(self, messages: list[ModelMessage], model_settings: Any, params: Any, run_context: Any = None) -> AsyncIterator[Any]:
        t0 = time.perf_counter()
        async with self.wrapped.request_stream(messages, model_settings, params, run_context) as streamed:
            yield streamed
        resp = streamed.get()
        self._record(call_started=t0, response=resp, model_settings=model_settings)


class _Budget:
    def __init__(self, state: TurnState, *, is_cancelled: Callable[[], bool], started: float) -> None:
        self.state, self.is_cancelled, self.started = state, is_cancelled, started
        self.forced = False

    def spent(self, usage: RunUsage) -> bool:
        return (usage.requests >= MAX_STEPS - 1 or self.state.tool_calls >= MAX_TOOL_CALLS
                or time.perf_counter() - self.started > TURN_BUDGET_S)


def _instructions(system_prompt: str, memories: list[str], prior_queries: list[str], budget: _Budget) -> list[Any]:
    def memory_block(_: RunContext[SelericDeps]) -> str:
        return ("What the user asked you to remember:\n- " + "\n- ".join(memories)) if memories else ""

    def prior_block(_: RunContext[SelericDeps]) -> str:
        return ("Queries already run in this conversation (use as parent_query_id when drilling down):\n"
                + "\n".join(prior_queries)) if prior_queries else ""

    def budget_block(_: RunContext[SelericDeps]) -> str:
        return BUDGET_NOTICE if budget.forced else ""

    return [system_prompt, memory_block, prior_block, budget_block]


async def run_turn(
    *,
    deps: SelericDeps,
    model: Model,
    gateway: Any,
    tool_defs: list[dict[str, Any]],
    system_prompt: str,
    question: str,
    history: list[ModelMessage],
    memories: list[str] | None = None,
    prior_queries: list[str] | None = None,
    prior_payloads: list[Any] | None = None,
    is_cancelled: Callable[[], bool] = lambda: False,
    snapshot_loader: Any = None,
    on_stream: Callable[[str, str], None] | None = None,
) -> LoopResult:
    begin_loop_effort()
    started = time.perf_counter()
    state = TurnState()
    budget = _Budget(state, is_cancelled=is_cancelled, started=started)
    usage = RunUsage()
    mission_id = deps.mission_id
    stats: dict[str, Any] = {"revisions": 0, "degenerate_retries": 0}
    timing: dict[str, Any] = {"ttft_ms": None, "last_response_ms": None}

    async def prepare(ctx: RunContext[SelericDeps], defs: list[ToolDefinition]) -> list[ToolDefinition]:
        if is_cancelled():
            raise MissionCancelledError(mission_id)
        if budget.spent(ctx.usage):
            budget.forced = True
            return []
        return defs

    tools = [*build_mcp_tools(gateway, tool_defs, state), *build_local_tools(state, snapshot_loader=snapshot_loader)]
    llm_calls: list[dict[str, Any]] = []
    agent: Agent[SelericDeps, str] = Agent(
        model=_TimedModel(model, llm_calls, turn_started=started, timing=timing),
        deps_type=SelericDeps,
        output_type=str,
        instructions=_instructions(system_prompt, memories or [], prior_queries or [], budget),
        name="seleric_agent",
        tools=tools,
        capabilities=[PrepareTools(prepare)],
        retries=2,
    )
    handler = progress_handler(mission_id) if has_progress_sink(mission_id) else None
    limits = UsageLimits(request_limit=MAX_STEPS + 6, tool_calls_limit=MAX_TOOL_CALLS + 8)

    async def run(prompt: str, messages: list[ModelMessage]) -> tuple[str, list[ModelMessage]]:
        result = await agent.run(
            prompt, deps=deps, message_history=messages, usage=usage, usage_limits=limits, event_stream_handler=handler
        )
        return (result.output or "").strip(), result.all_messages()

    limitations: list[str] = []
    emit_progress(mission_id, "agent.stage", "Working on it", {"stage": "agent"})
    try:
        answer, messages = await run(question, history)
        if degenerate(answer, _finish_reason(messages)):
            stats["degenerate_retries"] += 1
            emit_progress(mission_id, "agent.revising", "The model returned unusable output — retrying once", {"reason": "degenerate"})
            answer, messages = await run(question, history)
            if degenerate(answer, _finish_reason(messages)):
                answer, limitations = DEGENERATE_ANSWER, ["DEGENERATE_OUTPUT"]
    except UsageLimitExceeded as exc:
        _log.info("v3_loop_usage_limit mission=%s detail=%s", mission_id, exc)
        answer = ("I ran out of working budget before finishing. Here is what the data showed so far: "
                  + ("; ".join(e["tool"] for e in state.evidence[:5]) or "nothing yet") + ". Please narrow the question.")
        messages, limitations = history, ["EXECUTION_LIMIT_EXCEEDED"]

    pool = [*(prior_payloads or []), *state.payloads]
    first = grounding(answer, pool) if pool and not limitations else None
    issues = problems(first) if first is not None else []
    if VERIFY_NUMBERS and issues:
        # Deterministic check, not a critic model: one corrective pass only when the figures fail to check out.
        stats["revisions"] += 1
        emit_progress(mission_id, "agent.revising", "Checking the figures against the data", {"reason": "grounding", "issues": issues})
        budget.forced = False
        try:
            revised, _ = await run(correction_message(issues), messages)
        except UsageLimitExceeded:
            revised = ""
        if revised and not degenerate(revised):
            pool = [*(prior_payloads or []), *state.payloads]
            second = grounding(revised, pool)
            if first is not None and lost_substance(first, second):
                stats["correction_discarded"] = True  # the rewrite dropped most of the verified content: keep the draft
            else:
                answer, issues = revised, problems(second)
    if issues:  # still failing after the one pass: say so in the answer instead of shipping them silently
        answer += unverified_note(issues)
        limitations.append("UNVERIFIED_FIGURES")
    # Internal metric ids that slipped into the prose are written as their catalogue names (a known fix, not a model pass).
    answer = round_long_decimals(replace_metric_ids(answer, {m.id: m.label for m in deps.catalogue.metrics if m.label}))
    final = grounding(answer, [*(prior_payloads or []), *state.payloads]) if state.payloads else grounding(answer, [])
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    # First token = first model output of the turn; Answer starts = last model
    # output (the call that produced / revised the final prose). Falls back to
    # elapsed when the answer is dumped without an LLM step (limit / empty).
    ttft_ms = timing.get("ttft_ms")
    answer_ttft_ms = timing.get("last_response_ms")
    if answer.strip() and answer_ttft_ms is None:
        answer_ttft_ms = elapsed_ms
    stats.update(
        llm_calls=usage.requests, tool_calls=state.tool_calls, duplicate_calls=state.duplicates,
        prompt_tokens=usage.input_tokens, completion_tokens=usage.output_tokens,
        cached_tokens=getattr(usage, "cache_read_tokens", None), tool_ms=state.tool_ms, llm_steps=llm_calls,
        elapsed_ms=elapsed_ms, total_ms=elapsed_ms,
        ttft_ms=ttft_ms, answer_ttft_ms=answer_ttft_ms,
        errors=state.errors[:10],
    )
    status = "failed" if not answer.strip() else ("partial" if limitations else "completed")
    if on_stream is not None and answer.strip():
        on_stream("delta", answer)
    return LoopResult(answer=answer, status=status, state=state, grounding=final, issues=issues, stats=stats,
                      limitations=limitations, forced_final=budget.forced)
