"""A small live tree shaped like the SnugSuit launch (one CBO campaign, one ad set, one ad) and a rule context."""

from __future__ import annotations

from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

from seleric_swarm.services.live_monitor import config as C
from seleric_swarm.services.live_monitor import guardrails as G
from seleric_swarm.services.live_monitor.core.context import Band, BaselineBook, RuleContext
from seleric_swarm.services.live_monitor.core.model import ADDITIVE, Entity, Snapshot, derive

TZ = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 11, 20, 15, 0, tzinfo=TZ)  # a Friday, 15:00 IST
ACCOUNT, CAMPAIGN, ADSET, AD = "act_1685189008684458", "120251540232620783", "120251540305160783", "120251540306860783"


@lru_cache(maxsize=1)
def cfg() -> C.LiveMonitorConfig:
    return C.validate(C.defaults())


def config_with(**changes: Any) -> C.LiveMonitorConfig:
    return C.validate({**C.defaults(), **changes})


def m(**kw: float) -> dict[str, float | None]:
    out: dict[str, float | None] = {k: float(kw.get(k, 0.0)) for k in ADDITIVE}
    out = derive(out)
    if "frequency" in kw:
        out["frequency"] = kw["frequency"]
    return out


def world(*, campaign: dict | None = None, adset: dict | None = None, ad: dict | None = None,
          metrics: dict[str, dict[str, dict]] | None = None, hourly: dict | None = None) -> Snapshot:
    """``metrics`` = {level: {window: m(...)}}; every other window is zero."""
    age = NOW - timedelta(days=10)
    c = Entity("meta", "th-meta", "campaign", CAMPAIGN, "TH-477-SNUGSUIT-PROSP-IN", parent_id=ACCOUNT, campaign_id=CAMPAIGN,
               status="ACTIVE", effective_status="ACTIVE", created_at=age,
               attrs={"daily_budget": 100000, "labels": ["TH-477-SNUGSUIT"], "budget_type": "cbo"})
    s = Entity("meta", "th-meta", "adset", ADSET, "TH-477-SNUGSUIT-BROAD", parent_id=CAMPAIGN, campaign_id=CAMPAIGN, adset_id=ADSET,
               status="ACTIVE", effective_status="ACTIVE", created_at=age,
               attrs={"daily_budget": None, "learning_stage_info": {"status": "SUCCESS"},
                      "promoted_object": {"pixel_id": "1533635213935845", "custom_event_type": "PURCHASE"},
                      "attribution_spec": [{"event_type": "CLICK_THROUGH", "window_days": 7}, {"event_type": "VIEW_THROUGH", "window_days": 1}],
                      "existing_customer_budget_percentage": None,
                      "targeting": {"publisher_platforms": ["facebook", "instagram"], "instagram_positions": ["stream", "story", "reels"]}})
    a = Entity("meta", "th-meta", "ad", AD, "TH-477-SNUGSUIT-IMG1", parent_id=ADSET, campaign_id=CAMPAIGN, adset_id=ADSET,
               status="ACTIVE", effective_status="ACTIVE", created_at=age,
               attrs={"is_link_creative": True, "has_asset_feed": False, "link": "https://tiltingheads.com/products/snugsuit",
                      "display_link": "tiltingheads.com", "headline": "SnugSuit", "image_hash": "abc", "page_id": "436081472919628",
                      "issues": [], "review_feedback": {}})
    acc = Entity("meta", "th-meta", "account", ACCOUNT, "TH", status="ACTIVE", effective_status="ACTIVE")
    for e, extra in ((c, campaign), (s, adset), (a, ad)):
        for k, v in (extra or {}).items():
            if k == "attrs":
                e.attrs.update(v)
            else:
                setattr(e, k, v)
    entities = [acc, c, s, a]
    for e in entities:
        for w in ("today", "3d", "7d", "prev7d"):
            e.metrics[w] = ((metrics or {}).get(e.level) or {}).get(w) or m()
    return Snapshot("th-meta", "meta", NOW, entities, hourly or {})


def ctx(snap: Snapshot, *, config: C.LiveMonitorConfig | None = None, rows: dict | None = None,
        tracking: dict | None = None, now: datetime = NOW, curves: dict | None = None) -> RuleContext:
    config = config or cfg()
    book = BaselineBook(rows=dict(rows or {}), curves=dict(curves or {}), min_days=config.baselines.min_days)
    for level, metrics in config.baselines.defaults.items():
        book.defaults[level] = {k: Band(b.p10, b.p50, b.p90, b.mean, 0, f"default ({level})") for k, b in metrics.items()}
    return RuleContext(snap, book, G.effective(config.guardrails, None), config.tenants[0], config.stats, now, tracking or {})
