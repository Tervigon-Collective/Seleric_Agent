"""Analysis context across turns (semantic graph plan Phase 4): every tool call leaves a record, the next turn gets the
latest turn's records as data, its result ids resolve to the stored rows, and a diagnosis with no window reads the
conversation's period."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Any

import pytest
from pydantic_ai.messages import ModelMessage, TextPart, ToolCallPart

from seleric_swarm.agent import analysis_context
from seleric_swarm.agent.local_tools import _payload_for, _rehydrate
from seleric_swarm.agent.mcp_toolset import TurnState, compact_value
from seleric_swarm.agent.runner import _persist_turn, _thread_memory
from seleric_swarm.state.artifacts import InMemoryArtifactStore
from tests.unit.agent_loop.test_loop import PAYLOAD, _deps, _scripted, _turn


def test_a_query_call_records_its_metrics_window_dimensions_and_notes():
    args = {"measures": ["net_sales"], "time_range": {"start": "2026-09-01", "end": "2026-09-30"},
            "dimensions": ["finance_channel"], "compare_period": "previous_period"}
    payload = {"query_id": "q_9", "rows": [{}, {}], "warnings": ["No brand filter given — scoped to 20.", "Trimmed to 30 days."]}
    e = analysis_context.entry("metrics_query", args, payload, ok=True)
    assert e == {"tool": "metrics_query", "ok": True, "query_id": "q_9", "metrics": ["net_sales"],
                 "window": {"start": "2026-09-01", "end": "2026-09-30"}, "compare": "previous_period",
                 "dimensions": ["finance_channel"], "row_count": 2, "notes": ["Trimmed to 30 days."]}


def test_a_local_tool_call_with_datetimes_records_its_window_and_comparison():
    kwargs = {"metric_id": "net_sales", "event_start": datetime(2026, 9, 1), "event_end": datetime(2026, 9, 30),
              "compare_start": datetime(2026, 8, 1), "compare_end": datetime(2026, 8, 31), "drivers": ["orders"], "filters": None}
    e = analysis_context.entry("diagnose_metric_change", kwargs, None, ok=True)
    assert e["window"] == {"start": "2026-09-01", "end": "2026-09-30"}
    assert e["compare"] == {"start": "2026-08-01", "end": "2026-08-31"} and e["drivers"] == ["orders"]
    assert "filters" not in e
    assert analysis_context.last_window([e, {"tool": "x", "ok": True, "window": {"preset": "today"}}]) == (date(2026, 9, 1), date(2026, 9, 30))
    assert analysis_context.last_window([{**e, "ok": False}]) is None


@pytest.mark.asyncio
async def test_the_previous_turns_records_reach_the_next_turn_as_data():
    call = [ToolCallPart("metrics_query", {"measures": ["net_sales"]}, tool_call_id="c1")]
    first, _, store = await _turn(_scripted(call, [TextPart("Net sales were ₹42,177 yesterday, up from ₹34,377.")]))
    assert [e["tool"] for e in first.state.context] == ["metrics_query"] and first.state.context[0]["query_id"] == "q_1"
    _persist_turn(store, loop_result=first, query="net sales?", workspace_id="w", thread_id="t", mission_id="m1",
                  as_of_dt=datetime(2026, 10, 13, tzinfo=UTC))
    _, _, _, context = _thread_memory(store, "w", "t")
    assert context == first.state.context

    seen: list[str] = []

    def answer(messages: list[ModelMessage]) -> list[Any]:
        seen.extend(str(getattr(m, "instructions", "") or "") for m in messages)
        return [TextPart("Done.")]

    await _turn(_scripted(answer), store=store, prior_context=context)
    assert any("What the previous turn analysed" in s and '"query_id":"q_1"' in s for s in seen)


@pytest.mark.asyncio
async def test_an_earlier_turns_result_id_resolves_to_its_stored_rows():
    call = [ToolCallPart("metrics_query", {"measures": ["net_sales"]}, tool_call_id="c1")]
    first, _, store = await _turn(_scripted(call, [TextPart("Net sales were ₹42,177 yesterday, up from ₹34,377.")]))
    _persist_turn(store, loop_result=first, query="net sales?", workspace_id="w", thread_id="t", mission_id="m1",
                  as_of_dt=datetime(2026, 10, 13, tzinfo=UTC))
    later = replace(_deps(store), mission_id="m2")
    state = TurnState()
    _rehydrate(later, state, ["q_1", "q_unknown"])
    assert sorted(state.by_query["q_1"]) == sorted(first.state.by_query["q_1"]) and "q_unknown" not in state.by_query


def test_a_kept_result_is_reused_only_when_its_rows_were_not_cut():
    whole = compact_value(PAYLOAD)
    cut = compact_value({**PAYLOAD, "query_id": "q_2", "rows": [PAYLOAD["rows"][0]] * 80})
    state = TurnState(prior_payloads=[whole, cut])
    assert _payload_for(state, "q_1") is whole
    assert _payload_for(state, "q_2") is None
