"""build_dataset_query on the Agent side: the dataset's handle behaves like a query_id for evidence, tables and charts;
every row is loaded (not just the preview) and recorded; the model sees schema, preview, stats and notes, not the rows."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from seleric_swarm.agent import present
from seleric_swarm.agent.dependencies import ExecutionLimits, NullMcpClient, SelericDeps
from seleric_swarm.agent.mcp_toolset import (
    ALLOW, DATASET_TOOL, TurnState, _take_dataset, compact, dataset_as_query_payload, load_dataset_rows, tool_defs, write_evidence,
)
from seleric_swarm.conversations.contracts import ContextBundle, Principal
from seleric_swarm.services.catalogue_bootstrap import CatalogueSnapshot
from seleric_swarm.state.artifacts import InMemoryArtifactStore

SCHEMA = [
    {"name": "bucket", "role": "time", "timezone": "Asia/Kolkata"},
    {"name": "platform", "role": "dimension"},
    {"name": "ad_spend", "role": "measure", "source_view": "paid_media_hourly", "aggregation": "additive"},
    {"name": "orders", "role": "measure", "source_view": "commerce", "aggregation": "additive"},
]
ROWS = [
    {"bucket": f"2026-10-22 {h:02d}:00", "platform": "meta", "ad_spend": 100 + h, "orders": None if h > 20 else h}
    for h in range(24)
]
BUILD = {
    "query_id": "ds_abc", "handle": "ds_abc", "name": "t", "grain": "hour", "timezone": "Asia/Kolkata", "row_count": len(ROWS),
    "columns": SCHEMA, "preview": ROWS[:3], "stats": {"ad_spend": {"sum": 1}}, "notes": ["n1"], "limitations": ["Amazon is not modelled"],
    "partial_bucket": None, "sources": [{"view": "commerce"}],
    "provenance": {"kind": "dataset", "grain": "hour", "time_range": {"start": "2026-10-22", "end": "2026-10-22"}, "sources": []},
    "how_to_use": "x",
}


def _deps(store: InMemoryArtifactStore) -> SelericDeps:
    return SelericDeps(
        mission_id="m-ds", as_of=datetime(2026, 10, 23, tzinfo=UTC),
        principal=Principal(principal_id="p1", workspace_id="ws1", user_id="u1"), thread_id="t1", run_id="r1", trace_id="tr1",
        context=ContextBundle(), mcp_client=NullMcpClient(), artifact_store=store, limits=ExecutionLimits(),
        catalogue=CatalogueSnapshot(metrics=[]),
    )


class FakeGateway:
    """Serves get pages of ROWS."""

    def __init__(self, fail_at: int | None = None):
        self.calls: list[dict[str, Any]] = []
        self.fail_at = fail_at

    async def call(self, *, agent_id: str, capability: str, arguments: dict[str, Any]) -> Any:
        self.calls.append(arguments)
        assert capability == f"seleric.{DATASET_TOOL}" and arguments["action"] == "get"
        off, lim = arguments["offset"], arguments["limit"]
        if self.fail_at is not None and off >= self.fail_at:
            return {"error": "boom", "code": "internal_error"}
        page = ROWS[off:off + 10]  # the fake server pages by 10, whatever limit was asked
        nxt = off + 10 if off + 10 < len(ROWS) else None
        return {"query_id": "ds_abc", "handle": "ds_abc", "rows": page, "next_offset": nxt, "row_count": len(ROWS),
                "columns": SCHEMA, "provenance": BUILD["provenance"]}


def test_tool_is_allowed_and_defined_when_the_server_lists_it() -> None:
    assert DATASET_TOOL in ALLOW
    defs = tool_defs([{"name": DATASET_TOOL, "description": "d", "inputSchema": {"type": "object"}}])
    assert [d["name"] for d in defs] == [DATASET_TOOL]


def test_adapter_gives_the_query_result_shape_other_tools_read() -> None:
    q = dataset_as_query_payload(BUILD, ROWS)
    assert q["query_id"] == "ds_abc" and q["columns"] == ["bucket.hour", "platform", "ad_spend", "orders"]
    assert q["provenance"]["metric_ids"] == ["ad_spend", "orders"]
    assert q["rows"][9]["bucket.hour"] == "2026-10-22T09:00:00.000"
    src = present.source_of(q)
    assert src is not None and src.time_col == "bucket.hour" and src.dims == ["platform"] and src.measures == ["ad_spend", "orders"]


def test_day_grain_stamps_midnight() -> None:
    day = {**BUILD, "grain": "day", "provenance": {**BUILD["provenance"], "grain": "day"}}
    q = dataset_as_query_payload(day, [{"bucket": "2026-10-22", "platform": "meta", "ad_spend": 1, "orders": 2}])
    assert q["columns"][0] == "bucket.day" and q["rows"][0]["bucket.day"] == "2026-10-22T00:00:00.000"


def test_evidence_keeps_the_hour_dimension_and_null_cells() -> None:
    store = InMemoryArtifactStore()
    q = dataset_as_query_payload(BUILD, ROWS)
    ids = write_evidence(q, {"action": "build"}, _deps(store))
    rows = [store.get(i).payload for i in ids]
    assert len(rows) == 24 * 2 and {r["grain"] for r in rows} == {"hour"}
    spend = {r["period_start"][:16]: r["value"] for r in rows if r["metric_id"] == "ad_spend"}
    assert spend["2026-10-22T09:00"] == 109.0
    assert all(r["dimensions"] == {"platform": "meta"} for r in rows)
    missing = [r for r in rows if r["metric_id"] == "orders" and r["value"] is None]
    assert len(missing) == 3  # hours 21..23 are missing, not zero


def test_evidence_cap_is_flagged_not_silent() -> None:
    store = InMemoryArtifactStore()
    q = dataset_as_query_payload(BUILD, ROWS)
    ids = write_evidence(q, {"action": "build"}, _deps(store), max_points=20)
    assert len(ids) == 20 and any("first" in w for w in q["warnings"])


def test_take_dataset_loads_every_page_records_evidence_and_hides_rows_from_the_model() -> None:
    store = InMemoryArtifactStore()
    state = TurnState()
    gw = FakeGateway()
    ctx = SimpleNamespace(deps=_deps(store))
    shown = asyncio.run(_take_dataset(ctx, gw, state, {"action": "build"}, dict(BUILD)))
    assert [c["offset"] for c in gw.calls] == [0, 10, 20]
    assert shown is not None and shown["rows_loaded"] == 24 and "rows" not in shown and "provenance" not in shown
    body = json.loads(compact(shown))
    assert body["preview"] and body["notes"] == ["n1"] and "ds_abc" in body["agent_note"]
    full = next(p for p in state.payloads if p.get("dataset"))
    assert len(full["rows"]) == 24
    assert state.by_query["ds_abc"] and len(state.by_query["ds_abc"]) == 48
    assert state.evidence[-1]["query_id"] == "ds_abc"


def test_a_failed_page_is_reported_not_hidden() -> None:
    store = InMemoryArtifactStore()
    state = TurnState()
    ctx = SimpleNamespace(deps=_deps(store))
    shown = asyncio.run(_take_dataset(ctx, FakeGateway(fail_at=10), state, {"action": "build"}, dict(BUILD)))
    assert shown["rows_loaded"] == 10 and any("Only 10 of 24" in w for w in shown["warnings"])


def test_load_dataset_rows_stops_with_the_reason() -> None:
    rows, why = asyncio.run(load_dataset_rows(FakeGateway(fail_at=0), "ds_abc", None))
    assert rows == [] and why and "boom" in why


def test_hourly_table_cells_keep_the_hour() -> None:
    q = dataset_as_query_payload(BUILD, ROWS)
    frame = present.frame_for(present.sources_of(q), {})
    content = present.table_content(_deps(InMemoryArtifactStore()), frame, query_id="ds_abc", limit=5)
    first = content["rows"][0]
    assert any(str(v).startswith("2026-10-22 00:00") for v in first.values())


def test_a_get_on_a_handle_from_an_earlier_turn_loads_it_for_the_analytics_tools() -> None:
    store = InMemoryArtifactStore()
    state = TurnState()
    gw = FakeGateway()
    ctx = SimpleNamespace(deps=_deps(store))
    page = {"query_id": "ds_abc", "handle": "ds_abc", "rows": ROWS[:3], "next_offset": 3, "row_count": len(ROWS)}
    shown = asyncio.run(_take_dataset(ctx, gw, state, {"action": "get", "handle": "ds_abc"}, page))
    assert shown["rows"] == ROWS[:3] and "ds_abc" in shown["agent_note"]  # the model still sees the page it asked for
    assert len(state.by_query["ds_abc"]) == 48
    full = next(p for p in state.payloads if p.get("dataset"))
    assert len(full["rows"]) == 24 and full["columns"][0] == "bucket.hour" and full["provenance"]["metric_ids"] == ["ad_spend", "orders"]
