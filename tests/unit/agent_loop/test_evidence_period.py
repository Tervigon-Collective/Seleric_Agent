"""write_evidence labels each row with the side of a compare_period query it came from (P0.1).

Live 2026-10-17 MS3-ac2bc6865d: compare rows were told apart only by a datetime window, a script guessed wrong and both windows
were summed as "previous"."""

from __future__ import annotations

from datetime import UTC, datetime

from seleric_swarm.agent.dependencies import ExecutionLimits, NullMcpClient, SelericDeps
from seleric_swarm.agent.mcp_toolset import write_evidence
from seleric_swarm.conversations.contracts import ContextBundle, Principal
from seleric_swarm.services.catalogue_bootstrap import CatalogueSnapshot
from seleric_swarm.state.artifacts import InMemoryArtifactStore


def _deps(store: InMemoryArtifactStore, as_of: datetime | None = None) -> SelericDeps:
    return SelericDeps(
        mission_id="m-period", as_of=as_of or datetime(2026, 10, 17, tzinfo=UTC),
        principal=Principal(principal_id="p1", workspace_id="ws1", user_id="u1"), thread_id="t1", run_id="r1", trace_id="tr1",
        context=ContextBundle(), mcp_client=NullMcpClient(), artifact_store=store, limits=ExecutionLimits(),
        catalogue=CatalogueSnapshot(metrics=[]),
    )


PAYLOAD = {
    "query_id": "q_cmp",
    "columns": ["campaign_name", "ad_spend"],
    "rows": [{"campaign_name": "PMax", "ad_spend": "7063.7"}],
    "compare_rows": [{"campaign_name": "PMax", "ad_spend": "9463.4"}],
    "provenance": {
        "metric_ids": ["ad_spend"], "currency": "INR",
        "time_range": {"start": "2026-10-16", "end": "2026-10-17"},
        "compare_period": {"start": "2026-10-14", "end": "2026-10-15"},
    },
}


def test_compare_rows_are_labelled_previous_and_main_rows_current() -> None:
    store = InMemoryArtifactStore()
    ids = write_evidence(PAYLOAD, {"measures": ["ad_spend"]}, _deps(store))
    rows = [store.get(i).payload for i in ids]
    assert {(r["period"], r["value"]) for r in rows} == {("current", 7063.7), ("previous", 9463.4)}


def test_equal_values_in_both_windows_are_two_rows_not_one() -> None:
    store = InMemoryArtifactStore()
    payload = {**PAYLOAD, "compare_rows": [{"campaign_name": "PMax", "ad_spend": "7063.7"}],
               "provenance": {**PAYLOAD["provenance"], "compare_period": {"start": "2026-10-16", "end": "2026-10-17"}}}
    ids = write_evidence(payload, {"measures": ["ad_spend"]}, _deps(store))
    assert len(ids) == 2 and len(set(ids)) == 2


def test_rows_written_before_the_field_existed_load_as_current() -> None:
    from seleric_swarm.agent.artifacts import EvidenceArtifact

    old = {"metric_id": "ad_spend", "dimensions": {}, "grain": "none", "as_of": "2026-10-17T00:00:00+00:00",
           "period_start": "2026-10-16T00:00:00+00:00", "period_end": "2026-10-17T00:00:00+00:00", "value": 1.0,
           "source_query": {}}
    assert EvidenceArtifact.model_validate(old).period == "current"


def test_hourly_rows_keep_their_timestamp_not_midnight() -> None:
    # live Tilting Heads paid-media hourly: every hour collapsed to midnight, chart kept last hour only (₹517)
    store = InMemoryArtifactStore()
    payload = {
        "query_id": "q_hour",
        "columns": ["report_date.hour", "ad_spend", "clicks"],
        "rows": [
            {"report_date.hour": "2026-10-10T09:00:00.000", "ad_spend": "1200", "clicks": "40"},
            {"report_date.hour": "2026-10-10T14:00:00.000", "ad_spend": "517", "clicks": "25"},
            {"report_date.hour": "2026-10-14T10:00:00.000", "ad_spend": "800", "clicks": "30"},
        ],
        "provenance": {
            "metric_ids": ["ad_spend", "clicks"], "currency": "INR",
            "time_range": {"start": "2026-10-07", "end": "2026-10-20"},
        },
    }
    ids = write_evidence(payload, {"measures": ["ad_spend", "clicks"]}, _deps(store))
    rows = [store.get(i).payload for i in ids]
    assert len(rows) == 6  # 3 hours × 2 measures
    assert {r["grain"] for r in rows} == {"hour"}
    starts = sorted({r["period_start"][:16] for r in rows})
    assert starts == ["2026-10-10T09:00", "2026-10-10T14:00", "2026-10-14T10:00"]
    spend = {r["period_start"][:16]: r["value"] for r in rows if r["metric_id"] == "ad_spend"}
    assert spend["2026-10-10T09:00"] == 1200.0 and spend["2026-10-10T14:00"] == 517.0


