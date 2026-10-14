"""A chart for the main result when none was asked for, and the ``show_table`` tool."""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from seleric_swarm.agent.dependencies import ExecutionLimits, NullMcpClient, SelericDeps
from seleric_swarm.agent.loop import run_turn
from seleric_swarm.agent.mcp_toolset import tool_defs
from seleric_swarm.conversations.contracts import ContextBundle, Principal, PrincipalAuthMethod
from seleric_swarm.state.artifacts import InMemoryArtifactStore

DAYS = [(f"2026-10-{d:02d}", v) for d, v in zip(range(8, 13), (31000.5, 29500.25, 35000.0, 34376.9, 42177.33), strict=True)]
SERIES = {
    "query_id": "q_s", "columns": ["net_sales", "order_date", "order_date.day"],
    "rows": [{"order_date.day": f"{d}T00:00:00.000", "order_date": f"{d}T00:00:00.000", "net_sales": str(v)} for d, v in DAYS],
    "warnings": [],
    "provenance": {"query_id": "q_s", "metric_ids": ["net_sales"], "currency": "INR",
                   "time_range": {"start": "2026-10-08", "end": "2026-10-12"}},
}
TWO_ROWS = {**SERIES, "query_id": "q_2", "rows": SERIES["rows"][:2], "provenance": {**SERIES["provenance"], "query_id": "q_2"}}
DEFS = tool_defs([{"name": "metrics_query", "description": "Query metrics.", "inputSchema": {
    "type": "object", "properties": {"measures": {"type": "array", "items": {"type": "string"}}}, "required": ["measures"]}}])


class Gateway(NullMcpClient):
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    async def call(self, *, agent_id: str, capability: str, arguments: dict[str, Any]) -> Any:
        return dict(self.payload)


def _deps(store: InMemoryArtifactStore) -> SelericDeps:
    return SelericDeps(
        mission_id="m1", as_of=datetime(2026, 10, 13, tzinfo=UTC),
        principal=Principal(principal_id="p", workspace_id="w", user_id="u", authenticated=False, auth_method=PrincipalAuthMethod.ANONYMOUS),
        thread_id="t", run_id="r", trace_id="x", context=ContextBundle(), mcp_client=NullMcpClient(),
        artifact_store=store, limits=ExecutionLimits())


def _model(*steps: list[Any]) -> FunctionModel:
    queue = list(steps)

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=queue.pop(0) if len(queue) > 1 else queue[0])

    return FunctionModel(fn)


async def _run(model: FunctionModel, payload: dict[str, Any]):
    store = InMemoryArtifactStore()
    result = await run_turn(deps=_deps(store), model=model, gateway=Gateway(payload), tool_defs=DEFS, system_prompt="sys",
                            question="net sales last 5 days?", history=[])
    return result, store


def _charts(store: InMemoryArtifactStore) -> list[Any]:
    return [a for a in store.list_for_mission("m1") if a.artifact_type == "chart_spec"]


CALL = [ToolCallPart("metrics_query", {"measures": ["net_sales"]}, tool_call_id="c1")]
ANSWER = [TextPart("Net sales were ₹42,177 on 12 Oct, up from ₹34,377 the day before; 8 Oct was ₹31,001.")]


@pytest.mark.asyncio
async def test_a_multi_row_main_result_gets_a_chart_without_being_asked():
    result, store = await _run(_model(CALL, ANSWER), SERIES)
    charts = _charts(store)
    assert result.status == "completed" and len(charts) == 1
    assert charts[0].payload["chart_type"] == "line"  # a time axis draws a trend
    assert any(s["kind"] == "auto_chart" and s["query_id"] == "q_s" for s in result.state.steps)


@pytest.mark.asyncio
async def test_a_two_row_result_is_a_sentence_not_a_chart():
    result, store = await _run(_model(CALL, [TextPart("Net sales were ₹31,001, then ₹29,500.")]), TWO_ROWS)
    assert result.status == "completed" and not _charts(store)


@pytest.mark.asyncio
async def test_an_answer_that_quotes_nothing_from_the_result_gets_no_chart():
    _result, store = await _run(_model(CALL, [TextPart("I could not tell from this data.")]), SERIES)
    assert not _charts(store)


