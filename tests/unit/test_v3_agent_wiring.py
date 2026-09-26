"""Sprint 4 Profile A: the agent actually has every implemented toolset.

This is a wiring test, not an integration test — it proves all 26 real tool
functions register on ``SelericAgent`` without a schema error (the bug this
test guards: ``RunContext[SelericDeps]`` annotations that only resolved
under ``TYPE_CHECKING`` blew up at real tool-registration time with
``NameError: name 'SelericDeps' is not defined``, since nothing exercised
that path before this wiring existed). It does not call any tool for real
(no live MCP, no LLM) — that's ``tests/replay/``'s job.
"""

from __future__ import annotations

from seleric_swarm.agent.agent import TOOLS, build_seleric_agent


def test_every_frozen_function_is_registered() -> None:
    """26 = the 23 CONTRACTS.md §4 functions, ``sandbox.run_python`` (the
    python sandbox added on top of the frozen analytics surface),
    ``semantic.get_metric_definitions`` (batch of the frozen singular one) and
    ``semantic.resolve_brand`` (brand→brand_id resolution). The read-only ad
    surfaces in ``ads.py`` are deliberately unregistered (third-party APIs,
    not certified Cube views).

    Counting here is what stops a toolset being written and then silently left
    unregistered — the test suite would stay green, because a tool nobody
    registers is a tool nobody tests.
    """
    assert len(TOOLS) == 26


def test_all_tools_register_on_the_agent() -> None:
    agent = build_seleric_agent()
    names = set(agent._function_toolset.tools.keys())
    assert names == {
        "search_semantics",
        "resolve_brand",
        "get_metric_definition",
        "get_metric_definitions",
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
        # Sprint 4 Profile C — the four greenfield analytics functions...
        "contribution_analysis",
        "segment_decomposition",
        "funnel_decomposition",
        "cohort_analysis",
        # Python sandbox — arbitrary aggregation over already-fetched evidence.
        "run_python",
        # ...and the two toolsets that had no module at all before Sprint 4.
        "search_knowledge",
        "get_experiment_history",
        "estimate_sample_size",
        "evaluate_experiment",
    }


async def test_stub_model_still_calls_zero_tools() -> None:
    # call_tools=[] on the stub model must still hold even with 26 real
    # tools registered -- the 0%-traffic path stays side-effect-free.
    # "final_result" is pydantic_ai's own internal structured-output call,
    # not one of the real tools -- excluded, not counted as a tool call.
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
