from __future__ import annotations

import asyncio
from datetime import date, timedelta

from seleric_swarm.agent.live_rows import HOUR_FIELD
from seleric_swarm.agent.mcp_toolset import tool_defs
from seleric_swarm.services.bots import eda, store, worker

TODAY = date(2026, 11, 23)
LPV = eda.LPV


def _hour(h: int, spend: float) -> dict:
    return {HOUR_FIELD: f"{h:02d}:00:00 - {h:02d}:59:59", "spend": str(spend), "impressions": "100",
            "inline_link_clicks": "2", "actions": [{"action_type": "landing_page_view", "value": "1"}]}


def _wh_hours(day: date, per_hour: float, hours: int = 24) -> list[dict]:
    return [{"report_hour": f"{day}T{h:02d}:00:00.000", "ad_spend": per_hour, "impressions": 100, "link_clicks": 2, LPV: 1}
            for h in range(hours)]


def test_latest_live_hour_is_not_complete():
    done, by_hour = eda.today_by_hour([_hour(0, 10), _hour(1, 10), _hour(2, 99)])
    assert done == 2 and by_hour[2]["ad_spend"] == 99 and by_hour[0][LPV] == 1


def test_day_with_hourly_holes_is_left_out_not_read_as_slow():
    good, holed = TODAY - timedelta(days=1), TODAY - timedelta(days=2)
    rows = _wh_hours(good, 10) + _wh_hours(holed, 10, hours=3)  # holed: 3 of 24 hours arrived
    daily = {str(d): {"ad_spend": 240.0, "impressions": 2400.0, "link_clicks": 48.0, LPV: 24.0} for d in (good, holed)}
    by_now, dropped = eda.history_by_day(rows, 12, daily)
    assert dropped == [str(holed)] and by_now == {str(good): {"ad_spend": 120.0, "impressions": 1200.0, "link_clicks": 24.0, LPV: 12.0}}


def test_where_today_sits_compares_the_same_hours():
    days = [TODAY - timedelta(days=i) for i in range(1, 11)]
    daily = {str(d): {"ad_spend": 240.0 * i, "impressions": 2400.0, "link_clicks": 48.0, LPV: 24.0} for i, d in enumerate(days, 1)}
    rows = [r for i, d in enumerate(days, 1) for r in _wh_hours(d, 10.0 * i)]
    by_now, _ = eda.history_by_day(rows, 12, daily)
    today_now = {"ad_spend": 600.0, "impressions": 1200.0, "link_clicks": 24.0, LPV: 12.0}
    spend = next(r for r in eda.where_today_sits(today_now, by_now, daily, TODAY, 12) if r["metric"] == "ad_spend")
    # history by 12:00 is 120, 240 ... 1200; 600 is the 5th of 10
    assert spend["percentile_vs_56d"] == 0.5 and spend["p50"] == 660.0 and spend["usual_share_of_day_by_now"] == 0.5
    assert spend["yesterday_same_hours"] == 120.0 and spend["projected_full_day"] == 1200.0


def test_drivers_skip_ended_campaigns_and_rank_by_gap():
    y, old = str(TODAY - timedelta(days=1)), str(TODAY - timedelta(days=9))
    hist = [{"campaign_id": "A", "campaign_name": "a", "report_date": y, "ad_spend": 1000, "impressions": 10, "link_clicks": 1, LPV: 1},
            {"campaign_id": "B", "campaign_name": "b", "report_date": y, "ad_spend": 100, "impressions": 10, "link_clicks": 1, LPV: 1},
            {"campaign_id": "Z", "campaign_name": "ended", "report_date": old, "ad_spend": 5000, "impressions": 10, "link_clicks": 1, LPV: 1}]
    live = [{"campaign_id": "B", "campaign_name": "b", "spend": "300", "impressions": "10", "inline_link_clicks": "1"}]
    out = [{"campaign_id": "B", "report_date": str(TODAY), "ssot_campaign_ad_spend": 300, "ssot_campaign_orders": 2, "ssot_campaign_gross_sales": 900}]
    rows, n, conc = eda.drivers(live, hist, out, 0.5, TODAY)
    assert [r["campaign_id"] for r in rows] == ["A", "B"] and n == 2  # Z ended; A stopped today: gap -500 beats B's +250
    assert rows[0]["gap_to_expected"] == -500.0 and rows[1]["cpa_today"] == 150.0 and rows[1]["roas_today"] == 3.0
    assert conc["top_share_today"] == 1.0
    a_hist = [{"campaign_id": "A", "campaign_name": "a", "report_date": y, "ssot_campaign_ad_spend": 1000,
               "ssot_campaign_orders": 1, "ssot_campaign_gross_sales": 1}]
    live_a = [{"campaign_id": "A", "campaign_name": "a", "spend": "2000", "impressions": "10", "inline_link_clicks": "1"}]
    row = eda.drivers(live_a, hist, a_hist, 0.5, TODAY)[0][0]
    assert row["expected_orders_by_now"] == 2.0 and row["p_zero_orders_at_28d_rate"] == 0.135  # e^-2


