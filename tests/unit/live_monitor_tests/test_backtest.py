from __future__ import annotations

from datetime import date, timedelta

from seleric_swarm.services.live_monitor.backtest import day_snapshot

from .helpers import AD, ADSET, CAMPAIGN, TZ, cfg, world


def row(d: date, ad: str, adset: str, campaign: str, spend: float, purchases: float = 0) -> dict:
    return {"date": d.isoformat(), "ad_id": ad, "adset_id": adset, "campaign_id": campaign, "spend": spend,
            "impressions": spend * 3, "link_clicks": spend / 50, "lpv": 0, "purchases": purchases,
            "purchase_value": purchases * 2600, "atc": 0, "ic": 0}


def test_day_snapshot_rebuilds_the_tree_and_windows():
    d = date(2026, 11, 10)
    by_day = {d - timedelta(days=k): [row(d - timedelta(days=k), AD, ADSET, CAMPAIGN, 100, 1)] for k in range(10)}
    by_day[d].append(row(d, "gone-ad", "gone-adset", "gone-campaign", 500))
    snap = day_snapshot(cfg().tenants[0], world(), by_day, d, TZ)
    assert {e.id for e in snap.entities} >= {CAMPAIGN, ADSET, AD, "gone-ad", "gone-adset", "gone-campaign"}
    assert snap.get(CAMPAIGN).metric("3d", "spend") == 300 and snap.get(CAMPAIGN).metric("prev7d", "spend") == 700
    assert snap.account().metric("today", "spend") == 600
    gone = snap.get("gone-adset")
    assert gone.attrs["learning_stage_info"]["status"] == "LEARNING" and gone.created_at.date() == d  # first seen today
    assert snap.get("gone-ad").campaign_id == "gone-campaign" and snap.deliverable(snap.get("gone-campaign"))
    assert snap.get(CAMPAIGN).attrs["daily_budget"] == 100000  # today's attributes carried over
