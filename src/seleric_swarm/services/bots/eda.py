"""D-244 B23-B28: the bot's exploratory data analysis, computed in code on every run; the bot reads it and decides (B24).

History comes from the warehouse (Cube via MCP, B26), today from the live Meta API, and outcomes (orders, sales, CPA,
ROAS) from our own Shopify attribution, ``ssot_ad_pnl`` (B28), never Meta-reported purchases. Quantiles reuse
live_monitor's ``quantiles`` so the bot and the ``live.meta.*`` alerts read the same p10/p50/p90.
Every table is query-result shaped, so run_python and grounding can use it like any other query.
"""

from __future__ import annotations

import asyncio
import hashlib
import statistics
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import date, timedelta
from typing import Any

from seleric_swarm.agent.live_rows import HOUR_FIELD
from seleric_swarm.services.live_monitor.adapters.meta import normalise_insight
from seleric_swarm.services.live_monitor.core.stats import prob_zero, quantiles

TOOL = "bot_eda"
DESCRIPTION = (
    "Bot runs only. The run's exploratory data analysis, computed in code: where today so far sits against the last "
    "56 days at the same hour (and the same weekday), the 7-day trend, campaigns that drive the gap to expected, ads "
    "under the top movers, the funnel to our own Shopify-attributed orders, ad fatigue, concentration, and recent "
    "campaign edits. Call it first in a standing run; its tables carry query_ids for run_python. It gives ranges, not "
    "verdicts: you decide what matters.")
SCHEMA = {
    "type": "object",
    "properties": {
        "brand_id": {"type": "integer", "description": "Brand the ad account belongs to (warehouse scope)."},
        "account_id": {"type": "string", "description": "Meta ad account id, act_..."},
    },
    "required": ["brand_id", "account_id"],
    "additionalProperties": False,
}
HISTORY_DAYS, CAMPAIGN_DAYS, FATIGUE_DAYS, RECENT_DAYS = 56, 28, 14, 3
TOP_K, DRILL_K, CONCENTRATION_K = 8, 3, 5
COVERAGE = 0.02  # an hourly day must add up to its daily spend within 2 %
# Meta-reported landing views: live today has only Meta's count, and landing_page_views is our own sessions (D-13r).
DELIVERY = ("ad_spend", "impressions", "link_clicks", "platform_reported_landing_page_views")
LPV = DELIVERY[3]
OUTCOME = ("ssot_campaign_ad_spend", "ssot_campaign_orders", "ssot_campaign_gross_sales")
# Live insight fields -> the warehouse metric ids above (normalise_insight names on the left).
LIVE_AS = {"spend": "ad_spend", "impressions": "impressions", "link_clicks": "link_clicks", "lpv": LPV}
LIVE_FIELDS = ["spend", "impressions", "inline_link_clicks", "actions"]

Call = Callable[[str, dict[str, Any]], Awaitable[tuple[Any, bool]]]