def test_fatigue_ranks_spend_weighted_ctr_fall():
    rows = []
    for i in range(14):
        d = str(TODAY - timedelta(days=i + 1))
        recent = i < eda.RECENT_DAYS
        rows += [{"ad_id": "big", "ad_name": "big", "campaign_id": "c", "report_date": d, "ad_spend": 1000,
                  "impressions": 1000, "link_clicks": 10 if recent else 20, LPV: 0},
                 {"ad_id": "small", "ad_name": "small", "campaign_id": "c", "report_date": d, "ad_spend": 10,
                  "impressions": 1000, "link_clicks": 5 if recent else 20, LPV: 0}]
    out, n = eda.fatigue(rows, [], TODAY)
    assert n == 2 and out[0]["ad_id"] == "big" and out[0]["ctr_change_pct"] == -50.0


def test_run_survives_a_failed_source_and_registers_tables():
    async def call(name, args):
        if name == "meta_insights_live" and args["level"] == "account":
            return {"success": True, "data": [_hour(h, 100) for h in range(11)]}, False
        if name == "meta_insights_live":
            return {"success": True, "data": []}, False
        if "ssot_campaign_orders" in args["measures"]:
            return {"error": "Cube down"}, True
        if args.get("granularity") == "hour":
            return {"rows": [r for i in range(1, 8) for r in _wh_hours(TODAY - timedelta(days=i), 100)]}, False
        if args.get("granularity") == "day" and not args["dimensions"]:
            return {"rows": [{"report_date": str(TODAY - timedelta(days=i)), "ad_spend": 2400, "impressions": 2400,
                              "link_clicks": 48, LPV: 24} for i in range(1, 8)]}, False
        return {"rows": []}, False
    pack = asyncio.run(eda.run(call, 20, "act_1", TODAY))
    names = [p["query_id"].rsplit("_", 1)[0] for p in pack["parts"]]
    assert names == ["eda_where_today_sits"] and set(pack["unavailable"]) == {"outcomes", "ad_outcomes"}
    sits = pack["parts"][0]
    assert sits["rows"][0]["today_so_far"] == 1000.0 and sits["rows"][0]["percentile_vs_56d"] == 1.0
    assert set(sits["provenance"]["metric_ids"]) <= set(sits["columns"])


def test_bot_eda_is_a_bot_tool_and_a_skip_is_recorded():
    assert eda.TOOL in store.READ_TOOLS
    assert eda.TOOL in {d["name"] for d in tool_defs([], (eda.TOOL,))} and eda.TOOL not in {d["name"] for d in tool_defs([])}
    bot = {"tools": [eda.TOOL]}
    assert worker.skipped_eda(bot, {"trigger_kind": "cron"}, [{"tool": "meta_insights_live", "outcome": "completed"}])
    assert not worker.skipped_eda(bot, {"trigger_kind": "cron"}, [{"tool": eda.TOOL, "outcome": "completed"}])
    assert not worker.skipped_eda(bot, {"trigger_kind": "chat"}, [])
    assert not worker.skipped_eda(bot, {"trigger_kind": "manual", "query": "what did Sale spend?"}, [])
    assert worker.skipped_eda(bot, {"trigger_kind": "manual", "query": " "}, [])


def test_model_view_of_a_full_pack_fits_the_tool_result_cap():
    from seleric_swarm.agent.mcp_toolset import MAX_RESULT_CHARS, compact
    # The real tables' shapes (rows, numeric columns) at their caps, with long names and 9-digit values.
    shapes = [(6, 12), (10, 3), (5, 6), (eda.TOP_K, 21), (eda.TOP_K, 8), (eda.TOP_K, 8), (eda.TOP_K, 3)]
    parts = []
    for n, (rows, width) in enumerate(shapes):
        cols = ["id", "name", *[f"metric_number_{i}" for i in range(width)]]
        row = {"id": "120251449602820783", "name": "TH-494-BONDLINEHOODIE-17NOV-ADV+-UGC", **dict.fromkeys(cols[2:], 123456.7891)}
        parts.append(eda._table(f"t{n}", cols, cols[2:], [row] * rows, TODAY, TODAY, "n" * 300, "s"))
    body = compact(eda.for_model({"note": "x" * 300, "parts": parts, "unavailable": {}}))
    assert len(body) < MAX_RESULT_CHARS and "truncated" not in body
