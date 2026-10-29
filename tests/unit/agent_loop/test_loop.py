"""The single reasoning loop with a scripted model and a fake gateway (no network)."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from seleric_swarm.agent import loop as loop_mod
from seleric_swarm.agent.dependencies import ExecutionLimits, NullMcpClient, SelericDeps
from seleric_swarm.agent.loop import run_turn
from seleric_swarm.agent.mcp_toolset import tool_defs
from seleric_swarm.agent.model import EffortModel, begin_loop_effort, loop_effort, set_loop_effort
from seleric_swarm.conversations.contracts import ContextBundle, Principal, PrincipalAuthMethod
from seleric_swarm.state.artifacts import InMemoryArtifactStore

PAYLOAD = {
    "query_id": "q_1", "columns": ["net_sales", "order_date", "order_date.day"],
    "rows": [{"order_date.day": "2026-10-11T00:00:00.000", "order_date": "2026-10-11T00:00:00.000", "net_sales": "34376.9"},
             {"order_date.day": "2026-10-12T00:00:00.000", "order_date": "2026-10-12T00:00:00.000", "net_sales": "42177.33"}],
    "compare_rows": None, "warnings": [],
    "provenance": {"query_id": "q_1", "metric_ids": ["net_sales"], "currency": "INR",
                   "time_range": {"start": "2026-10-11", "end": "2026-10-12"}, "cube_query": {"x": 1}},
}
DEFS = tool_defs([{"name": "metrics_query", "description": "Query metrics.", "inputSchema": {
    "type": "object", "properties": {"measures": {"type": "array", "items": {"type": "string"}}}, "required": ["measures"]}}])


class FakeGateway(NullMcpClient):
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def call(self, *, agent_id: str, capability: str, arguments: dict[str, Any]) -> Any:
        self.calls.append({"capability": capability, "arguments": arguments})
        return dict(PAYLOAD)


def _deps(store: InMemoryArtifactStore) -> SelericDeps:
    return SelericDeps(
        mission_id="m1", as_of=datetime(2026, 10, 13, tzinfo=UTC),
        principal=Principal(principal_id="p", workspace_id="w", user_id="u", authenticated=False, auth_method=PrincipalAuthMethod.ANONYMOUS),
        thread_id="t", run_id="r", trace_id="x", context=ContextBundle(), mcp_client=NullMcpClient(),
        artifact_store=store, limits=ExecutionLimits())


def _scripted(*steps: Any) -> FunctionModel:
    """Each step is a list of parts (or a callable of the messages returning parts); consumed one per model request."""
    queue = list(steps)

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        step = queue.pop(0) if len(queue) > 1 else queue[0]
        return ModelResponse(parts=step(messages) if callable(step) else step)

    return FunctionModel(fn)


async def _turn(model: FunctionModel, gw: FakeGateway | None = None, store: InMemoryArtifactStore | None = None, question: str = "net sales?", **kw: Any):
    gw = gw or FakeGateway()
    store = store or InMemoryArtifactStore()
    result = await run_turn(deps=_deps(store), model=model, gateway=gw, tool_defs=DEFS, system_prompt="sys",
                            question=question, history=[], **kw)
    return result, gw, store


@pytest.mark.asyncio
async def test_tool_call_then_grounded_answer_writes_evidence():
    call = [ToolCallPart("metrics_query", {"measures": ["net_sales"], "extra_null": None}, tool_call_id="c1")]
    result, gw, store = await _turn(_scripted(call, [TextPart("Net sales were ₹42,177 yesterday, up from ₹34,377.")]))
    assert result.status == "completed" and not result.issues
    assert result.stats["llm_calls"] == 2 and result.stats["tool_calls"] == 1
    assert isinstance(result.stats.get("ttft_ms"), int) and result.stats["ttft_ms"] >= 0
    assert isinstance(result.stats.get("answer_ttft_ms"), int)
    assert result.stats["answer_ttft_ms"] >= result.stats["ttft_ms"]
    assert result.stats.get("total_ms") == result.stats.get("elapsed_ms")
    assert result.stats["llm_steps"] and "at_ms" in result.stats["llm_steps"][0]
    assert gw.calls[0]["arguments"] == {"measures": ["net_sales"]}  # explicit nulls are dropped
    assert [s["ok"] for s in result.state.steps if s["kind"] == "tool_return"] == [True]
    assert len(result.state.evidence_ids) == 2 and result.state.by_query["q_1"] == result.state.evidence_ids
    kinds = [a.artifact_type for a in store.list_for_mission("m1")]
    assert kinds.count("evidence") == 2
    assert result.state.evidence[0]["query_id"] == "q_1"


@pytest.mark.asyncio
async def test_identical_calls_in_one_step_run_once():
    call = [ToolCallPart("metrics_query", {"measures": ["net_sales"]}, tool_call_id=f"c{i}") for i in range(2)]
    result, gw, _ = await _turn(_scripted(call, [TextPart("Net sales were ₹42,177.")]))
    assert len(gw.calls) == 1 and result.stats["duplicate_calls"] == 1


@pytest.mark.asyncio
async def test_invalid_arguments_are_a_tool_result_not_an_exception():
    bad = [ToolCallPart("metrics_query", {"measures": "net_sales"}, tool_call_id="c1")]
    seen: list[str] = []

    def answer(messages: list[ModelMessage]) -> list[Any]:
        seen.extend(str(p.content) for m in messages for p in m.parts if isinstance(p, ToolReturnPart))
        return [TextPart("I could not query that.")]

    result, gw, _ = await _turn(_scripted(bad, answer))
    assert not gw.calls and any("Invalid arguments" in s for s in seen)
    assert [s["ok"] for s in result.state.steps if s["kind"] == "tool_return"] == [False]


@pytest.mark.asyncio
async def test_ungrounded_figure_gets_one_corrective_pass_then_ships_clean():
    call = [ToolCallPart("metrics_query", {"measures": ["net_sales"]}, tool_call_id="c1")]
    prompts: list[str] = []

    def second(messages: list[ModelMessage]) -> list[Any]:
        prompts.append(str(messages[-1].parts[-1].content))
        return [TextPart("Net sales were ₹42,177.")]

    result, _, _ = await _turn(_scripted(call, [TextPart("Net sales were ₹99,999.")], second))
    assert result.stats["revisions"] == 1 and not result.issues and "99,999" not in result.answer
    assert "Verification found problems" in prompts[0] and "99,999" in prompts[0]


@pytest.mark.asyncio
async def test_still_ungrounded_after_the_pass_is_flagged_not_hidden():
    call = [ToolCallPart("metrics_query", {"measures": ["net_sales"]}, tool_call_id="c1")]
    result, _, _ = await _turn(_scripted(call, [TextPart("Net sales were ₹99,999.")]))
    assert result.issues and "⚠ Not verified against the data" in result.answer
    assert "UNVERIFIED_FIGURES" in result.limitations and result.status == "partial"


@pytest.mark.asyncio
async def test_degenerate_output_is_never_shipped(monkeypatch):
    junk = "catalogue. If needed. Never invent. " * 60
    result, _, _ = await _turn(_scripted([TextPart(junk)]))
    assert "couldn't produce a reliable answer" in result.answer and result.stats["degenerate_retries"] == 1


@pytest.mark.asyncio
async def test_tool_budget_withdraws_tools_and_notes_it(monkeypatch):
    monkeypatch.setattr(loop_mod, "MAX_TOOL_CALLS", 1)
    offered: list[int] = []

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        offered.append(len(info.function_tools))
        if len(offered) == 1:
            return ModelResponse(parts=[ToolCallPart("metrics_query", {"measures": ["net_sales"]}, tool_call_id="c1")])
        return ModelResponse(parts=[TextPart("Net sales were ₹42,177.")])

    result, _, _ = await _turn(FunctionModel(fn))
    assert offered[0] > 0 and offered[1] == 0 and result.forced_final


@pytest.mark.asyncio
async def test_cancellation_stops_before_the_next_model_request():
    from seleric_swarm.cancellation import MissionCancelledError

    with pytest.raises(MissionCancelledError):
        await _turn(_scripted([TextPart("never")]), is_cancelled=lambda: True)


@pytest.mark.asyncio
async def test_escalation_set_in_a_tool_task_reaches_the_model_request():
    """Tools run in child tasks: the effort must survive that boundary."""
    seen: list[Any] = []

    class Inner(FunctionModel):
        pass

    inner = _scripted([TextPart("ok")])
    orig = inner.request

    async def spy(messages, settings, params):
        seen.append((settings or {}).get("openai_reasoning_effort"))
        return await orig(messages, settings, params)

    inner.request = spy  # type: ignore[method-assign]
    model = EffortModel(inner)
    begin_loop_effort()
    await asyncio.create_task(asyncio.sleep(0))
    await asyncio.create_task(_set_in_child())
    assert loop_effort() == "medium"
    await model.request([], None, inner.customize_request_parameters(__import__("pydantic_ai").models.ModelRequestParameters()))
    assert seen == ["medium"]


async def _set_in_child() -> None:
    set_loop_effort("medium")


@pytest.mark.asyncio
async def test_local_tools_register_and_are_traced():
    names: list[str] = []

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if not names:
            names.extend(sorted(t.name for t in info.function_tools))
            return ModelResponse(parts=[ToolCallPart("escalate", {"reason": "needs real analysis"}, tool_call_id="e1"),
                                        ToolCallPart("business_snapshot", {}, tool_call_id="b1")])
        return ModelResponse(parts=[TextPart("Done.")])

    async def snapshot() -> dict[str, Any]:
        return {"as_of": "2026-10-12", "metrics": [{"metric": "net_sales", "value": 42177.33}]}

    result, _, _ = await _turn(FunctionModel(fn), snapshot_loader=snapshot)
    assert {"diagnose_metric_change", "break_down_metric", "forecast_metrics", "explore_data", "run_python",
            "show_chart", "business_snapshot", "escalate", "metrics_query"} <= set(names)
    assert loop_effort() == "medium"  # escalate ran in a tool task and reached the mission's scope
    traced = {s["tool"]: s for s in result.state.steps if s["kind"] == "tool_return"}
    assert traced["escalate"]["ok"] and traced["business_snapshot"]["ok"] and "ms" in traced["escalate"]
    assert result.state.payloads and result.state.payloads[0]["as_of"] == "2026-10-12"


@pytest.mark.asyncio
async def test_a_correction_that_drops_the_table_is_discarded_and_the_draft_kept():
    """Live 2026-10-13: a corrective pass replaced a per-product table with grand totals."""
    rows = [{"product_title": f"Product {c}", "net_sales": str(1000 * (i + 1) + 0.4)} for i, c in enumerate("ABCDEFGH")]
    payload = {**PAYLOAD, "columns": ["product_title", "net_sales"], "rows": rows, "provenance": {**PAYLOAD["provenance"], "time_range": {"start": "2026-10-01", "end": "2026-10-12"}}}

    class Gw(FakeGateway):
        async def call(self, *, agent_id: str, capability: str, arguments: dict[str, Any]) -> Any:
            return dict(payload)

    table = "\n".join(f"| Product {c} | {1000 * (i + 1):,} |" for i, c in enumerate("ABCDEFGH")) + "\n| Product Z | 99,999 |"
    call = [ToolCallPart("metrics_query", {"measures": ["net_sales"]}, tool_call_id="c1")]
    result, _, _ = await _turn(_scripted(call, [TextPart(table)], [TextPart("Total net sales were ₹36,000.")]), gw=Gw())
    assert result.stats["revisions"] == 1 and result.stats.get("correction_discarded")
    assert "Product A" in result.answer and "Total net sales were" not in result.answer  # the table survived
    assert "⚠ Not verified against the data" in result.answer and "99,999" in result.answer


def test_the_model_sees_a_helper_summary_not_its_bookkeeping():
    from seleric_swarm.agent.local_tools import ToolView, _view
    from seleric_swarm.agent.output import ToolResult
    from seleric_swarm.conversations.contracts import ArtifactProvenance

    big = ToolResult(success=True, summary="Net sales fell 12%.", artifact_ids=[f"a{i}" for i in range(500)], warnings=["partial day"],
                     provenance=ArtifactProvenance(source_metadata={"diagnosis": {"x": "y" * 65000}}))
    view = _view(big)
    assert isinstance(view, ToolView) and view.summary == "Net sales fell 12%." and view.evidence_rows == 500
    assert len(view.model_dump_json()) < 400
    failed = _view(ToolResult(success=False, summary="no data", error_code="INSUFFICIENT_EVIDENCE"))
    assert failed.success is False and failed.error_code == "INSUFFICIENT_EVIDENCE"
    assert _view({"available": False}) == {"available": False}


@pytest.mark.asyncio
async def test_metric_ids_in_the_prose_are_written_as_catalogue_names():
    from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta, CatalogueSnapshot

    store = InMemoryArtifactStore()
    deps = _deps(store)
    object.__setattr__(deps, "catalogue", CatalogueSnapshot(metrics=(
        CatalogueMetricMeta(id="pnl_return_revenue", label="P&L return revenue"),
        CatalogueMetricMeta(id="net_sales", label="Net sales"))))
    call = [ToolCallPart("metrics_query", {"measures": ["net_sales"]}, tool_call_id="c1")]
    result = await run_turn(deps=deps, model=_scripted(call, [TextPart("pnl_return_revenue was ₹42,177 and the pnl_return_revenue_total stayed.")]),
                            gateway=FakeGateway(), tool_defs=DEFS, system_prompt="s", question="q", history=[])
    assert result.answer.startswith("P&L return revenue was ₹42,177") and "pnl_return_revenue_total" in result.answer


def test_raw_floats_are_rounded_for_display_but_real_precision_is_kept():
    from seleric_swarm.agent.answer_text import round_long_decimals

    text = "Net profit: ₹-41,920.83203758388; spend ₹73,136.85; ROAS 0.838; share 7.1%; ratio 0.12345678; id SKU12345.678901"
    out = round_long_decimals(text)
    assert "₹-41,921;" in out and "₹73,136.85" in out and "ROAS 0.838" in out and "7.1%" in out
    assert "0.12" in out and "0.12345678" not in out and "SKU12345.678901" in out


@pytest.mark.asyncio
async def test_run_python_reads_the_fetched_rows_as_a_list_with_query_ids():
    """Live 2026-10-13: 16 of 16 run_python calls failed (evidence treated as a dict keyed by query id, no `result`)."""
    code = (
        "rows = [e for e in evidence if e['metric_id'] == 'net_sales' and e['query_id'] == 'q_1']\n"
        "result = {'total': sum(e['value'] for e in rows), 'days': len(rows)}\n"
    )
    seen: list[str] = []

    def answer(messages: list[ModelMessage]) -> list[Any]:
        seen.extend(str(p.content) for m in messages for p in m.parts if isinstance(p, ToolReturnPart) and p.tool_name == "run_python")
        return [TextPart("Net sales totalled ₹76,554 over 2 days.")]

    calls = [ToolCallPart("metrics_query", {"measures": ["net_sales"]}, tool_call_id="c1")]
    run = [ToolCallPart("run_python", {"code": code, "query_ids": ["q_1"]}, tool_call_id="c2")]
    result, _, _ = await _turn(_scripted(calls, run, answer))
    traced = [s for s in result.state.steps if s["kind"] == "tool_return" and s["tool"] == "run_python"]
    assert traced and traced[0]["ok"], traced
    assert "total" in seen[0] and "76554" in seen[0].replace(",", "")  # the computed total is in what the model sees


@pytest.mark.asyncio
async def test_run_python_name_error_restates_what_data_exists():
    """Live 2026-10-14: the model invented `q_<id>` / `__query_results__` variables four times in a row."""
    seen: list[str] = []

    def answer(messages: list[ModelMessage]) -> list[Any]:
        seen.extend(str(p.content) for m in messages for p in m.parts if isinstance(p, ToolReturnPart) and p.tool_name == "run_python")
        return [TextPart("done")]

    calls = [ToolCallPart("metrics_query", {"measures": ["net_sales"]}, tool_call_id="c1")]
    run = [ToolCallPart("run_python", {"code": "result = q_1['rows']", "query_ids": ["q_1"]}, tool_call_id="c2")]
    await _turn(_scripted(calls, run, answer))
    assert "NameError" in seen[0] and "only data available is `evidence`" in seen[0] and "query_id" in seen[0]


@pytest.mark.asyncio
async def test_a_filtered_final_step_is_retried_from_the_gathered_evidence():
    call = [ToolCallPart("metrics_query", {"measures": ["net_sales"]}, tool_call_id="c1")]
    junk = "catalogue. If needed. Never invent. " * 60
    queries: list[int] = []

    def retry(messages: list[ModelMessage]) -> list[Any]:
        queries.append(sum(isinstance(p, ToolReturnPart) for m in messages for p in m.parts))
        return [TextPart("Net sales were ₹42,177.")]

    result, gw, _ = await _turn(_scripted(call, [TextPart(junk)], retry))
    assert result.stats["degenerate_retries"] == 1 and "42,177" in result.answer
    assert len(gw.calls) == 1 and queries == [1]  # no second fetch; the retry saw the first tool result


@pytest.mark.asyncio
async def test_a_failed_call_is_not_replayed_from_the_cache():
    class Flaky(FakeGateway):
        async def call(self, *, agent_id: str, capability: str, arguments: dict[str, Any]) -> Any:
            self.calls.append({"capability": capability, "arguments": arguments})
            if len(self.calls) == 1:
                raise TimeoutError("upstream slow")
            return dict(PAYLOAD)

    call = lambda cid: [ToolCallPart("metrics_query", {"measures": ["net_sales"]}, tool_call_id=cid)]
    result, gw, _ = await _turn(_scripted(call("c1"), call("c2"), [TextPart("Net sales were ₹42,177.")]), Flaky())
    assert len(gw.calls) == 2 and result.status == "completed"
    assert [s["ok"] for s in result.state.steps if s["kind"] == "tool_return"] == [False, True]


def test_compact_value_stays_data_when_too_large():
    from seleric_swarm.agent.mcp_toolset import compact_value

    big = {"rows": [{"k": "x" * 30000}], "total": 7}
    kept = compact_value(big)
    assert kept["total"] == 7 and isinstance(kept["rows"], list)


@pytest.mark.asyncio
async def test_a_long_answer_cut_by_the_length_limit_is_shipped_marked_not_discarded():
    cut = "Net sales by campaign: " + "Alpha 1 beta gamma delta, " * 20  # substantive, then cut at the cap
    steps = [ModelResponse(parts=[TextPart(cut)], finish_reason="length")] * 3

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return steps[0]

    result, _, _ = await _turn(FunctionModel(fn))
    assert result.answer.startswith("Net sales by campaign") and "cut off at the length limit" in result.answer
    assert "ANSWER_TRUNCATED" in result.limitations


@pytest.mark.asyncio
async def test_figures_with_no_tool_call_are_checked_against_the_conversation_only():
    result, _, _ = await _turn(_scripted([TextPart("Net sales were ₹99,999.")]))
    assert result.issues  # nothing fetched, nothing in the question: the figure is unverified
    quoted, _, _ = await _turn(_scripted([TextPart("Net sales were ₹99,999.")]), question="net sales were 99,999, right?")
    assert not quoted.issues  # the user's own figure may be repeated


def test_a_model_going_on_cooldown_is_logged_with_whether_a_fallback_is_ready():
    from structlog.testing import capture_logs

    from seleric_swarm.agent.model_health import ModelHealth

    health = ModelHealth(clock=lambda: 0.0)
    health.register("loop:gpt-5-mini"), health.register("loop:gpt-4o")
    with capture_logs() as logs:
        health.cool("loop:gpt-5-mini", 45.0)
    assert logs == [{"event": "llm_model_cooling", "log_level": "warning", "model": "loop:gpt-5-mini", "seconds": 45.0, "fallback_ready": True}]
