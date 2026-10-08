"""Sprint 4 Profile A: the agent actually has every implemented toolset.

This is a wiring test, not an integration test — it proves every real tool
functions register on ``SelericAgent`` without a schema error (the bug this
test guards: ``RunContext[SelericDeps]`` annotations that only resolved
under ``TYPE_CHECKING`` blew up at real tool-registration time with
``NameError: name 'SelericDeps' is not defined``, since nothing exercised
that path before this wiring existed). It does not call any tool for real
(no live MCP, no LLM) — that's ``tests/replay/``'s job.
"""

from __future__ import annotations

from seleric_swarm.agent.agent import TOOLS, build_seleric_agent, unbacked_tools


def test_every_tool_is_registered() -> None:
    """18 tools (was 31). Same-purpose tools were merged (2026-10-07): metric discovery is
    ``find_metrics`` (concept resolver + glossary search + listing), the six
    calculations over fetched evidence are ``analyze(method=...)``; refute_estimate,
    actions.validate/preview and the always-refusing predict_ltv/predict_propensity
    are not registered. The read-only ad surfaces in ``ads.py`` stay unregistered.

    Counting here is what stops a toolset being written and then silently left
    unregistered — the test suite would stay green, because a tool nobody
    registers is a tool nobody tests.
    """
    assert len(TOOLS) == 19  # + exploration.explore_data


_ALL = {
    "find_metrics",
    "resolve_brand",
    "get_metric_definitions",
    "query_metrics",
    "semantic_sql",
    "drilldown",
    "analyze",
    "generate_visualization",
    "run_python",
    "diagnose_metric_change",
    "explore_data",
    "estimate_effect",
    "forecast",
    "propose_action",
    "commit_action",
    "search_knowledge",
    "get_experiment_history",
    "estimate_sample_size",
    "evaluate_experiment",
}


def test_all_tools_register_on_the_agent() -> None:
    agent = build_seleric_agent()
    assert set(agent._function_toolset.tools.keys()) == _ALL


def test_tools_with_nothing_behind_them_are_not_offered() -> None:
    # This deployment: writes off, no experiments registered, empty knowledge
    # corpus; the four approved forecast models keep forecast.
    hidden = unbacked_tools(allow_writes=False)
    assert hidden == {"propose_action", "commit_action", "get_experiment_history", "evaluate_experiment", "search_knowledge"}
    assert "propose_action" not in unbacked_tools(allow_writes=True)
    agent = build_seleric_agent(hidden=hidden)
    names = set(agent._function_toolset.tools.keys())
    assert names == _ALL - hidden
    assert "estimate_sample_size" in names and "forecast" in names


async def test_stub_model_still_calls_zero_tools() -> None:
    # call_tools=[] on the stub model must still hold even with 26 real
    # tools registered -- the 0%-traffic path stays side-effect-free.
    # "final_result" is pydantic_ai's own internal structured-output call,
    # not one of the real tools -- excluded, not counted as a tool call.
    agent = build_seleric_agent()
    result = await agent.run("hello")  # type: ignore[arg-type]
    assert result.output.mission_id == "stub"
    messages = result.all_messages()
    tool_calls = [
        part.tool_name
        for message in messages
        for part in getattr(message, "parts", [])
        if type(part).__name__ == "ToolCallPart" and part.tool_name != "final_result"
    ]
    assert tool_calls == []
