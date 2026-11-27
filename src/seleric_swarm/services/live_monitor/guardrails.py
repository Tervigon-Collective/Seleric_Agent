"""Finance guardrails, recomputed nightly, and the pixel-vs-store tracking reference.

Break-even ROAS on Meta's scale: finance's break-even ROAS is net sales / contribution before ads; Meta reports
purchase value (incl. tax, before returns), so the line moves by Meta AOV / net AOV:
    be_roas_platform = be_roas_net * meta_aov / net_aov
Allowable CPA = contribution per order = net AOV * contribution margin.
"""

from __future__ import annotations

import statistics
from datetime import date, timedelta
from typing import Any

from seleric_swarm.services.live_monitor.core.context import Guardrails


def _value(resp: dict[str, Any], measure: str) -> float | None:
    rows = list(resp.get("rows") or [])
    for part in resp.get("parts") or []:
        rows.extend(part.get("rows") or [])
    for r in rows:
        if r.get(measure) not in (None, ""):
            try:
                return float(r[measure])
            except (TypeError, ValueError):
                return None
    return None


def compute(finance: dict[str, Any], meta_aov: float | None, target_multiple: float, computed_at: str) -> dict[str, Any] | None:
    """Pure: guardrail values from a metrics_query response (be_roas, contribution_margin_pct, net_aov)."""
    be_net, cm, net_aov = _value(finance, "be_roas"), _value(finance, "contribution_margin_pct"), _value(finance, "net_aov")
    if not (be_net and cm and net_aov and meta_aov) or be_net <= 0 or cm <= 0 or net_aov <= 0:
        return None
    factor = meta_aov / net_aov
    be_platform = be_net * factor
    return {
        "be_roas_net": round(be_net, 3), "contribution_margin": round(cm, 4), "net_aov": round(net_aov, 2),
        "meta_aov": round(meta_aov, 2), "be_roas_platform": round(be_platform, 3),
        "target_roas": round(be_platform * target_multiple, 3), "allowable_cpa": round(net_aov * cm, 2),
        "working": (f"break-even {be_net:.2f} (net sales) x Meta AOV {meta_aov:,.0f} / net AOV {net_aov:,.0f} = {be_platform:.2f}; "
                    f"allowable CPA = net AOV {net_aov:,.0f} x margin {cm:.1%} = {net_aov * cm:,.0f}"),
        "computed_at": computed_at,
    }


def effective(cfg: Any, stored: dict[str, Any] | None) -> Guardrails:
    """Config mode decides: manual values, or the nightly computation (manual values until the first one lands)."""
    overrides = {o.campaign_id: {k: v for k, v in o.model_dump().items() if k != "campaign_id" and v is not None}
                 for o in cfg.campaign_overrides}
    m = cfg.manual
    if cfg.mode == "manual" or not stored:
        return Guardrails(m.be_roas_platform, m.target_roas, m.allowable_cpa,
                          working="set by hand" if cfg.mode == "manual" else "default until the first nightly computation",
                          source="manual" if cfg.mode == "manual" else "default", campaign_overrides=overrides)
    return Guardrails(
        be_roas_platform=float(stored["be_roas_platform"]),
        target_roas=float(stored["be_roas_platform"]) * cfg.target_roas_multiple,
        allowable_cpa=float(stored["allowable_cpa"]), be_roas_net=stored.get("be_roas_net"),
        contribution_margin=stored.get("contribution_margin"), net_aov=stored.get("net_aov"), meta_aov=stored.get("meta_aov"),
        working=str(stored.get("working") or ""), source="auto", computed_at=stored.get("computed_at"),
        campaign_overrides=overrides,
    )


def finance_query(brand_id: int, as_of: date) -> dict[str, Any]:
    return {
        "measures": ["be_roas", "contribution_margin_pct", "net_aov"],
        "time_range": {"start": (as_of - timedelta(days=30)).isoformat(), "end": (as_of - timedelta(days=1)).isoformat()},
        "filters": [{"dimension": "brand_id", "operator": "equals", "values": [str(brand_id)]}],
    }


def store_orders_query(brand_id: int, as_of: date, days: int) -> dict[str, Any]:
    return {
        "measures": ["ssot_campaign_orders"],
        "time_range": {"start": (as_of - timedelta(days=days)).isoformat(), "end": (as_of - timedelta(days=1)).isoformat()},
        "granularity": "day",
        "filters": [{"dimension": "brand_id", "operator": "equals", "values": [str(brand_id)]},
                    {"dimension": "channel", "operator": "equals", "values": ["meta"]}],
    }


def tracking_reference(pixel: dict[str, dict[str, float]], store_resp: dict[str, Any]) -> dict[str, Any]:
    """Daily pixel purchases vs store orders the warehouse attributes to Meta, and the usual ratio between them."""
    store: dict[str, float] = {}
    for r in store_resp.get("rows") or []:
        d = str(r.get("report_date.day") or r.get("report_date") or "")[:10]
        if d and r.get("ssot_campaign_orders") not in (None, ""):
            store[d] = store.get(d, 0.0) + float(r["ssot_campaign_orders"])
    days = []
    for d in sorted(set(pixel) & set(store)):
        days.append({"date": d, "pixel_purchases": pixel[d]["purchases"], "pixel_atc": pixel[d]["atc"], "store_orders": store[d]})
    ratios = [x["pixel_purchases"] / x["store_orders"] for x in days if x["store_orders"] >= 5]
    return {"days": days, "median_ratio": statistics.median(ratios) if len(ratios) >= 5 else None}