@pytest.mark.asyncio
async def test_a_chart_the_model_asked_for_is_not_doubled():
    show = [ToolCallPart("show_chart", {"query_ids": ["q_s"], "intent": "trend", "title": "Net sales"}, tool_call_id="c2")]
    result, store = await _run(_model(CALL, show, ANSWER), SERIES)
    assert len(_charts(store)) == 1
    assert not any(s["kind"] == "auto_chart" for s in result.state.steps)


@pytest.mark.asyncio
async def test_show_table_attaches_the_rows_as_a_table_part():
    table = [ToolCallPart("show_table", {"query_id": "q_s", "caption": "Net sales by day"}, tool_call_id="c2")]
    result, _store = await _run(_model(CALL, table, ANSWER), SERIES)
    (part,) = result.state.parts
    assert part["type"] == "TABLE" and part["content"]["caption"] == "Net sales by day"
    assert len(part["content"]["rows"]) == 5 and part["content"]["state"] == "ok"


@pytest.mark.asyncio
async def test_show_table_for_an_unknown_query_is_a_failed_result_not_an_exception():
    from seleric_swarm.agent.local_tools import build_local_tools
    from seleric_swarm.agent.mcp_toolset import TurnState

    state = TurnState()
    tool = next(t for t in build_local_tools(state) if t.__name__ == "show_table")
    res = await tool(SimpleNamespace(deps=None), "nope")
    assert getattr(res, "success", True) is False and not state.parts


def _part(qid: str, metric: str, rows: list[tuple[str, float]]) -> dict[str, Any]:
    return {
        "query_id": qid, "columns": ["campaign_name", metric],
        "rows": [{"campaign_name": n, metric: str(v)} for n, v in rows],
        "provenance": {"query_id": qid, "metric_ids": [metric], "currency": "INR",
                       "time_range": {"start": "2026-10-07", "end": "2026-10-13"}},
    }


COMPOSED = {
    "query_id": "q_parent", "composed": True, "composition": "multi_view",
    "parts": [
        _part("q_paid", "ad_spend", [("Alpha", 10268.0), ("Beta", 9707.0), ("Gamma", 8100.0), ("Delta", 7000.0)]),
        _part("q_shop", "net_sales", [("Alpha", 5884.0), ("Beta", 2293.0), ("Gamma", 4100.0), ("Delta", 3300.0)]),
    ],
}


@pytest.mark.asyncio
async def test_a_multi_view_result_writes_evidence_per_part_under_every_id_the_model_can_use():
    result, store = await _run(_model(CALL, [TextPart("Alpha spent ₹10,268 and sold ₹5,884; Beta spent ₹9,707.")]), COMPOSED)
    by_query = result.state.by_query
    assert len(by_query["q_paid"]) == 4 and len(by_query["q_shop"]) == 4
    assert set(by_query["q_parent"]) == set(by_query["q_paid"]) | set(by_query["q_shop"])
    assert len(result.state.evidence_ids) == 8
    assert result.status == "completed" and len(_charts(store)) == 1  # the part the answer quotes most is charted


@pytest.mark.asyncio
async def test_run_python_reads_the_rows_of_a_multi_view_part():
    code = "result = sum(float(e['value']) for e in evidence if e['metric_id'] == 'ad_spend')"
    py = [ToolCallPart("run_python", {"code": code, "query_ids": ["q_paid", "q_shop"]}, tool_call_id="c2")]
    seen: list[str] = []

    def answer(messages: list[ModelMessage]) -> list[Any]:
        from pydantic_ai.messages import ToolReturnPart

        seen.extend(str(p.content) for m in messages for p in m.parts if isinstance(p, ToolReturnPart))
        return [TextPart("Spend in total was ₹35,075.")]

    queue = [CALL, py, answer]

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        step = queue.pop(0) if len(queue) > 1 else queue[0]
        return ModelResponse(parts=step(messages) if callable(step) else step)

    _result, _ = await _run(FunctionModel(fn), COMPOSED)
    assert not any("no evidence_ids supplied" in s for s in seen), seen
    assert any("35075" in s for s in seen), seen
