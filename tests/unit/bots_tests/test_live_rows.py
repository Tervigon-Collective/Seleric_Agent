"""Live Meta envelopes (real responses captured 2026-11-21 from the preview, trimmed) become query results the
evidence path reads: run_python must be able to compute over live rows (D-244 acceptance 2)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from seleric_swarm.agent.dependencies import ExecutionLimits, NullMcpClient, SelericDeps
from seleric_swarm.agent.live_rows import as_query_result
from seleric_swarm.agent.mcp_toolset import all_evidence
from seleric_swarm.conversations.contracts import ContextBundle, Principal
from seleric_swarm.services.catalogue_bootstrap import CatalogueSnapshot
from seleric_swarm.state.artifacts import InMemoryArtifactStore
from zoneinfo import ZoneInfo

FIX = json.loads((Path(__file__).parent / "fixtures" / "meta_live.json").read_text())
ARGS = {"object_id": "act_1685189008684458", "account_id": "act_1685189008684458"}


def _deps() -> SelericDeps:
    return SelericDeps(
        mission_id="m-live", as_of=datetime(2026, 11, 21, 16, 0, tzinfo=ZoneInfo("Asia/Kolkata")),
        principal=Principal(principal_id="p1", workspace_id="ws1", user_id="u1"), thread_id="t1", run_id="r1", trace_id="tr1",
        context=ContextBundle(), mcp_client=NullMcpClient(), artifact_store=InMemoryArtifactStore(), limits=ExecutionLimits(),
        catalogue=CatalogueSnapshot(metrics=[]),
    )


def test_hourly_insights_become_hour_rows_with_numbers():
    out = as_query_result("meta_insights_live", ARGS, FIX["insights_hourly"])
    assert out["query_id"].startswith("live_") and "data" not in out
    assert out["columns"] == ["date.hour", "spend", "impressions", "clicks"]
    first = out["rows"][0]
    assert first["date.hour"] == "2026-11-21T00:00:00" and isinstance(first["spend"], float)
    ev = all_evidence(out, ARGS, _deps())
    assert {e.metric_id for e in ev} == {"spend", "impressions", "clicks"}
    assert {e.grain for e in ev} == {"hour"}
    assert sum(e.value for e in ev if e.metric_id == "spend") == sum(float(r["spend"]) for r in FIX["insights_hourly"]["data"])


def test_campaign_rows_keep_names_and_flatten_actions():
    out = as_query_result("meta_insights_live", ARGS, FIX["insights_campaign"])
    assert {"campaign_id", "campaign_name"} <= set(out["columns"]) and "date.day" in out["columns"]
    assert "campaign_id" not in out["provenance"]["metric_ids"]  # numeric-looking ids stay dimensions
    flat = [c for c in out["columns"] if c.startswith("actions__")]
    assert flat and all("." not in c for c in flat)
    ev = all_evidence(out, ARGS, _deps())
    assert {e.dimensions["campaign_name"] for e in ev} == {r["campaign_name"] for r in FIX["insights_campaign"]["data"]}


def test_budgets_are_converted_from_paise():
    out = as_query_result("meta_campaigns_list", ARGS, FIX["campaigns_list"])
    raw = FIX["campaigns_list"]["data"][0]["daily_budget"]
    assert out["rows"][0]["daily_budget"] == float(raw) / 100
    assert "id" in out["columns"] and "id" not in out["provenance"]["metric_ids"]


def test_errors_and_odd_shapes_pass_through_unchanged():
    err = {"success": False, "error": {"code": "INVALID_ARGUMENT", "message": "Provide account_id"}}
    assert as_query_result("meta_insights_live", ARGS, err) is err
    single = {"success": True, "data": {"id": "1"}}
    assert as_query_result("meta_campaigns_get", ARGS, single) is single
    assert as_query_result("meta_insights_live", ARGS, "plain text") == "plain text"


def test_same_call_same_query_id():
    a = as_query_result("meta_insights_live", ARGS, FIX["insights_hourly"])["query_id"]
    b = as_query_result("meta_insights_live", dict(reversed(list(ARGS.items()))), FIX["insights_hourly"])["query_id"]
    assert a == b
