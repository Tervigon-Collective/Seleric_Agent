"""Once live data is known to be down, data tools are withdrawn from the model.

Live bug: with MCP unconfigured the agent kept calling query_metrics for 100-400s.
The guard is enforced in code (tool availability), not left to the prompt.
"""

from __future__ import annotations

import contextlib
import dataclasses
from datetime import UTC, datetime

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from seleric_swarm.agent.agent import _STILL_AVAILABLE_WITHOUT_LIVE_DATA, build_seleric_agent
from seleric_swarm.agent.dependencies import ExecutionLimits, SelericDeps
from seleric_swarm.conversations.contracts import ContextBundle, Principal
from seleric_swarm.state.artifacts import InMemoryArtifactStore
from seleric_swarm.toolsets import semantic


class _UnconfiguredMcp:
    async def call(self, *, agent_id: str, capability: str, arguments: dict) -> dict:
        raise NotImplementedError(f"MCP capability not available: {capability}")


def _deps() -> SelericDeps:
    return SelericDeps(
        mission_id="m-guard",
        as_of=datetime(2026, 9, 24, tzinfo=UTC),
        principal=Principal(principal_id="p", workspace_id="w", user_id="u"),
        thread_id="t",
        run_id="r",
        trace_id="tr",
        context=ContextBundle(),
        mcp_client=_UnconfiguredMcp(),
        artifact_store=InMemoryArtifactStore(),
        limits=ExecutionLimits(),
    )


@pytest.mark.asyncio
async def test_data_tools_disappear_after_the_first_unavailable_fetch():
    offered: list[set[str]] = []

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        offered.append({t.name for t in info.function_tools})
        if len(offered) == 1:
            return ModelResponse(
                parts=[ToolCallPart("query_metrics", {"metric_id": "net_sales"})]
            )
        return ModelResponse(parts=[TextPart("done")])

    agent = build_seleric_agent(model=FunctionModel(model))
    deps = _deps()
    # The plain-text reply is not a valid MissionResult, so the run itself may
    # error; only the tools offered to the model are under test here.
    with contextlib.suppress(Exception):
        await agent.run("net sales yesterday", deps=deps)

    assert len(offered) >= 2
    assert "query_metrics" in offered[0]
    assert deps.call_counts.get(semantic.LIVE_DATA_UNAVAILABLE) == 1
    assert offered[1] == offered[1] & _STILL_AVAILABLE_WITHOUT_LIVE_DATA
    assert "query_metrics" not in offered[1]
    assert "search_semantics" in offered[1]


@pytest.mark.asyncio
async def test_a_conversational_mission_is_offered_no_tools_at_all():
    """Live: "thanks!" made the agent look up and report a CAC figure."""
    from seleric_swarm.agent.agent import CONVERSATIONAL

    offered: list[set[str]] = []

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        offered.append({t.name for t in info.function_tools})
        return ModelResponse(parts=[TextPart("You're welcome!")])

    deps = _deps()
    deps.call_counts[CONVERSATIONAL] = 1
    with contextlib.suppress(Exception):
        await build_seleric_agent(model=FunctionModel(model)).run("thanks!", deps=deps)

    assert offered and offered[0] == set()


@pytest.mark.asyncio
async def test_definition_lookups_are_capped_without_raising():
    deps = _deps()

    class Ctx:
        pass

    ctx = Ctx()
    ctx.deps = deps  # type: ignore[attr-defined]
    results = [semantic._definition_budget_spent(ctx) for _ in range(semantic._MAX_DEFINITION_LOOKUPS + 2)]  # type: ignore[arg-type]

    assert all(r is None for r in results[: semantic._MAX_DEFINITION_LOOKUPS])
    spent = results[semantic._MAX_DEFINITION_LOOKUPS]
    assert spent is not None and spent.success is True
    assert "exhausted" in spent.summary


class _RowsMcp:
    def __init__(self) -> None:
        self.calls = 0

    async def call(self, *, agent_id: str, capability: str, arguments: dict) -> dict:
        self.calls += 1
        return {"rows": [{"net_sales": "1000"}], "provenance": {}}


@pytest.mark.asyncio
async def test_query_metrics_is_withdrawn_after_the_duplicate_hard_stop():
    """Live 2026-09-25: a model repeating an identical query_metrics call hit the
    ModelRetry hard stop until pydantic_ai's retry limit killed the mission."""
    offered: list[set[str]] = []
    call = {"metric_id": "net_sales", "dimensions": {}}

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        tools = {t.name for t in info.function_tools}
        offered.append(tools)
        if "query_metrics" in tools:
            return ModelResponse(parts=[ToolCallPart("query_metrics", call)])
        return ModelResponse(parts=[TextPart("done")])

    agent = build_seleric_agent(model=FunctionModel(model))
    mcp = _RowsMcp()
    deps = dataclasses.replace(_deps(), mcp_client=mcp)
    with contextlib.suppress(Exception):
        await agent.run("net sales yesterday", deps=deps)

    assert deps.call_counts.get(semantic.QUERY_LOOP_STOPPED) == 1
    # fetch, nudge, hard stop — then the tool is gone instead of a 4th call.
    assert [("query_metrics" in t) for t in offered[:4]] == [True, True, True, False]
    assert mcp.calls == 1