def _f(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _div(a: float, b: float) -> float | None:
    return a / b if b else None


def _r(v: float | None, nd: int = 4) -> float | None:
    return None if v is None else round(v, nd)


def percentile(value: float, history: list[float]) -> float | None:
    """Share of history at or below ``value`` (0-1); None without history."""
    xs = [x for x in history if x is not None]
    return round(sum(x <= value for x in xs) / len(xs), 3) if xs else None


def _band(values: list[float | None]) -> dict[str, float | None]:
    q = quantiles([v for v in values if v is not None])
    return dict(zip(("p10", "p50", "p90"), (_r(x) for x in q), strict=True)) if q else dict.fromkeys(("p10", "p50", "p90"))


def _table(name: str, columns: list[str], measures: list[str], rows: list[dict[str, Any]], start: date, end: date,
           note: str, source: str, total: int | None = None) -> dict[str, Any]:
    qid = f"eda_{name}_{hashlib.sha1(f'{start}{end}{len(rows)}'.encode()).hexdigest()[:8]}"
    more = f" Showing the top {len(rows)} of {total}; ask run_python over the source query for the rest." if total and total > len(rows) else ""
    return {"query_id": qid, "columns": columns, "rows": rows, "row_count": len(rows),
            "provenance": {"query_id": qid, "metric_ids": measures, "currency": "INR",
                           "time_range": {"start": start.isoformat(), "end": end.isoformat()},
                           "freshness": {"source": source}},
            "note": note + more}


def _rows(payload: Any) -> list[dict[str, Any]]:
    return [r for r in (payload.get("rows") or payload.get("data") or []) if isinstance(r, dict)] if isinstance(payload, dict) else []


# --- today (live) ---------------------------------------------------------------------------------------------------

def today_by_hour(live_hourly: list[dict[str, Any]]) -> tuple[int, dict[int, dict[str, float]]]:
    """Live account hours -> (hours complete, {hour: metrics}). The latest hour is still running, so it is not complete."""
    by_hour = {int(str(r.get(HOUR_FIELD))[:2]): {LIVE_AS[k]: v for k, v in normalise_insight(r).items() if k in LIVE_AS}
               for r in live_hourly if str(r.get(HOUR_FIELD) or "")[:2].isdigit()}
    return (max(by_hour) if by_hour else 0), by_hour


# --- history (warehouse) --------------------------------------------------------------------------------------------

def daily_totals(daily_rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    return {str(r.get("report_date") or r.get("report_date.day") or "")[:10]: {m: _f(r.get(m)) for m in DELIVERY} for r in daily_rows}


def history_by_day(hourly_rows: list[dict[str, Any]], hours_done: int,
                   daily: dict[str, dict[str, float]]) -> tuple[dict[str, dict[str, float]], list[str]]:
    """Warehouse hourly rows -> ({day: totals before ``hours_done``}, days left out). A day whose hours do not add up
    to its daily spend (within COVERAGE) has holes in the hourly data and is left out (2026-11-23: Nov 15-17 hourly
    held 0-17 % of the day), so a hole never reads as a slow day."""
    by_now: dict[str, dict[str, float]] = defaultdict(lambda: dict.fromkeys(DELIVERY, 0.0))
    full: dict[str, dict[str, float]] = defaultdict(lambda: dict.fromkeys(DELIVERY, 0.0))
    for r in hourly_rows:
        stamp = str(r.get("report_hour") or r.get("report_hour.hour") or "")
        if len(stamp) < 13:
            continue
        day, hour = stamp[:10], int(stamp[11:13])
        for m in DELIVERY:
            full[day][m] += _f(r.get(m))
            if hour < hours_done:
                by_now[day][m] += _f(r.get(m))
    dropped = sorted(d for d, t in daily.items() if t["ad_spend"] and abs(full.get(d, {}).get("ad_spend", 0.0) / t["ad_spend"] - 1) > COVERAGE)
    return {d: v for d, v in by_now.items() if d in daily and d not in dropped}, dropped


def _ratios(t: dict[str, float]) -> dict[str, float | None]:
    return {"ctr_link": _div(t["link_clicks"], t["impressions"]), "lpv_per_click": _div(t[LPV], t["link_clicks"])}


def where_today_sits(today_now: dict[str, float], by_now: dict[str, dict[str, float]], full: dict[str, dict[str, float]],
                     today: date, hours_done: int) -> list[dict[str, Any]]:
    days = sorted(d for d in by_now if d < today.isoformat() and d in full)
    weekday = [d for d in days if date.fromisoformat(d).weekday() == today.weekday()]
    yesterday = (today - timedelta(days=1)).isoformat()
    rows = []
    hist_now = {d: by_now[d] for d in days}
    for m in (*DELIVERY, "ctr_link", "lpv_per_click"):
        def val(t: dict[str, float]) -> float | None:
            return t.get(m) if m in DELIVERY else _ratios(t)[m]  # noqa: B023 - used inside this iteration only
        history = [val(hist_now[d]) for d in days]
        now = val(today_now)
        shares = [_div(hist_now[d][m], full[d][m]) for d in days] if m in DELIVERY else []
        share = statistics.median([s for s in shares if s is not None]) if any(s is not None for s in shares) else None
        rows.append({
            "metric": m, "today_so_far": _r(now, 4), **_band(history),
            "percentile_vs_56d": percentile(now, history) if now is not None else None,
            "same_weekday_p50": _band([val(hist_now[d]) for d in weekday])["p50"], "same_weekday_days": len(weekday),
            "yesterday_same_hours": _r(val(hist_now[yesterday]) if yesterday in hist_now else None),
            "usual_share_of_day_by_now": _r(share, 3),
            "projected_full_day": _r(now / share, 2) if now is not None and share and m in DELIVERY else None,
            "history_days": len(days), "hours_complete": hours_done,
        })
    return rows


def trend(full: dict[str, dict[str, float]], outcomes_by_day: dict[str, dict[str, float]], today: date) -> list[dict[str, Any]]:
    """Last 7 complete days against the 7 before, on delivery (warehouse) and attributed outcomes (ssot)."""
    last = [(today - timedelta(days=i)).isoformat() for i in range(1, 8)]
    prior = [(today - timedelta(days=i)).isoformat() for i in range(8, 15)]

    def tot(days: list[str]) -> dict[str, float]:
        t = {m: sum(full.get(d, {}).get(m, 0.0) for d in days) for m in DELIVERY}
        t |= {m: sum(outcomes_by_day.get(d, {}).get(m, 0.0) for d in days) for m in OUTCOME}
        t["ctr_link"] = _div(t["link_clicks"], t["impressions"])
        t["cpa"] = _div(t["ssot_campaign_ad_spend"], t["ssot_campaign_orders"])
        t["roas"] = _div(t["ssot_campaign_gross_sales"], t["ssot_campaign_ad_spend"])
        return t
    a, b = tot(last), tot(prior)
    return [{"metric": m, "last_7d": _r(a[m], 4), "prior_7d": _r(b[m], 4),
             "change_pct": _r((a[m] / b[m] - 1) * 100, 1) if a[m] is not None and b[m] else None} for m in a]


def drivers(live_campaigns: list[dict[str, Any]], camp_daily: list[dict[str, Any]], camp_outcomes: list[dict[str, Any]],
            share_by_now: float | None, today: date) -> tuple[list[dict[str, Any]], int, dict[str, float]]:
    """Campaigns ranked by how far today's spend so far is from what their own history expects by now."""
    hist: dict[str, dict[str, dict[str, float]]] = defaultdict(dict)
    names: dict[str, str] = {}
    for r in camp_daily:
        cid = str(r.get("campaign_id") or "")
        if cid:
            names.setdefault(cid, str(r.get("campaign_name") or ""))
            hist[cid][str(r.get("report_date") or r.get("report_date.day") or "")[:10]] = {m: _f(r.get(m)) for m in DELIVERY}
    out_today: dict[str, dict[str, float]] = defaultdict(lambda: dict.fromkeys(OUTCOME, 0.0))
    out_hist: dict[str, dict[str, float]] = defaultdict(lambda: dict.fromkeys(OUTCOME, 0.0))
    for r in camp_outcomes:
        cid, day = str(r.get("campaign_id") or ""), str(r.get("report_date") or r.get("report_date.day") or "")[:10]
        for m in OUTCOME:
            (out_today if day == today.isoformat() else out_hist)[cid][m] += _f(r.get(m))
    live = {str(r.get("campaign_id")): r for r in live_campaigns if r.get("campaign_id")}
    for cid, r in live.items():
        names[cid] = str(r.get("campaign_name") or names.get(cid, ""))
    spend_today = {cid: normalise_insight(r)["spend"] for cid, r in live.items()}
    total_today = sum(spend_today.values())
    total_hist = sum(v["ad_spend"] for days in hist.values() for v in days.values())
    rows = []
    for cid in set(live) | set(hist):
        days = hist.get(cid, {})
        active = [v["ad_spend"] for v in days.values() if v["ad_spend"] > 0]
        now = normalise_insight(live[cid]) if cid in live else dict.fromkeys(LIVE_AS, 0.0)
        if not now["spend"] and not days.get((today - timedelta(days=1)).isoformat(), {}).get("ad_spend"):
            continue  # not spending today and not yesterday: ended, not a mover
        p = _band(active)
        expected = p["p50"] * share_by_now if p["p50"] is not None and share_by_now else None
        pooled = {m: sum(v[m] for v in days.values()) for m in DELIVERY}
        ot, oh = out_today[cid], out_hist[cid]
        cpa_28 = _div(oh["ssot_campaign_ad_spend"], oh["ssot_campaign_orders"])
        rows.append({
            "campaign_id": cid, "campaign_name": names.get(cid, ""),
            "spend_today": _r(now["spend"], 2), "expected_by_now": _r(expected, 2),
            "gap_to_expected": _r(now["spend"] - expected, 2) if expected is not None else None,
            "spend_vs_expected_pct": _r((now["spend"] / expected - 1) * 100, 1) if expected else None,
            "percentile_vs_active_days": percentile(now["spend"], [a * share_by_now for a in active]) if share_by_now and active else None,
            "active_days_28d": len(active),
            "ctr_today": _r(_div(now["link_clicks"], now["impressions"])), "ctr_28d": _r(_div(pooled["link_clicks"], pooled["impressions"])),
            "orders_today": ot["ssot_campaign_orders"], "orders_28d": oh["ssot_campaign_orders"],
            "cpa_today": _r(_div(ot["ssot_campaign_ad_spend"], ot["ssot_campaign_orders"]), 2),
            "cpa_28d": _r(cpa_28, 2),
            "expected_orders_by_now": _r(now["spend"] / cpa_28, 2) if cpa_28 else None,
            # How often a campaign converting at its 28-day rate gets no order on today's spend (Poisson, as live_monitor).
            "p_zero_orders_at_28d_rate": _r(prob_zero(1 / cpa_28, now["spend"]), 3) if cpa_28 and not ot["ssot_campaign_orders"] else None,
            "roas_today": _r(_div(ot["ssot_campaign_gross_sales"], ot["ssot_campaign_ad_spend"]), 3),
            "roas_28d": _r(_div(oh["ssot_campaign_gross_sales"], oh["ssot_campaign_ad_spend"]), 3),
            "spend_share_today": _r(_div(now["spend"], total_today), 3),
            "spend_share_28d": _r(_div(pooled["ad_spend"], total_hist), 3),
        })
    rows.sort(key=lambda r: abs(r["gap_to_expected"] if r["gap_to_expected"] is not None else r["spend_today"]), reverse=True)
    shares = sorted((r["spend_share_today"] or 0.0 for r in rows), reverse=True)
    shares_28 = sorted((r["spend_share_28d"] or 0.0 for r in rows), reverse=True)
    concentration = {"top_share_today": round(sum(shares[:CONCENTRATION_K]), 3), "top_share_28d": round(sum(shares_28[:CONCENTRATION_K]), 3)}
    return rows[:TOP_K], len(rows), concentration


def ads_under(live_ads: list[dict[str, Any]], ad_daily: list[dict[str, Any]], share_by_now: float | None) -> tuple[list[dict[str, Any]], int]:
    hist: dict[str, list[dict[str, float]]] = defaultdict(list)
    for r in ad_daily:
        hist[str(r.get("ad_id") or "")].append({m: _f(r.get(m)) for m in DELIVERY})
    rows = []
    for r in live_ads:
        aid = str(r.get("ad_id") or "")
        now = normalise_insight(r)
        days = hist.get(aid, [])
        active = [d["ad_spend"] for d in days if d["ad_spend"] > 0]
        p50 = _band(active)["p50"]
        expected = p50 * share_by_now if p50 is not None and share_by_now else None
        imp, clk = sum(d["impressions"] for d in days), sum(d["link_clicks"] for d in days)
        rows.append({"ad_id": aid, "ad_name": str(r.get("ad_name") or ""), "adset_name": str(r.get("adset_name") or ""),
                     "campaign_id": str(r.get("campaign_id") or ""), "spend_today": _r(now["spend"], 2),
                     "expected_by_now": _r(expected, 2),
                     "gap_to_expected": _r(now["spend"] - expected, 2) if expected is not None else None,
                     "ctr_today": _r(_div(now["link_clicks"], now["impressions"])), "ctr_14d": _r(_div(clk, imp)),
                     "active_days_14d": len(active)})
    rows.sort(key=lambda r: abs(r["gap_to_expected"] if r["gap_to_expected"] is not None else r["spend_today"]), reverse=True)
    return rows[:TOP_K], len(rows)


def funnel(full: dict[str, dict[str, float]], outcomes_by_day: dict[str, dict[str, float]], today_now: dict[str, float],
           today_outcome: dict[str, float], today: date) -> list[dict[str, Any]]:
    """Impression -> link click -> landing view -> attributed order, and CPA/ROAS, today against 28 daily values."""
    days = [(today - timedelta(days=i)).isoformat() for i in range(1, CAMPAIGN_DAYS + 1)]

    def steps(d: dict[str, float], o: dict[str, float]) -> dict[str, float | None]:
        return {**_ratios(d), "orders_per_lpv": _div(o.get("ssot_campaign_orders", 0.0), d.get(LPV, 0.0)),
                "cpa": _div(o.get("ssot_campaign_ad_spend", 0.0), o.get("ssot_campaign_orders", 0.0)),
                "roas": _div(o.get("ssot_campaign_gross_sales", 0.0), o.get("ssot_campaign_ad_spend", 0.0))}
    hist = [steps(full.get(d, dict.fromkeys(DELIVERY, 0.0)), outcomes_by_day.get(d, {})) for d in days]
    now = steps(today_now, today_outcome)
    rows = []
    for m, v in now.items():
        series = [h[m] for h in hist]
        rows.append({"step": m, "today": _r(v), **_band(series),
                     "percentile_vs_28d": percentile(v, [s for s in series if s is not None]) if v is not None else None,
                     "orders_today": today_outcome.get("ssot_campaign_orders", 0.0)})
    return rows


def fatigue(ad_daily: list[dict[str, Any]], ad_outcomes: list[dict[str, Any]], today: date) -> tuple[list[dict[str, Any]], int]:
    """Ads that ran most of the last 14 days: CTR of the last 3 days against the days before, and orders per ₹1k spend."""
    recent_from = (today - timedelta(days=RECENT_DAYS)).isoformat()
    by_ad: dict[str, dict[str, Any]] = {}
    for r in ad_daily:
        aid = str(r.get("ad_id") or "")
        a = by_ad.setdefault(aid, {"name": str(r.get("ad_name") or ""), "campaign_id": str(r.get("campaign_id") or ""),
                                   "days": 0, "recent": dict.fromkeys(DELIVERY, 0.0), "before": dict.fromkeys(DELIVERY, 0.0),
                                   "orders_recent": 0.0, "orders_before": 0.0})
        a["days"] += _f(r.get("ad_spend")) > 0
        part = "recent" if str(r.get("report_date") or r.get("report_date.day") or "")[:10] >= recent_from else "before"
        for m in DELIVERY:
            a[part][m] += _f(r.get(m))
    for r in ad_outcomes:
        a = by_ad.get(str(r.get("ad_id") or ""))
        if a is not None:
            recent = str(r.get("report_date") or r.get("report_date.day") or "")[:10] >= recent_from
            a["orders_recent" if recent else "orders_before"] += _f(r.get("ssot_campaign_orders"))
    rows = []
    for aid, a in by_ad.items():
        if a["days"] < FATIGUE_DAYS // 2:
            continue
        ctr_r, ctr_b = _div(a["recent"]["link_clicks"], a["recent"]["impressions"]), _div(a["before"]["link_clicks"], a["before"]["impressions"])
        rows.append({"ad_id": aid, "ad_name": a["name"], "campaign_id": a["campaign_id"], "active_days_14d": a["days"],
                     "spend_14d": _r(a["recent"]["ad_spend"] + a["before"]["ad_spend"], 2),
                     "ctr_last_3d": _r(ctr_r), "ctr_before": _r(ctr_b),
                     "ctr_change_pct": _r((ctr_r / ctr_b - 1) * 100, 1) if ctr_r is not None and ctr_b else None,
                     "orders_per_1k_last_3d": _r(_div(a["orders_recent"] * 1000, a["recent"]["ad_spend"]), 3),
                     "orders_per_1k_before": _r(_div(a["orders_before"] * 1000, a["before"]["ad_spend"]), 3)})
    # Spend-weighted CTR fall first: a big ad losing CTR matters more than a small one.
    rows.sort(key=lambda r: (r["ctr_change_pct"] or 0.0) * (r["spend_14d"] or 0.0))
    return rows[:TOP_K], len(rows)


# --- fetch + assemble -----------------------------------------------------------------------------------------------

async def run(call: Call, brand_id: int, account_id: str, today: date) -> dict[str, Any]:
    """All sources concurrently; a failed source leaves its tables out and is named in ``unavailable``."""
    y = today - timedelta(days=1)
    brand = {"dimension": "brand_id", "operator": "equals", "values": [str(brand_id)]}
    meta = {"dimension": "ad_platform", "operator": "equals", "values": ["meta"]}
    ssot = {"dimension": "channel", "operator": "equals", "values": ["meta"]}

    def mq(measures: tuple[str, ...], start: date, end: date, dims: list[str], gran: str, filt: dict[str, Any]) -> Awaitable[tuple[Any, bool]]:
        return call("metrics_query", {"measures": list(measures), "dimensions": dims, "granularity": gran, "filters": [brand, filt],
                                      "time_range": {"start": start.isoformat(), "end": end.isoformat()}})

    def live(level: str, fields: list[str], **extra: Any) -> Awaitable[tuple[Any, bool]]:
        return call("meta_insights_live", {"object_id": account_id, "account_id": account_id, "level": level,
                                           "date_preset": "today", "fields": fields, "limit": 500, **extra})
    c28, c14 = today - timedelta(days=CAMPAIGN_DAYS), today - timedelta(days=FATIGUE_DAYS)
    sources = {
        "live_hourly": live("account", LIVE_FIELDS, hourly=True, time_increment="1"),
        "live_campaigns": live("campaign", ["campaign_id", "campaign_name", *LIVE_FIELDS]),
        "hourly": mq(DELIVERY, today - timedelta(days=HISTORY_DAYS), y, [], "hour", meta),
        "daily": mq(DELIVERY, today - timedelta(days=HISTORY_DAYS), y, [], "day", meta),
        "campaigns": mq(DELIVERY, c28, y, ["campaign_id", "campaign_name"], "day", meta),
        "outcomes": mq(OUTCOME, c28, today, ["campaign_id"], "day", ssot),
        "ads": mq(DELIVERY, c14, y, ["ad_id", "ad_name", "campaign_id"], "day", meta),
        "ad_outcomes": mq(("ssot_campaign_orders",), c14, y, ["ad_id"], "day", ssot),
        "changes": call("metrics_query", {"measures": ["status_changes", "budget_changes", "bid_strategy_changes"],
                                          "dimensions": ["campaign_id", "campaign_name"], "filters": [brand, meta],
                                          "time_range": {"start": (y - timedelta(days=1)).isoformat(), "end": today.isoformat()}}),
    }
    got = dict(zip(sources, await asyncio.gather(*sources.values()), strict=True))
    failed = {k: str((p or {}).get("error") if isinstance(p, dict) else p)[:200] for k, (p, err) in got.items() if err}
    truncated = [k for k, (p, err) in got.items() if not err and isinstance(p, dict) and (p.get("paging") or {}).get("after")]
    data = {k: _rows(p) for k, (p, err) in got.items() if not err}
    tables: list[dict[str, Any]] = []
    wh, lv = "warehouse (Cube paid_media / ssot_ad_pnl)", "live Meta API today + warehouse history"

    hours_done, by_hour = today_by_hour(data.get("live_hourly", []))
    today_now = {m: sum(h[m] for hr, h in by_hour.items() if hr < hours_done) for m in DELIVERY}
    full = daily_totals(data.get("daily", []))
    by_now, dropped = history_by_day(data.get("hourly", []), hours_done, full)
    share = None
    if {"live_hourly", "hourly", "daily"} <= set(data) and hours_done:
        sits = where_today_sits(today_now, by_now, full, today, hours_done)
        share = next(r["usual_share_of_day_by_now"] for r in sits if r["metric"] == "ad_spend")
        tables.append(_table("where_today_sits", list(sits[0]), [k for k in sits[0] if k != "metric"], sits, today - timedelta(days=HISTORY_DAYS), today,
                             f"Today 00:00-{hours_done:02d}:00 (complete hours, account time) against each of the last "
                             f"{HISTORY_DAYS} days over the same hours: p10/p50/p90, today's percentile, same weekday, "
                             "yesterday same hours. A value inside p10-p90 is normal for this hour."
                             + (f" Left out (hourly data incomplete in the warehouse): {', '.join(dropped)}." if dropped else ""), lv))
    by_day_out: dict[str, dict[str, float]] = defaultdict(lambda: dict.fromkeys(OUTCOME, 0.0))
    for r in data.get("outcomes", []):
        for m in OUTCOME:
            by_day_out[str(r.get("report_date") or r.get("report_date.day") or "")[:10]][m] += _f(r.get(m))
    if "daily" in data and "outcomes" in data:
        tr = trend(full, by_day_out, today)
        tables.append(_table("trend", list(tr[0]), ["last_7d", "prior_7d", "change_pct"], tr, today - timedelta(days=14), y,
                             "Last 7 complete days against the 7 before. ssot_campaign_* are our Shopify-attributed figures.", wh))
        if "live_hourly" in data and hours_done:
            fn = funnel(full, by_day_out, today_now, by_day_out.get(today.isoformat(), {}), today)
            tables.append(_table("funnel", list(fn[0]), [k for k in fn[0] if k != "step"], fn, c28, today,
                                 "Steps: link CTR, landing views per click (Meta delivery, today's complete hours), attributed "
                                 "orders per landing view, CPA and ROAS (our attribution; today's orders keep arriving for "
                                 "hours, so today's order steps read low). Ratios against 28 daily values.", lv))
    if {"live_campaigns", "campaigns", "outcomes"} <= set(data):
        dr, n, conc = drivers(data["live_campaigns"], data["campaigns"], data["outcomes"], share, today)
        if dr:
            tables.append(_table("drivers", list(dr[0]), [k for k in dr[0] if k not in ("campaign_id", "campaign_name")], dr, c28, today,
                                 f"Campaigns ranked by today's spend so far against their own median active day x the account's "
                                 f"usual share by this hour ({share}). Concentration: top {CONCENTRATION_K} campaigns hold "
                                 f"{conc['top_share_today']:.0%} of today's spend vs {conc['top_share_28d']:.0%} over 28 days. Orders are our "
                                 "attribution and keep arriving for hours; p_zero_orders_at_28d_rate is how often zero orders "
                                 "happens by chance at the campaign's own 28-day CPA (small = unusual).",
                                 lv, total=n))
            top = [r["campaign_id"] for r in dr[:DRILL_K]]
            ads_p, ads_err = await live("ad", ["ad_id", "ad_name", "adset_name", "campaign_id", *LIVE_FIELDS],
                                        filtering=[{"field": "campaign.id", "operator": "IN", "value": top}])
            if ads_err:
                failed["live_ads"] = str(ads_p)[:200]
            elif "ads" in data:
                au, n_ads = ads_under(_rows(ads_p), data["ads"], share)
                if au:
                    tables.append(_table("ads_under_top_movers", list(au[0]), [k for k in au[0] if k in ("spend_today", "expected_by_now", "gap_to_expected", "ctr_today", "ctr_14d", "active_days_14d")],
                                         au, c14, today, f"Ads in the top {DRILL_K} campaigns above, against their own 14 days.", lv, total=n_ads))
    if {"ads", "ad_outcomes"} <= set(data):
        fa, n = fatigue(data["ads"], data["ad_outcomes"], today)
        if fa:
            tables.append(_table("fatigue", list(fa[0]), [k for k in fa[0] if k not in ("ad_id", "ad_name", "campaign_id")], fa, c14, y,
                                 f"Ads active at least {FATIGUE_DAYS // 2} of the last {FATIGUE_DAYS} days, biggest spend-weighted "
                                 f"CTR fall first: last {RECENT_DAYS} days against the days before. Frequency is not in the warehouse.",
                                 wh, total=n))
    if "changes" in data:
        ch = [{"campaign_id": str(r.get("campaign_id") or ""), "campaign_name": str(r.get("campaign_name") or ""),
               **{m: _f(r.get(m)) for m in ("status_changes", "budget_changes", "bid_strategy_changes")}} for r in data["changes"]]
        ch = sorted((r for r in ch if r["status_changes"] + r["budget_changes"] + r["bid_strategy_changes"]),
                    key=lambda r: -(r["status_changes"] + r["budget_changes"] + r["bid_strategy_changes"]))
        if ch:
            tables.append(_table("changes", list(ch[0]), ["status_changes", "budget_changes", "bid_strategy_changes"], ch[:TOP_K],
                                 y - timedelta(days=1), today, "Campaign edits recorded since the day before yesterday.", wh, total=len(ch)))
    qid = f"eda_{hashlib.sha1(f'{brand_id}{account_id}{today}{hours_done}'.encode()).hexdigest()[:10]}"
    return {"query_id": qid, "parts": tables, "unavailable": failed, "truncated_sources": truncated,
            "note": ("EDA computed in code for this run. Each part has its own query_id for run_python. It gives ranges, "
                     "not verdicts: you decide what matters. A value inside its normal range for this hour is not a finding "
                     "on its own." + (f" Unavailable: {sorted(failed)}." if failed else ""))}


def for_model(pack: dict[str, Any]) -> dict[str, Any]:
    """What the model reads: each table as columns once + rows as lists, no provenance (evidence keeps the full
    tables). The full pack is ~18k characters and the tool-result cap is 14k, so the generic compaction kept only the
    first three tables (live 2026-11-23: the bot then called fatigue and concentration "normal" without seeing them)."""
    return {"note": pack["note"], "unavailable": pack.get("unavailable") or {},
            "parts": [{"query_id": p["query_id"], "note": p["note"], "columns": p["columns"],
                       "rows": [[r.get(c) for c in p["columns"]] for r in p["rows"]]} for p in pack["parts"]]}
