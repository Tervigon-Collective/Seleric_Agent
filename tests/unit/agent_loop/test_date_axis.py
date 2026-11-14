"""The chat's date axis reaches every MCP query through the tool layer and never through the prompt."""
from __future__ import annotations

import dataclasses
from typing import Any

import pytest
from pydantic_ai.messages import TextPart, ToolCallPart

from seleric_swarm.agent import prompt as prompt_mod
from seleric_swarm.agent.loop import run_turn
from seleric_swarm.agent.mcp_toolset import tool_defs
from seleric_swarm.services.mcp_query import scope_date_axis, with_date_axis
from seleric_swarm.state.artifacts import InMemoryArtifactStore
from tests.unit.agent_loop.test_loop import FakeGateway, _deps, _scripted

DEFS = tool_defs([
    {"name": "metrics_query", "description": "Query metrics.", "inputSchema": {
        "type": "object", "required": ["measures"],
        "properties": {"measures": {"type": "array", "items": {"type": "string"}}, "date_axis": {"type": "string"}}}},
    {"name": "catalogue_search_metrics", "description": "Search.", "inputSchema": {
        "type": "object", "properties": {"query": {"type": "string"}}}},
])


async def _run(axis: str | None, call: ToolCallPart, tool: str = "metrics_query") -> list[dict[str, Any]]:
    gw = FakeGateway()
    deps = dataclasses.replace(_deps(InMemoryArtifactStore()), date_axis=axis)
    await run_turn(deps=deps, model=_scripted([call], [TextPart("Net sales were ₹42,177.")]), gateway=gw, tool_defs=DEFS,
                   system_prompt="sys", question="net sales?", history=[])
    return [c["arguments"] for c in gw.calls if c["capability"] == f"seleric.{tool}"]


@pytest.mark.asyncio
async def test_the_chat_axis_is_added_to_a_metrics_query():
    args = await _run("event_date", ToolCallPart("metrics_query", {"measures": ["net_sales"]}, tool_call_id="c1"))
    assert args == [{"measures": ["net_sales"], "date_axis": "event_date"}]


@pytest.mark.asyncio
async def test_an_axis_the_model_passes_for_the_users_wording_wins():
    call = ToolCallPart("metrics_query", {"measures": ["refund_count"], "date_axis": "event_date"}, tool_call_id="c1")
    assert await _run("order_date", call) == [{"measures": ["refund_count"], "date_axis": "event_date"}]


@pytest.mark.asyncio
async def test_no_chat_axis_leaves_the_call_to_the_servers_default():
    args = await _run(None, ToolCallPart("metrics_query", {"measures": ["net_sales"]}, tool_call_id="c1"))
    assert args == [{"measures": ["net_sales"]}]


@pytest.mark.asyncio
async def test_a_tool_without_a_date_axis_argument_is_not_given_one():
    args = await _run("event_date", ToolCallPart("catalogue_search_metrics", {"query": "sales"}, tool_call_id="c1"),
                      tool="catalogue_search_metrics")
    assert args == [{"query": "sales"}]


def test_with_date_axis_keeps_an_explicit_axis_and_ignores_none():
    assert with_date_axis({"measures": ["a"]}, "event_date") == {"measures": ["a"], "date_axis": "event_date"}
    assert with_date_axis({"date_axis": "order_date"}, "event_date") == {"date_axis": "order_date"}
    assert with_date_axis({"measures": ["a"]}, None) == {"measures": ["a"]}


def test_scope_date_axis_accepts_the_two_axes_and_rejects_the_rest():
    assert scope_date_axis({}) is None and scope_date_axis({"date_axis": "string"}) is None
    assert scope_date_axis({"date_axis": "event_date"}) == "event_date"
    assert scope_date_axis({"dateAxis": "order_date"}) == "order_date"
    with pytest.raises(ValueError):
        scope_date_axis({"date_axis": "cohort"})


def test_the_prompt_carries_behaviour_not_the_selected_axis():
    from datetime import UTC, datetime

    from seleric_swarm.services.catalogue_bootstrap import CatalogueSnapshot

    text = prompt_mod.system_prompt(CatalogueSnapshot(), datetime(2026, 10, 23, tzinfo=UTC))
    assert "date_axis" in text and "pnl_" in text  # the rule is there
    assert "chat is set to" not in text and "active axis" not in text.lower()


# ---- measures that are not switchable never get the chat's axis (B-01) and the undated note is not misleading (B-03)
def _snapshot():
    from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta, CatalogueSnapshot

    return CatalogueSnapshot(metrics=(
        CatalogueMetricMeta(id="orders", raw={"description": "Sale orders placed. Grain: order. Date axis: placement only. date_axis=order_date (default)"}),
        CatalogueMetricMeta(id="net_sales", raw={"description": "Net sales ex-GST. Date axis: switchable. date_axis=order_date (default)"}),
        CatalogueMetricMeta(id="ad_spend", raw={"description": "Ad spend (INR) by delivery date. Date axis: native. Ignores date_axis (its own clock, D-36)."}),
        CatalogueMetricMeta(id="repeat_rate", raw={"description": "Repeat customers / customers. Date axis: cohort only."}),
    ))


def test_a_placement_only_or_native_or_cohort_metric_is_not_given_the_axis():
    snap = _snapshot()
    for metric in ("orders", "ad_spend", "repeat_rate"):
        assert with_date_axis({"measures": [metric]}, "event_date", snap) == {"measures": [metric]}


def test_a_switchable_or_unknown_metric_still_gets_the_axis():
    snap = _snapshot()
    assert with_date_axis({"measures": ["net_sales"]}, "event_date", snap)["date_axis"] == "event_date"
    assert with_date_axis({"measures": ["not_in_catalogue"]}, "event_date", snap)["date_axis"] == "event_date"
    assert with_date_axis({"measures": ["orders"]}, "event_date")["date_axis"] == "event_date"  # no catalogue: fail open


def test_a_call_mixing_switchable_and_placement_only_measures_keeps_the_axis():
    # known limit: Core must ignore the axis per measure; splitting the call is not done here
    assert with_date_axis({"measures": ["net_sales", "orders"]}, "event_date", _snapshot())["date_axis"] == "event_date"


@pytest.mark.asyncio
async def test_orders_on_an_event_date_chat_are_queried_without_the_axis():
    gw = FakeGateway()
    deps = dataclasses.replace(_deps(InMemoryArtifactStore()), date_axis="event_date", catalogue=_snapshot())
    await run_turn(deps=deps, model=_scripted([ToolCallPart("metrics_query", {"measures": ["orders"]}, tool_call_id="c1")], [TextPart("26 orders.")]),
                   gateway=gw, tool_defs=DEFS, system_prompt="sys", question="orders on 24 Oct?", history=[])
    assert [c["arguments"] for c in gw.calls if c["capability"] == "seleric.metrics_query"] == [{"measures": ["orders"]}]


def test_the_undated_note_says_it_is_all_history_and_not_part_of_the_window():
    from seleric_swarm.services.mcp_query import UNDATED_NOTE, relabel_undated

    result = {"rows": [], "undated": {"label": "Undated", "note": "dated + Undated reconcile to the order_date total.", "rows": []}}
    assert relabel_undated(result)["undated"]["note"] == UNDATED_NOTE
    assert "ALL history" in UNDATED_NOTE and "NOT part of the dated window" in UNDATED_NOTE
    assert relabel_undated({"rows": []}) == {"rows": []} and relabel_undated("x") == "x"
