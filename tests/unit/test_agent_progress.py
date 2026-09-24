"""Live progress: tool steps become activity events while the run is still going."""

from __future__ import annotations

import pytest
from pydantic_ai.messages import (
    FinalResultEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ToolCallPart,
    ToolReturnPart,
)

from seleric_swarm.agent import progress


async def _events(*items):
    for item in items:
        yield item


@pytest.fixture(autouse=True)
def _clean_sinks():
    yield
    progress._SINKS.clear()


@pytest.mark.asyncio
async def test_tool_calls_and_results_are_reported_with_human_labels():
    seen: list[tuple[str, str]] = []
    progress.set_progress_sink("m1", lambda et, summary, payload: seen.append((et, summary)))

    call = FunctionToolCallEvent(ToolCallPart("search_semantics", {"query": "cac"}, "c1"))
    result = FunctionToolResultEvent(ToolReturnPart("search_semantics", "ok", "c1"))
    final = FinalResultEvent(tool_name="final_result", tool_call_id="c2")
    await progress.progress_handler("m1")(None, _events(call, result, final))  # type: ignore[arg-type]

    assert seen == [
        ("agent.tool_started", "Searching the metric catalogue"),
        ("agent.tool_completed", "Searching the metric catalogue — done"),
        ("agent.answering", "Writing the answer"),
    ]


def test_emit_without_a_sink_is_a_noop():
    progress.emit_progress("nobody", "agent.tool_started", "x", {})


def test_a_failing_sink_never_fails_the_mission():
    def boom(*_args):
        raise RuntimeError("transport down")

    progress.set_progress_sink("m2", boom)
    progress.emit_progress("m2", "agent.tool_started", "x", {})


def test_unknown_tools_get_a_readable_label():
    assert progress.tool_label("some_new_tool") == "Some new tool"
