"""KPI cards / table / chart chosen from the shape of the main result (``agent/present.py``), with no model call."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from seleric_swarm.agent import present
from seleric_swarm.agent.dependencies import ExecutionLimits, NullMcpClient, SelericDeps
from seleric_swarm.agent.loop import run_turn
from seleric_swarm.agent.mcp_toolset import tool_defs
from seleric_swarm.conversations.contracts import ContextBundle, Principal, PrincipalAuthMethod
from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta, CatalogueSnapshot
from seleric_swarm.state.artifacts import InMemoryArtifactStore

CATALOGUE = CatalogueSnapshot(metrics=(
    CatalogueMetricMeta(id="net_sales", label="Net sales", raw={"unit": "INR", "aggregation": "additive"}),
    CatalogueMetricMeta(id="orders", label="Orders", raw={"unit": "count", "aggregation": "additive"}),
    CatalogueMetricMeta(id="aov", label="AOV", raw={"unit": "INR", "aggregation": "ratio"}),
    CatalogueMetricMeta(id="ad_spend", label="Ad spend", raw={"unit": "INR", "aggregation": "additive"}),
    CatalogueMetricMeta(id="ctr", label="CTR", raw={"unit": "percent", "aggregation": "ratio"}),
))
DEFS = tool_defs([{"name": "metrics_query", "description": "Query metrics.", "inputSchema": {
    "type": "object", "properties": {"measures": {"type": "array", "items": {"type": "string"}}}, "required": ["measures"]}}])
CALL = [ToolCallPart("metrics_query", {"measures": ["net_sales"]}, tool_call_id="c1")]


class Gateway(NullMcpClient):
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    async def call(self, *, agent_id: str, capability: str, arguments: dict[str, Any]) -> Any:
        return dict(self.payload)


def payload(qid: str, columns: list[str], rows: list[dict[str, Any]], measures: list[str], **extra: Any) -> dict[str, Any]:
    return {"query_id": qid, "columns": columns, "rows": rows, "warnings": [],
            "provenance": {"query_id": qid, "metric_ids": measures, "currency": "INR",
                           "time_range": {"start": "2026-10-09", "end": "2026-10-15"}, **extra.pop("prov", {})}, **extra}


async def run(result_payload: dict[str, Any], answer: str):
    store = InMemoryArtifactStore()
    deps = SelericDeps(
        mission_id="m1", as_of=datetime(2026, 10, 16, tzinfo=UTC),
        principal=Principal(principal_id="p", workspace_id="w", user_id="u", authenticated=False, auth_method=PrincipalAuthMethod.ANONYMOUS),
        thread_id="t", run_id="r", trace_id="x", context=ContextBundle(), mcp_client=NullMcpClient(),
        artifact_store=store, limits=ExecutionLimits(), catalogue=CATALOGUE)
    queue = [CALL, [TextPart(answer)]]

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=queue.pop(0) if len(queue) > 1 else queue[0])

    result = await run_turn(deps=deps, model=FunctionModel(fn), gateway=Gateway(result_payload), tool_defs=DEFS,
                            system_prompt="sys", question="how did we do?", history=[])
    charts = [a.payload for a in store.list_for_mission("m1") if a.artifact_type == "chart_spec"]
    return result, charts


def kinds(result: Any) -> list[str]:
    return [p["type"] for p in result.state.parts]


@pytest.mark.asyncio
async def test_a_snapshot_of_several_measures_is_kpi_cards_with_units():
    snap = payload("q1", ["net_sales", "orders", "aov"],
                   [{"net_sales": "258133.4", "orders": "136", "aov": "1898.04"}], ["net_sales", "orders", "aov"])
    result, charts = await run(snap, "Net sales were ₹2,58,133 from 136 orders, AOV ₹1,898.")
    assert kinds(result) == ["KPI_GRID"] and not charts
    cards = result.state.parts[0]["content"]["cards"]
    assert [(c["label"], c["display"]) for c in cards] == [("Net sales", "₹2,58,133"), ("Orders", "136"), ("AOV", "₹1,898")]
    assert cards[0]["time_basis"] == "9 Oct – 15 Oct"


@pytest.mark.asyncio
async def test_two_periods_are_cards_with_the_change_not_a_two_bar_chart():
    rows = [{"order_date.day": "2026-10-12T00:00:00.000", "net_sales": "258133", "orders": "136"},
            {"order_date.day": "2026-10-08T00:00:00.000", "net_sales": "290000", "orders": "151"}]
    result, charts = await run(payload("q1", ["order_date.day", "net_sales", "orders"], rows, ["net_sales", "orders"]),
                               "Net sales fell to ₹2,58,133 on 12 Oct from ₹2,90,000 on 8 Oct; orders 136 from 151.")
    assert kinds(result) == ["KPI_GRID"] and not charts
    card = result.state.parts[0]["content"]["cards"][0]
    assert card["time_basis"] == "12 Oct vs 8 Oct" and card["comparison"]["value"] == 290000.0
    assert card["comparison"]["delta_pct"] == -11.0


@pytest.mark.asyncio
async def test_compare_rows_give_the_previous_period():
    snap = payload("q1", ["net_sales", "orders"], [{"net_sales": "200", "orders": "10"}], ["net_sales", "orders"],
                   compare_rows=[{"net_sales": "100", "orders": "20"}],
                   prov={"compare_period": {"start": "2026-10-02", "end": "2026-10-08"}})
    result, _ = await run(snap, "Net sales were 200 against 100, orders 10 against 20.")
    cards = {c["label"]: c for c in result.state.parts[0]["content"]["cards"]}
    assert cards["Net sales"]["comparison"]["delta_pct"] == 100.0 and cards["Orders"]["comparison"]["delta"] == -10.0


@pytest.mark.asyncio
async def test_a_single_figure_is_a_sentence():
    result, charts = await run(payload("q1", ["net_sales"], [{"net_sales": "258133"}], ["net_sales"]),
                               "Net sales were ₹2,58,133.")
    assert kinds(result) == [] and not charts


DAYS = [f"2026-10-{d:02d}T00:00:00.000" for d in range(9, 16)]


@pytest.mark.asyncio
async def test_a_trend_is_a_chart_and_only_a_wide_one_also_a_table():
    one = payload("q1", ["order_date.day", "net_sales"],
                  [{"order_date.day": d, "net_sales": str(100000 + i * 5000)} for i, d in enumerate(DAYS)], ["net_sales"])
    result, charts = await run(one, "Net sales rose from ₹1,00,000 to ₹1,30,000.")
    assert kinds(result) == [] and [c["chart_type"] for c in charts] == ["line"]
    three = payload("q2", ["order_date.day", "net_sales", "orders", "ad_spend"],
                    [{"order_date.day": d, "net_sales": str(100000 + i * 5000), "orders": str(50 + i), "ad_spend": str(20000 + i)}
                     for i, d in enumerate(DAYS)], ["net_sales", "orders", "ad_spend"])
    result, charts = await run(three, "Net sales rose from ₹1,00,000 to ₹1,30,000 while orders went from 50 to 56.")
    assert kinds(result) == ["TABLE"] and len(charts) == 1
    assert next(p["title"] for p in charts[0]["panels"]) == "Net sales, Ad spend"


def campaigns(n: int, measures: list[str]) -> list[dict[str, Any]]:
    return [{"campaign_name": f"Campaign number {i:02d} Seasonal", **{m: str(10000 - i * 300) for m in measures}} for i in range(n)]


@pytest.mark.asyncio
async def test_a_long_ranking_is_horizontal_bars_and_a_table():
    rows = campaigns(12, ["ad_spend"])
    result, charts = await run(payload("q1", ["campaign_name", "ad_spend"], rows, ["ad_spend"]),
                               "Campaign number 00 Seasonal spent ₹10,000, the most.")
    assert kinds(result) == ["TABLE"] and [c["chart_type"] for c in charts] == ["horizontal_bar"]
    table = result.state.parts[0]["content"]
    assert table["columns"] == ["Campaign Name", "Ad spend"] and table["rows"][0]["Ad spend"] == "₹10,000"


@pytest.mark.asyncio
async def test_a_short_ranking_of_one_measure_is_only_a_chart_and_many_measures_only_a_table():
    short = payload("q1", ["campaign_name", "ad_spend"], campaigns(5, ["ad_spend"]), ["ad_spend"])
    result, charts = await run(short, "Campaign number 00 Seasonal spent ₹10,000, the most.")
    assert kinds(result) == [] and len(charts) == 1
    wide = payload("q2", ["campaign_name", "ad_spend", "net_sales", "orders"], campaigns(5, ["ad_spend", "net_sales", "orders"]),
                   ["ad_spend", "net_sales", "orders"])
    result, charts = await run(wide, "Campaign number 00 Seasonal spent ₹10,000, the most.")
    assert kinds(result) == ["TABLE"] and not charts


@pytest.mark.asyncio
async def test_views_are_joined_and_a_missing_cell_is_a_dash_not_zero():
    paid = payload("q_paid", ["campaign_name", "ad_spend"], [
        {"campaign_name": "Alpha", "ad_spend": "10268"}, {"campaign_name": "Beta", "ad_spend": "9707"},
        {"campaign_name": "Gamma", "ad_spend": "8100"}], ["ad_spend"])
    shop = payload("q_shop", ["campaign_name", "net_sales", "orders"], [
        {"campaign_name": "Alpha", "net_sales": "5884", "orders": "33"}, {"campaign_name": None, "net_sales": "900", "orders": "81"},
        {"campaign_name": "Gamma", "net_sales": "4100", "orders": "20"}], ["net_sales", "orders"])
    composed = {"query_id": "q_parent", "composed": True, "parts": [paid, shop]}
    result, charts = await run(composed, "Alpha spent ₹10,268 and sold ₹5,884; Beta spent ₹9,707.")
    assert kinds(result) == ["TABLE"] and not charts
    table = result.state.parts[0]["content"]
    assert table["columns"] == ["Campaign Name", "Net sales", "Ad spend", "Orders"]  # the query listed net sales first
    by_name = {r["Campaign Name"]: r for r in table["rows"]}
    assert by_name["Beta"]["Net sales"] == present.BLANK and by_name["Alpha"]["Orders"] == "33"
    assert present.UNSET_ENTITY not in by_name  # the list is the ranked view's rows; the other view's extra row is not added
    assert "1 more row in the other view not in this list" in table["caption"] and "no data for that row" in table["caption"]


@pytest.mark.asyncio
async def test_an_answer_that_quotes_nothing_attaches_nothing():
    snap = payload("q1", ["net_sales", "orders"], [{"net_sales": "258133.4", "orders": "136"}], ["net_sales", "orders"])
    result, charts = await run(snap, "I could not tell from this data.")
    assert kinds(result) == [] and not charts


def test_display_values_follow_the_answer_rules():
    d = present.display_value
    assert d(42177.33, unit="INR", aggregation="additive") == "₹42,177"
    assert d(-172226.96, unit="INR", aggregation="additive") == "-₹1,72,227"
    assert d(12.3456, unit="percent", aggregation="ratio") == "12.3%"
    assert d(1.2741, unit=None, aggregation="ratio") == "1.27"
    assert d(1234567, unit="count", aggregation="additive") == "12,34,567"
    assert d(None, unit="INR", aggregation=None) == present.BLANK


def test_an_answer_table_the_attached_table_already_holds_is_dropped_but_one_with_new_figures_stays():
    from seleric_swarm.agent.answer_text import numeric_tokens, strip_markdown_tables

    cells = numeric_tokens("₹10,000 ₹9,700 33 2026-10-12")
    same = "Lead.\n\n| Campaign | Spend |\n| --- | ---: |\n| A | ₹10,000 |\n| B | ₹9,700 |\n\nTail."
    derived = "Lead.\n\n| Campaign | Spend | Change |\n| --- | ---: | ---: |\n| A | ₹10,000 | -11.4% |\n\nTail."

    def covered(lines: list[str]) -> bool:
        return numeric_tokens(" ".join(lines)) <= cells

    assert "|" not in strip_markdown_tables(same, covered=covered)
    assert "-11.4%" in strip_markdown_tables(derived, covered=covered)


def test_long_figures_use_indian_grouping_and_codes_and_decimals_are_left_alone():
    from seleric_swarm.agent.answer_text import indian_digit_grouping as g

    assert g("net sales ₹982,074 and ₹1,193,322; 12,345 orders") == "net sales ₹9,82,074 and ₹11,93,322; 12,345 orders"
    assert g("₹740746 and 1,234,567.89 and SKU123456 and 2026-10-12") == "₹7,40,746 and 12,34,567.89 and SKU123456 and 2026-10-12"
    assert g("₹9,82,074 stays") == "₹9,82,074 stays"
    assert g("pincode 560001, order 1234567, campaign 123456789") == "pincode 560001, order 1234567, campaign 123456789"


@pytest.mark.asyncio
async def test_two_one_row_queries_for_two_dates_become_one_pair_of_cards():
    day8 = payload("q8", ["net_sales", "orders"], [{"net_sales": "113399", "orders": "63"}], ["net_sales", "orders"],
                   prov={"time_range": {"start": "2026-10-08", "end": "2026-10-08"}})
    day12 = payload("q12", ["net_sales", "orders"], [{"net_sales": "40119", "orders": "26"}], ["net_sales", "orders"],
                    prov={"time_range": {"start": "2026-10-12", "end": "2026-10-12"}})

    class TwoCalls(Gateway):
        def __init__(self) -> None:
            self.queue = [day8, day12]

        async def call(self, *, agent_id: str, capability: str, arguments: dict[str, Any]) -> Any:
            return dict(self.queue.pop(0))

    store = InMemoryArtifactStore()
    deps = SelericDeps(
        mission_id="m1", as_of=datetime(2026, 10, 16, tzinfo=UTC),
        principal=Principal(principal_id="p", workspace_id="w", user_id="u", authenticated=False, auth_method=PrincipalAuthMethod.ANONYMOUS),
        thread_id="t", run_id="r", trace_id="x", context=ContextBundle(), mcp_client=NullMcpClient(),
        artifact_store=store, limits=ExecutionLimits(), catalogue=CATALOGUE)
    calls = [ToolCallPart("metrics_query", {"measures": ["net_sales", "orders"], "n": 8}, tool_call_id="a"),
             ToolCallPart("metrics_query", {"measures": ["net_sales", "orders"], "n": 12}, tool_call_id="b")]
    queue = [calls, [TextPart("Net sales fell from ₹1,13,399 on 8 Oct to ₹40,119 on 12 Oct; orders 63 to 26.")]]

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=queue.pop(0) if len(queue) > 1 else queue[0])

    defs = tool_defs([{"name": "metrics_query", "description": "Query metrics.", "inputSchema": {
        "type": "object", "properties": {"measures": {"type": "array", "items": {"type": "string"}}, "n": {"type": "integer"}},
        "required": ["measures"]}}])
    result = await run_turn(deps=deps, model=FunctionModel(fn), gateway=TwoCalls(), tool_defs=defs, system_prompt="s",
                            question="8 Oct vs 12 Oct", history=[])
    (part,) = result.state.parts
    card = part["content"]["cards"][0]
    assert card["display"] == "₹40,119" and card["comparison"]["value"] == 113399.0 and card["time_basis"] == "12 Oct vs 8 Oct"


@pytest.mark.asyncio
async def test_two_queries_for_the_same_days_are_one_chart_not_the_one_quoted_most():
    sales = payload("qs", ["order_date.day", "net_sales"],
                    [{"order_date.day": d, "net_sales": str(100000 + i * 5000)} for i, d in enumerate(DAYS)], ["net_sales"])
    spend = payload("qa", ["order_date.day", "ad_spend"],
                    [{"order_date.day": d, "ad_spend": str(40000 + i * 900)} for i, d in enumerate(DAYS)], ["ad_spend"])

    class Two(Gateway):
        def __init__(self) -> None:
            self.queue = [sales, spend]

        async def call(self, *, agent_id: str, capability: str, arguments: dict[str, Any]) -> Any:
            return dict(self.queue.pop(0))

    store = InMemoryArtifactStore()
    deps = SelericDeps(
        mission_id="m1", as_of=datetime(2026, 10, 16, tzinfo=UTC),
        principal=Principal(principal_id="p", workspace_id="w", user_id="u", authenticated=False, auth_method=PrincipalAuthMethod.ANONYMOUS),
        thread_id="t", run_id="r", trace_id="x", context=ContextBundle(), mcp_client=NullMcpClient(),
        artifact_store=store, limits=ExecutionLimits(), catalogue=CATALOGUE)
    calls = [ToolCallPart("metrics_query", {"measures": ["net_sales"]}, tool_call_id="a"),
             ToolCallPart("metrics_query", {"measures": ["ad_spend"]}, tool_call_id="b")]
    queue = [calls, [TextPart("Net sales ran ₹1,00,000 to ₹1,30,000 while ad spend ran ₹40,000 to ₹45,400.")]]

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=queue.pop(0) if len(queue) > 1 else queue[0])

    await run_turn(deps=deps, model=FunctionModel(fn), gateway=Two(), tool_defs=DEFS, system_prompt="s",
                   question="daily net sales and ad spend", history=[])
    (chart,) = [a.payload for a in store.list_for_mission("m1") if a.artifact_type == "chart_spec"]
    assert sorted(chart["metrics"]) == ["ad_spend", "net_sales"] and chart["chart_type"] == "line"


@pytest.mark.asyncio
async def test_an_answer_that_states_shares_gets_a_donut_or_treemap():
    rows = [{"channel": c, "net_sales": str(v)} for c, v in [("a", 500), ("b", 300), ("c", 200)]]
    _result, charts = await run(payload("q1", ["channel", "net_sales"], rows, ["net_sales"]),
                                "Of ₹1,000 net sales, channel a brought ₹500 (50.0%), b ₹300 (30.0%) and c ₹200 (20.0%).")
    assert [c["chart_type"] for c in charts] == ["donut"]


def test_share_form_depends_on_the_slice_count_and_on_the_answer_stating_shares():
    deps = SimpleNamespaceDeps(CATALOGUE)
    values = [100 - i * 5 for i in range(8)]
    total = sum(values)
    src = present.Source(query_id="q", measures=["net_sales"], time_col=None, dims=["channel"], compare_rows=[],
                         rows=[{"channel": f"c{i}", "net_sales": str(v)} for i, v in enumerate(values)],
                         currency="INR", window=None, compare_window=None)
    frame = present.frame_of([src])
    assert frame is not None
    stated = f"c0 was {100 * 100 / total:.1f}% and c1 {100 * 95 / total:.1f}% of net sales."
    assert present.share_chart_type(deps, frame, stated) == "treemap"  # type: ignore[arg-type]
    assert present.share_chart_type(deps, frame, "c0 spent the most, ahead of c1.") is None  # type: ignore[arg-type]
    frame.rows = frame.rows[:4]
    stated4 = f"c0 was {100 * 100 / sum(values[:4]):.1f}% and c1 {100 * 95 / sum(values[:4]):.1f}%."
    assert present.share_chart_type(deps, frame, stated4) == "donut"  # type: ignore[arg-type]


def test_a_count_over_count_ratio_is_a_percentage_and_an_amount_ratio_a_multiple():
    from seleric_swarm.agent.dependencies import NullMcpClient as _N  # noqa: F401

    cat = CatalogueSnapshot(metrics=(
        CatalogueMetricMeta(id="clicks", label="Clicks", raw={"unit": "count", "aggregation": "additive"}),
        CatalogueMetricMeta(id="impressions", label="Impressions", raw={"unit": "count", "aggregation": "additive"}),
        CatalogueMetricMeta(id="ctr", label="CTR", raw={"unit": "ratio", "aggregation": "ratio",
                            "ratio_components": {"numerator": "clicks", "denominator": "impressions", "multiplier": 1.0}}),
        CatalogueMetricMeta(id="net_roas", label="Net ROAS", raw={"unit": "ratio", "aggregation": "ratio",
                            "ratio_components": {"numerator": "net_sales", "denominator": "ad_spend", "multiplier": 1.0}}),
        CatalogueMetricMeta(id="net_sales", label="Net sales", raw={"unit": "INR"}),
        CatalogueMetricMeta(id="ad_spend", label="Ad spend", raw={"unit": "INR"}),
    ))
    deps = SimpleNamespaceDeps(cat)
    meta = present.Meta.of(deps, ["ctr", "net_roas"])  # type: ignore[arg-type]
    assert meta.show("ctr", 0.01356) == "1.4%" and meta.show("net_roas", 1.2741) == "1.27"


class SimpleNamespaceDeps:
    def __init__(self, catalogue: CatalogueSnapshot) -> None:
        self.catalogue = catalogue


@pytest.mark.asyncio
async def test_views_on_different_date_columns_line_up_by_day_and_the_query_decides_rank_and_column_order():
    sales = payload("qs", ["order_date.day", "net_sales"],
                    [{"order_date.day": d, "net_sales": str(100000 + i * 5000)} for i, d in enumerate(DAYS)], ["net_sales"])
    spend = payload("qa", ["report_date.day", "ad_spend"],
                    [{"report_date.day": d, "ad_spend": str(40000 + i * 900)} for i, d in enumerate(DAYS)], ["ad_spend"])
    composed = {"query_id": "qp", "composed": True, "parts": [sales, spend]}
    result, charts = await run(composed, "Net sales ran ₹1,00,000 to ₹1,30,000 while ad spend ran ₹40,000 to ₹45,400.")
    assert [c["chart_type"] for c in charts] == ["line"] and sorted(charts[0]["metrics"]) == ["ad_spend", "net_sales"]
    assert kinds(result) == []  # two measures over time: the chart carries it


def test_the_query_s_measure_order_and_sort_lead_the_table():
    a = present.Source(query_id="a", measures=["net_sales", "orders"], time_col=None, dims=["campaign_name"], compare_rows=[],
                       rows=[{"campaign_name": None, "net_sales": "900", "orders": "81"},
                             {"campaign_name": "X", "net_sales": "500", "orders": "33"}],
                       currency="INR", window=None, compare_window=None)
    b = present.Source(query_id="b", measures=["ad_spend"], time_col=None, dims=["campaign_name"], compare_rows=[],
                       rows=[{"campaign_name": "X", "ad_spend": "100"}, {"campaign_name": "Y", "ad_spend": "300"}],
                       currency="INR", window=None, compare_window=None)
    args = {"measures": ["ad_spend", "orders", "net_sales"], "sort": [{"field": "ad_spend", "direction": "desc"}]}
    frame = present.frame_of([a, b], present.sort_measure(args))  # the view holding the ranking measure leads
    assert frame is not None
    present.apply_call_hints(frame, args)
    deps = SimpleNamespaceDeps(CATALOGUE)
    table = present.table_content(deps, frame, query_id="q")  # type: ignore[arg-type]
    assert table["columns"] == ["Campaign Name", "Ad spend", "Orders", "Net sales"]
    assert [r["Campaign Name"] for r in table["rows"]] == ["Y", "X"]  # the ranked view's rows; the unnamed row is not in it
    assert table["sort"] == {"column": "Ad spend", "direction": "desc"}


@pytest.mark.asyncio
async def test_the_models_own_show_table_call_also_follows_the_querys_sort_and_measure_order():
    from seleric_swarm.agent.local_tools import build_local_tools
    from seleric_swarm.agent.mcp_toolset import TurnState

    paid = payload("q_paid", ["campaign_name", "ad_spend"], [{"campaign_name": "X", "ad_spend": "100"}, {"campaign_name": "Y", "ad_spend": "300"}], ["ad_spend"])
    shop = payload("q_shop", ["campaign_name", "net_sales"], [{"campaign_name": None, "net_sales": "900"}, {"campaign_name": "X", "net_sales": "500"}], ["net_sales"])
    state = TurnState()
    state.payloads.append({"query_id": "q_parent", "parts": [paid, shop]})
    state.evidence.append({"tool": "metrics_query", "query_id": "q_parent",
                           "args": {"measures": ["ad_spend", "net_sales"], "sort": [{"field": "ad_spend", "direction": "desc"}]}})
    tool = next(t for t in build_local_tools(state) if t.__name__ == "show_table")
    deps = SimpleNamespaceDeps(CATALOGUE)
    for qid in ("q_parent", "q_paid"):
        state.parts.clear()
        await tool(type("Ctx", (), {"deps": deps})(), qid)
        content = state.parts[0]["content"]
        assert content["columns"][1] == "Ad spend"
    state.parts.clear()
    await tool(type("Ctx", (), {"deps": deps})(), "q_parent")
    assert [r["Campaign Name"] for r in state.parts[0]["content"]["rows"]] == ["Y", "X"]


def test_a_row_by_row_list_the_visuals_already_hold_is_dropped_but_findings_stay():
    from seleric_swarm.agent.answer_text import drop_enumerations, numeric_tokens

    cells = numeric_tokens("2026-10-09 2026-10-10 2026-10-11 2026-10-12 68,449 70,730 34,631 40,119 91,432")
    text = (
        "Sales peaked on 13 Oct and bottomed on 11 Oct.\n\n"
        "- 2026-10-09 — ₹68,449\n- 2026-10-10 — ₹70,730\n- 2026-10-11 — ₹34,631\n- 2026-10-12 — ₹40,119\n\n"
        "Worth checking: the 11 Oct dip."
    )
    out = drop_enumerations(text, cells)
    assert "68,449" not in out and "Sales peaked" in out and "Worth checking" in out
    two = "- 2026-10-09 — ₹68,449\n- 2026-10-10 — ₹70,730"
    assert drop_enumerations(two, cells) == two  # two items are a finding, not a listing
    new_figure = "- a ₹68,449\n- b ₹70,730\n- c ₹34,631\n- d ₹40,119 (up 18%)"
    assert "d ₹40,119" in drop_enumerations(new_figure, cells)  # an item with a figure the visual lacks breaks the run
    numbered = "1. x — ₹68,449\n2. y — ₹70,730\n3. z — ₹34,631\n4. w — ₹40,119"
    assert drop_enumerations(numbered, cells) == ""


@pytest.mark.asyncio
async def test_a_multi_view_overview_is_one_set_of_cards_each_dated_by_its_own_view_with_the_previous_day():
    """Live 2026-10-16 'overview of yesterday': two views, one row each, each with its own previous-day row."""
    commerce = payload("qc", ["net_sales", "orders"], [{"net_sales": "68609.6", "orders": "37"}], ["net_sales", "orders"],
                       compare_rows=[{"net_sales": "78789.2", "orders": "39"}],
                       prov={"time_range": {"start": "2026-10-15", "end": "2026-10-15"},
                             "compare_period": {"start": "2026-10-14", "end": "2026-10-14"}})
    pnl = payload("qp", ["ad_spend", "ctr"], [{"ad_spend": "37916.2", "ctr": "0.0131"}], ["ad_spend", "ctr"],
                  compare_rows=[{"ad_spend": "47392.0", "ctr": "0.0125"}],
                  prov={"time_range": {"start": "2026-10-15", "end": "2026-10-15"},
                        "compare_period": {"start": "2026-10-14", "end": "2026-10-14"}})
    result, charts = await run({"query_id": "qparent", "composed": True, "parts": [commerce, pnl]},
                               "Net sales were ₹68,610 (down from ₹78,789) on 37 orders; ad spend ₹37,916.")
    assert kinds(result) == ["KPI_GRID"] and not charts
    cards = {c["label"]: c for c in result.state.parts[0]["content"]["cards"]}
    assert cards["Net sales"]["comparison"]["delta_display"] == "▼ 12.9%" and cards["Net sales"]["time_basis"] == "15 Oct vs 14 Oct"
    assert cards["Ad spend"]["comparison"]["delta_display"] == "▼ 20.0%"
    assert cards["Orders"]["display"] == "37"


def test_a_change_is_written_the_way_its_unit_should_be_read():
    meta = present.Meta(labels={"s": "s", "r": "r", "c": "c", "a": "a"}, units={"s": "INR", "r": "ratio", "c": "ratio", "a": "INR"},
                        aggregations={"s": "additive", "r": "ratio", "c": "ratio", "a": "ratio"}, fractions=frozenset({"c"}))
    assert present.delta_text(meta, "s", 110.0, 100.0, "INR") == ("▲ 10.0%", "up")      # a total: percent
    assert present.delta_text(meta, "r", 1.18, 0.99, "") == ("▲ 0.19", "up")           # a multiple: absolute
    assert present.delta_text(meta, "c", 0.0131, 0.0125, "") == ("▲ 0.1 pp", "up")      # a share: points
    assert present.delta_text(meta, "a", 1900.0, 2058.0, "INR") == ("▼ ₹158", "down")   # an average amount: absolute
    assert present.delta_text(meta, "s", 5.0, 5.0, "INR") == ("– no change", "flat")
    assert present.delta_text(meta, "s", 6873.0, -292.0, "INR") == ("▲ ₹7,165", "up")  # off a negative base: absolute
    assert present.delta_text(meta, "s", 1000.0, 10.0, "INR") == ("▲ ₹990", "up")      # a near-zero base: absolute


@pytest.mark.asyncio
async def test_when_no_visual_fits_the_data_still_gets_a_table_so_the_answer_is_never_prose_only():
    rows = [{"channel": "a", "net_sales": "500", "orders": "5"}, {"channel": "b", "net_sales": "300", "orders": "3"}]
    result, _ = await run(payload("q1", ["channel", "net_sales", "orders"], rows, ["net_sales", "orders"]),
                          "Channel a brought ₹500 net sales and channel b ₹300.")
    assert kinds(result) == ["TABLE"]


def test_the_line_that_introduced_a_dropped_listing_goes_with_it():
    from seleric_swarm.agent.answer_text import drop_enumerations, numeric_tokens

    cells = numeric_tokens("2026-10-09 2026-10-10 2026-10-11 2026-10-12 68,449 70,730 34,631 40,119")
    text = ("Sales peaked on 13 Oct.\n\nDay-by-day read (date · net sales):\n\n- 2026-10-09 — ₹68,449\n- 2026-10-10 — ₹70,730\n"
            "- 2026-10-11 — ₹34,631\n- 2026-10-12 — ₹40,119\n\nWorth checking: the dip.")
    out = drop_enumerations(text, cells)
    assert "Day-by-day" not in out and "Sales peaked" in out and "Worth checking" in out


def test_rounded_figures_and_one_line_series_count_as_the_visuals_data():
    from seleric_swarm.agent.answer_text import drop_enumerations, numeric_tokens, with_roundings

    cells = with_roundings(numeric_tokens("₹14.60 ₹34,506 1.4 4.6"))
    assert {"15", "14.6"} <= cells
    series = ("- Net sales: 10-09 ₹68,449 · 10-10 ₹70,730 · 10-11 ₹34,631 · 10-12 ₹40,119\n"
              "Finding: 11 Oct was weakest.")
    day_cells = numeric_tokens("2026-10-09 2026-10-10 2026-10-11 2026-10-12 68,449 70,730 34,631 40,119")
    out = drop_enumerations(series, day_cells)
    assert "68,449" not in out and "Finding" in out
