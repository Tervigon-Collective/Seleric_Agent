import asyncio

from seleric_swarm.agent.local_tools import _tracked
from seleric_swarm.agent.mcp_toolset import TurnState
from seleric_swarm.agent.output import ToolResult


def test_a_helper_that_returns_failure_is_recorded_in_the_turn_errors():
    state = TurnState()

    async def run_python(**_: object) -> ToolResult:
        return ToolResult(success=False, summary="KeyError: 'value_prev'", error_code="sandbox_error")

    async def ok_tool(**_: object) -> ToolResult:
        return ToolResult(success=True, summary="fine")

    asyncio.run(_tracked(state, run_python)(code="x"))
    asyncio.run(_tracked(state, ok_tool)())
    assert state.errors == ["run_python: KeyError: 'value_prev'"]
    assert [s["ok"] for s in state.steps if s["kind"] == "tool_return"] == [False, True]
