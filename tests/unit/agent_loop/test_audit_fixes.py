"""Regression cases for the 2026-10-15 code audit of the rebuilt agent (one per defect)."""
from __future__ import annotations

import asyncio
from datetime import date

import pytest
from pydantic_ai.messages import ToolReturnPart

from seleric_swarm.agent.answer_text import round_long_decimals
from seleric_swarm.agent.local_tools import _tracked, build_local_tools
from seleric_swarm.agent.mcp_toolset import (
    TurnState,
    _bucket_end,
    compact,
    flag_composed,
    flag_partial,
)
from seleric_swarm.agent.model import begin_loop_effort, loop_effort, set_loop_effort
from seleric_swarm.agent.output import ToolResult
from seleric_swarm.agent.progress import _tool_problem
from seleric_swarm.agent.runner import _thread_memory, _user_facing_agent_failure


def test_partial_window_is_flagged_for_datetime_ends_and_for_parts():
    today = date(2026, 10, 15)
    for end in ("2026-10-15", "2026-10-15T00:00:00", "2026-10-15T00:00:00.000"):
        payload = {"provenance": {"time_range": {"end": end}}}
        flag_partial(payload, today)
        assert payload["warnings"], end
    composed = {"parts": [{"provenance": {"time_range": {"end": "2026-10-10"}}},
                          {"provenance": {"time_range": {"end": "2026-10-15"}}}]}
    flag_partial(composed, today)
    assert composed["warnings"]
    done = {"provenance": {"time_range": {"end": "2026-10-14"}}}
    flag_partial(done, today)
    assert "warnings" not in done


def test_parts_with_different_breakdowns_are_not_reported_as_missing_entities():
    by_campaign = {"rows": [{"campaign": "Alpha", "spend": "1"}, {"campaign": "Beta", "spend": "2"}]}
    by_platform = {"rows": [{"platform": "Meta", "spend": "3"}]}
    payload = {"parts": [by_campaign, by_platform]}
    flag_composed(payload)
    assert "warnings" not in payload
    same = {"parts": [by_campaign, {"rows": [{"campaign": "Alpha", "orders": "4"}]}]}
    flag_composed(same)
    assert "Beta" in same["warnings"][0]


def test_an_empty_result_set_stays_visible_to_the_model():
    assert '"rows":[]' in compact({"rows": [], "warnings": [], "x": ""})


def test_quarter_and_year_buckets_span_their_real_period():
    assert _bucket_end(date(2026, 7, 1), "quarter", None) == date(2026, 9, 30)
    assert _bucket_end(date(2026, 1, 1), "year", None) == date(2026, 12, 31)
    assert _bucket_end(date(2026, 12, 1), "month", None) == date(2026, 12, 31)


def test_a_failed_diagnosis_does_not_lower_the_reasoning_effort():
    state = TurnState()

    async def diagnose_metric_change(**_: object) -> ToolResult:
        return ToolResult(success=False, summary="refused", error_code="INSUFFICIENT_EVIDENCE")

    async def scenario() -> str | None:
        begin_loop_effort()
        set_loop_effort("high")
        await _tracked(state, diagnose_metric_change, raises_effort=True)()
        return loop_effort()

    assert asyncio.run(scenario()) == "high"


def test_mcp_error_bodies_show_as_failed_in_the_progress_feed():
    failed = ToolReturnPart(tool_name="metrics_query", content='{"error":"Tool timed out after 45s"}', tool_call_id="c")
    fine = ToolReturnPart(tool_name="metrics_query", content='{"rows":[]}', tool_call_id="c")
    assert _tool_problem(failed) == ("Tool timed out after 45s", False)
    assert _tool_problem(fine) == (None, False)


@pytest.mark.asyncio
async def test_run_python_with_an_unknown_query_id_says_so_instead_of_reading_everything():
    state = TurnState()
    tool = next(t for t in build_local_tools(state) if t.__name__ == "run_python")
    result = await tool(None, code="result = 1", query_ids=["q_missing"])
    assert result.success is False and "q_missing" in result.summary


def test_failure_text_is_chosen_by_type_not_by_digits_in_a_message():
    assert _user_facing_agent_failure(RuntimeError("request 4291 failed"))[1] == "V3_AGENT_FAILED"
    assert _user_facing_agent_failure(TimeoutError("slow"))[1] == "V3_AGENT_TIMEOUT"


def test_thread_memory_is_looked_up_inside_the_workspace():
    seen: list[tuple[str, str]] = []

    class Store:
        def list_for_context(self, workspace_id: str, thread_id: str) -> list[object]:
            seen.append((workspace_id, thread_id))
            return []

    _thread_memory(Store(), "ws-1", "t-1")
    assert seen == [("ws-1", "t-1")]


def test_small_rates_keep_their_significance_when_rounded_for_display():
    assert round_long_decimals("rate 0.0034212 and 1.234567 and 12345.67891") == "rate 0.003421 and 1.23 and 12,346"


def test_a_non_ascii_api_key_header_is_a_401_not_a_crash():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from seleric_swarm.api.security import ApiSecurityMiddleware

    app = FastAPI()

    @app.get("/v1/x")
    def x() -> dict[str, int]:
        return {"ok": 1}

    app.add_middleware(ApiSecurityMiddleware, api_key="shared-secret", rate_limit_enabled=False)
    response = TestClient(app).get("/v1/x", headers={"x-api-key": "caf\u00e9".encode("latin-1")})
    assert response.status_code == 401
