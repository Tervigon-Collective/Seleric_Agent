"""Mission tool budget (2026-10-06, MS3-486986e598).

"give me in depth analysis of ad performance of this month" needed 32 tool
calls. The lookup budget is 100, but it was split across the initial run and
3 possible revisions up front, so the FIRST run got 25 and the mission failed
with nothing shown — although no revision ever ran and 24 queries had already
returned evidence.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from seleric_swarm.agent.dependencies import ExecutionLimits, NullMcpClient, SelericDeps
from seleric_swarm.agent.output import MissionResult
from seleric_swarm.agent.validation import run_validated_mission
from seleric_swarm.conversations.contracts import ContextBundle, Principal, PrincipalAuthMethod
from seleric_swarm.state.artifacts import InMemoryArtifactStore


def _deps(*, tool_calls: int, revisions: int = 3) -> SelericDeps:
    return SelericDeps(
        mission_id="MS3-budget",
        as_of=datetime.now(UTC),
        principal=Principal(
            principal_id="p1",
            workspace_id="ws-1",
            user_id="user-1",
            authenticated=False,
            auth_method=PrincipalAuthMethod.ANONYMOUS,
        ),
        thread_id="thread-1",
        run_id="run-1",
        trace_id="trace-1",
        context=ContextBundle(),
        mcp_client=NullMcpClient(),
        artifact_store=InMemoryArtifactStore(),
        limits=ExecutionLimits(max_tool_calls=tool_calls, max_validation_revisions=revisions),
    )


def _final(text: str, status: str = "completed") -> ModelResponse:
    return ModelResponse(
        parts=[ToolCallPart("final_result", {"status": status, "final_response": text, "evidence_ids": []})]
    )


def _fetched(messages: list) -> int:
    return sum(
        1 for m in messages for p in m.parts if isinstance(p, ToolReturnPart) and p.tool_name == "fetch"
    )


def _prompts(messages: list) -> str:
    return " ".join(
        str(p.content) for m in messages for p in m.parts if p.part_kind == "user-prompt"
    )


def _agent(model) -> tuple[Agent, list[int]]:
    agent = Agent(FunctionModel(model), deps_type=SelericDeps, output_type=MissionResult)
    calls: list[int] = []

    @agent.tool_plain
    def fetch(i: int) -> str:
        calls.append(i)
        return "rows"

    return agent, calls


@pytest.mark.asyncio
async def test_first_run_gets_the_whole_budget() -> None:
    def model(messages: list, _info: AgentInfo) -> ModelResponse:
        done = _fetched(messages)
        if done < 7:
            return ModelResponse(parts=[ToolCallPart("fetch", {"i": done})])
        return _final("the answer")

    agent, calls = _agent(model)
    # 8 calls with 3 revisions allowed: the old up-front split left the first run 2.
    result = await run_validated_mission(agent, _deps(tool_calls=8), "q")
    assert result.status == "completed"
    assert len(calls) == 7


@pytest.mark.asyncio
async def test_spent_budget_answers_from_fetched_evidence_as_partial() -> None:
    def model(messages: list, _info: AgentInfo) -> ModelResponse:
        if "tool budget for this question is used up" in _prompts(messages):
            return _final("what the fetched evidence shows")
        done = _fetched(messages)
        return ModelResponse(parts=[ToolCallPart("fetch", {"i": done + k}) for k in range(3)])

    agent, calls = _agent(model)
    result = await run_validated_mission(agent, _deps(tool_calls=7), "q")
    assert result.status == "partial"
    assert result.final_response == "what the fetched evidence shows"
    assert result.limitations[0] == "TOOL_BUDGET_EXHAUSTED"
    assert result.error_code is None
    assert len(calls) <= 7


@pytest.mark.asyncio
async def test_wrap_up_that_still_calls_tools_fails_with_a_clear_message() -> None:
    def model(messages: list, _info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart("fetch", {"i": _fetched(messages)})])

    agent, calls = _agent(model)
    result = await run_validated_mission(agent, _deps(tool_calls=3), "q")
    assert result.status == "failed"
    assert result.error_code == "EXECUTION_LIMIT_EXCEEDED"
    assert "narrower slice" in result.final_response
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_revisions_draw_on_the_same_mission_budget() -> None:
    def model(messages: list, _info: AgentInfo) -> ModelResponse:
        done = _fetched(messages)
        # every attempt wants 4 more fetches, then answers with an empty (rejected) draft
        if done % 4 != 0 or not messages[-1].parts or messages[-1].parts[0].part_kind == "user-prompt":
            return ModelResponse(parts=[ToolCallPart("fetch", {"i": done})])
        return _final("")

    agent, calls = _agent(model)
    result = await run_validated_mission(agent, _deps(tool_calls=10, revisions=3), "q")
    assert len(calls) <= 10
    assert result.status in ("failed", "partial")
