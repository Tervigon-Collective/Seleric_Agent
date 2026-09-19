"""Sprint 4 Profile A: the agent actually has every implemented toolset.

This is a wiring test, not an integration test — it proves 15 real tool
functions register on ``SelericAgent`` without a schema error (the bug this
test guards: ``RunContext[SelericDeps]`` annotations that only resolved
under ``TYPE_CHECKING`` blew up at real tool-registration time with
``NameError: name 'SelericDeps' is not defined``, since nothing exercised
that path before this wiring existed). It does not call any tool for real
(no live MCP, no LLM) — that's ``tests/replay/``'s job.
"""

from __future__ import annotations

from seleric_swarm.agent.agent import TOOLS, build_seleric_agent


def test_all_fifteen_tools_are_listed() -> None:
    assert len(TOOLS) == 15


def test_all_tools_register_on_the_agent() -> None:
    agent = build_seleric_agent()
    names = set(agent._function_toolset.tools.keys())
    assert names == {
        "search_semantics",
        "get_metric_definition",
        "query_metrics",
        "drilldown",
        "compare_periods",
        "detect_anomalies",
        "estimate_effect",
        "refute_estimate",
        "forecast",
        "predict_ltv",
        "predict_propensity",
        "propose_action",
        "validate",
        "preview",
        "commit_action",
    }


async def test_stub_model_still_calls_zero_tools() -> None:
    # call_tools=[] on the stub model must still hold even with 15 real
    # tools registered -- the 0%-traffic path stays side-effect-free.
    # "final_result" is pydantic_ai's own internal structured-output call,
    # not one of the 15 real tools -- excluded, not counted as a tool call.
    agent = build_seleric_agent()
    result = await agent.run("hello")
    assert result.output.mission_id == "stub"
    messages = result.all_messages()
    tool_calls = [
        part.tool_name
        for message in messages
        for part in getattr(message, "parts", [])
        if type(part).__name__ == "ToolCallPart" and part.tool_name != "final_result"
    ]
    assert tool_calls == []