# ---- a long hourly series reaches the script whole, and a cut is never silent ---------------------------------------
def _hourly_payload(days: int, measures: list[str]) -> dict:
    from datetime import timedelta

    start = datetime(2026, 9, 21)
    rows = []
    for h in range(days * 24):
        stamp = (start + timedelta(hours=h)).strftime("%Y-%m-%dT%H:00:00.000")
        rows.append({"report_hour.hour": stamp, "report_hour": stamp, **{m: str(h % 7 + 1) for m in measures}})
    return {
        "query_id": "q_hourly", "columns": [*measures, "report_hour", "report_hour.hour"], "rows": rows,
        "provenance": {"metric_ids": measures, "currency": "INR",
                       "time_range": {"start": "2026-09-21", "end": f"2026-10-{days - 10:02d}"}},
    }


def _ist_deps(store: InMemoryArtifactStore) -> SelericDeps:
    from zoneinfo import ZoneInfo

    return _deps(store, datetime(2026, 10, 21, tzinfo=ZoneInfo("Asia/Kolkata")))


def test_thirty_days_of_hourly_rows_with_six_measures_are_all_kept() -> None:
    measures = ["ad_spend", "impressions", "clicks", "ctr", "cpc", "landing_page_views"]
    payload = _hourly_payload(30, measures)
    store = InMemoryArtifactStore()
    ids = write_evidence(payload, {"measures": measures}, _ist_deps(store))
    rows = [store.get(i).payload for i in ids]
    assert len(rows) == 720 * 6
    assert "warnings" not in payload
    spend = [r for r in rows if r["metric_id"] == "ad_spend"]
    assert sum(r["value"] for r in spend) == sum(float(r["ad_spend"]) for r in payload["rows"])
    assert min(r["period_start"] for r in spend) < "2026-09-22" and max(r["period_start"] for r in spend) >= "2026-10-20"


def test_naive_cube_hours_keep_the_calendar_day_they_were_reported_for() -> None:
    payload = {"query_id": "q_h", "columns": ["ad_spend", "report_hour.hour"],
               "rows": [{"report_hour.hour": "2026-10-20T23:00:00.000", "ad_spend": "5"}],
               "provenance": {"metric_ids": ["ad_spend"], "time_range": {"start": "2026-10-20", "end": "2026-10-20"}}}
    store = InMemoryArtifactStore()
    ids = write_evidence(payload, {"measures": ["ad_spend"]}, _ist_deps(store))
    assert store.get(ids[0]).payload["period_start"].startswith("2026-10-20T23:00:00")


def test_evidence_over_the_cap_is_flagged_and_cut_on_whole_rows() -> None:
    from seleric_swarm.agent import mcp_toolset

    measures = ["ad_spend", "clicks"]
    payload = _hourly_payload(10, measures)
    store = InMemoryArtifactStore()
    old, mcp_toolset.MAX_EVIDENCE_ROWS = mcp_toolset.MAX_EVIDENCE_ROWS, 100
    try:
        ids = write_evidence(payload, {"measures": measures}, _ist_deps(store))
    finally:
        mcp_toolset.MAX_EVIDENCE_ROWS = old
    rows = [store.get(i).payload for i in ids]
    assert len(rows) == 100 and sum(r["metric_id"] == "ad_spend" for r in rows) == 50 == sum(r["metric_id"] == "clicks" for r in rows)
    assert "Only the first 50 of 240 rows" in payload["warnings"][0]
    assert store.get(ids[0]).provenance.source_metadata["evidence_truncated"].startswith("50 of 240 rows")
