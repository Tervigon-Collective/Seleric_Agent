"""Live Meta envelopes (real responses captured 2026-11-21 from the preview, trimmed) become query results the
evidence path reads: run_python must be able to compute over live rows (D-244 acceptance 2)."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from seleric_swarm.agent.dependencies import ExecutionLimits, NullMcpClient, SelericDeps
from seleric_swarm.agent.live_rows import HOUR_FIELD as HOUR
from seleric_swarm.agent.live_rows import as_query_result
from seleric_swarm.agent.mcp_toolset import all_evidence
from seleric_swarm.conversations.contracts import ContextBundle, Principal
from seleric_swarm.services.catalogue_bootstrap import CatalogueSnapshot
from seleric_swarm.state.artifacts import InMemoryArtifactStore

FIX = json.loads((Path(__file__).parent / "fixtures" / "meta_live.json").read_text())
TODAY = date(2026, 11, 21)
ARGS = {"object_id": "act_1685189008684458", "account_id": "act_1685189008684458"}


def _deps() -> SelericDeps:
    return SelericDeps(
        mission_id="m-live", as_of=datetime(2026, 11, 21, 16, 0, tzinfo=ZoneInfo("Asia/Kolkata")),
        principal=Principal(principal_id="p1", workspace_id="ws1", user_id="u1"), thread_id="t1", run_id="r1", trace_id="tr1",
        context=ContextBundle(), mcp_client=NullMcpClient(), artifact_store=InMemoryArtifactStore(), limits=ExecutionLimits(),
        catalogue=CatalogueSnapshot(metrics=[]),
    )


def test_hourly_insights_become_hour_rows_with_numbers():
    out = as_query_result("meta_insights_live", ARGS, FIX["insights_hourly"], TODAY)
    assert out["query_id"].startswith("live_") and "data" not in out
    assert out["columns"] == ["date.hour", "spend", "impressions", "clicks"]
    first = out["rows"][0]
    assert first["date.hour"] == "2026-11-21T00:00:00" and isinstance(first["spend"], float)
    ev = all_evidence(out, ARGS, _deps())
    assert {e.metric_id for e in ev} == {"spend", "impressions", "clicks"}
    assert {e.grain for e in ev} == {"hour"}
    assert sum(e.value for e in ev if e.metric_id == "spend") == sum(float(r["spend"]) for r in FIX["insights_hourly"]["data"])


def test_campaign_rows_keep_names_and_flatten_actions():
    out = as_query_result("meta_insights_live", ARGS, FIX["insights_campaign"], TODAY)
    assert {"campaign_id", "campaign_name"} <= set(out["columns"]) and "date.day" in out["columns"]
    assert "campaign_id" not in out["provenance"]["metric_ids"]  # numeric-looking ids stay dimensions
    flat = [c for c in out["columns"] if c.startswith("actions__")]
    assert flat and all("." not in c for c in flat)
    ev = all_evidence(out, ARGS, _deps())
    assert {e.dimensions["campaign_name"] for e in ev} == {r["campaign_name"] for r in FIX["insights_campaign"]["data"]}


def test_budgets_are_converted_from_paise():
    out = as_query_result("meta_campaigns_list", ARGS, FIX["campaigns_list"], TODAY)
    raw = FIX["campaigns_list"]["data"][0]["daily_budget"]
    assert out["rows"][0]["daily_budget"] == float(raw) / 100
    ev = [e for e in all_evidence(out, ARGS, _deps()) if e.metric_id == "daily_budget"]  # undated snapshot still loads
    assert len(ev) == len(FIX["campaigns_list"]["data"]) and ev[0].value == float(raw) / 100
    assert ev[0].dimensions["name"] == FIX["campaigns_list"]["data"][0]["name"]
    assert "id" in out["columns"] and "id" not in out["provenance"]["metric_ids"]


def test_errors_and_odd_shapes_pass_through_unchanged():
    err = {"success": False, "error": {"code": "INVALID_ARGUMENT", "message": "Provide account_id"}}
    assert as_query_result("meta_insights_live", ARGS, err, TODAY) is err
    single = {"success": True, "data": {"id": "1"}}
    assert as_query_result("meta_campaigns_get", ARGS, single, TODAY) is single
    assert as_query_result("meta_insights_live", ARGS, "plain text", TODAY) == "plain text"


def test_same_call_same_query_id():
    a = as_query_result("meta_insights_live", ARGS, FIX["insights_hourly"], TODAY)["query_id"]
    b = as_query_result("meta_insights_live", dict(reversed(list(ARGS.items()))), FIX["insights_hourly"], TODAY)["query_id"]
    assert a == b


def test_note_tells_the_model_the_run_python_shape():
    note = as_query_result("meta_insights_live", ARGS, FIX["insights_hourly"], TODAY)["note"]
    assert "e['metric_id'] == 'spend'" in note and "e['value']" in note and "period_start" in note


def test_rows_reaching_today_say_they_are_partial():
    note = as_query_result("meta_insights_live", ARGS, FIX["insights_hourly"], TODAY)["note"]
    assert "not over" in note and "same hours" in note
    later = as_query_result("meta_insights_live", ARGS, FIX["insights_hourly"], date(2026, 11, 25))["note"]
    assert "not over" not in later  # a closed day is complete


def _two_days():
    today = FIX["insights_hourly"]["data"]
    yday = [{**r, "date_start": "2026-11-20", "date_stop": "2026-11-20"} for r in today]
    return {**FIX["insights_hourly"], "data": yday + today, "paging": {"after": None, "has_next": False}}


def test_same_hours_sums_both_days_over_complete_hours_only():
    from seleric_swarm.agent.live_rows import same_hours

    hourly = as_query_result("meta_insights_live", ARGS, _two_days(), TODAY)
    out = same_hours(hourly, TODAY)
    hours = sorted({r["date.hour"][11:] for r in hourly["rows"]})
    done = [r for r in FIX["insights_hourly"]["data"] if str(r[HOUR])[:8] < hours[-1]]  # latest hour still running
    want = round(sum(float(r["spend"]) for r in done), 2)
    assert [r["date.day"] for r in out["rows"]] == ["2026-11-20", "2026-11-21"]
    assert out["rows"][0]["spend"] == out["rows"][1]["spend"] == want
    assert f"up to {hours[-1][:5]}" in out["note"] and out["query_id"].endswith("_same_hours")
    assert same_hours({**hourly, "paging": {"after": "x"}}, TODAY) is None  # a partial page is not compared


def test_same_hours_only_for_day_level_insights_reaching_today():
    from seleric_swarm.agent.live_rows import same_hours_args

    day = as_query_result("meta_insights_live", ARGS, FIX["insights_campaign"], TODAY)  # 2026-11-20 only
    assert same_hours_args("meta_insights_live", ARGS, day, TODAY) is None
    reaching = {**day, "provenance": {**day["provenance"], "time_range": {"start": "2026-11-20", "end": "2026-11-21"}}}
    extra = same_hours_args("meta_insights_live", {**ARGS, "level": "campaign"}, reaching, TODAY)
    assert extra["hourly"] is True and extra["time_range"] == {"since": "2026-11-20", "until": "2026-11-21"}
    assert "level" not in extra and extra["object_id"] == ARGS["object_id"]
    assert same_hours_args("meta_insights_live", {**ARGS, "hourly": True}, reaching, TODAY) is None
    assert same_hours_args("meta_campaigns_list", ARGS, reaching, TODAY) is None
