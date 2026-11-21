"""Replay the last N days of Meta insights and report findings per day per rule.

Uses each day's spenders as the live tree (historical effective_status is not available),
trailing baselines ending the day before, and evaluates at 18:00 local so under-pacing is
in scope. Compliance rules that need live Graph fields are skipped.
"""

from __future__ import annotations

import collections
import logging
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from seleric_swarm.services.live_monitor import baselines as B
from seleric_swarm.services.live_monitor import config as C
from seleric_swarm.services.live_monitor import guardrails as G
from seleric_swarm.services.live_monitor.adapters.meta import PLATFORM, MetaAdapter
from seleric_swarm.services.live_monitor.config import RuleSetting
from seleric_swarm.services.live_monitor.core.context import RuleContext
from seleric_swarm.services.live_monitor.core.model import (
    ADDITIVE,
    Entity,
    Snapshot,
    derive,
    sum_metrics,
)
from seleric_swarm.services.live_monitor.core.registry import ensure_loaded
from seleric_swarm.services.live_monitor.engine import evaluate
from seleric_swarm.services.live_monitor.mcp import McpClient
from seleric_swarm.services.live_monitor.store import book_from_rows

log = logging.getLogger(__name__)

# Live Graph fields; not reconstructable from daily insights.
SKIP_RULES = {
    # Compliance / live Graph fields we cannot rebuild from daily insights.
    "status_not_active", "ad_review_issue", "missing_campaign_label", "missing_ig_stream",
    "lifecycle_restricted", "attribution_mismatch", "missing_display_link_or_headline",
    "wrong_pixel_or_event", "landing_break", "delivery_stall", "learning_limited",
    # Today's budgets, not the budget that was live on that day — pacing would be fiction.
    "spend_pacing",
}

# Same inclusive offsets as MetaAdapter._range (start, end) relative to the as-of day.
_WINDOWS = {"today": (0, 0), "3d": (2, 0), "7d": (6, 0), "prev7d": (9, 3)}


def _empty() -> dict[str, float | None]:
    return derive({k: 0.0 for k in ADDITIVE})


def _rows_for(by_day: dict[date, list[dict[str, Any]]], day: date, start: int, end: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i in range(start, end - 1, -1):
        out.extend(by_day.get(day - timedelta(days=i), ()))
    return out


def _roll(rows: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, float | None]]]:
    groups: dict[str, dict[str, list[dict[str, float | None]]]] = {
        "ad": {}, "adset": {}, "campaign": {}, "account": {},
    }
    for r in rows:
        m = {k: float(r.get(k) or 0.0) for k in ADDITIVE}
        m["reach"] = float(r.get("reach") or 0.0)
        m["frequency"] = float(r.get("frequency") or 0.0)
        for level, key in (
            ("ad", r.get("ad_id")), ("adset", r.get("adset_id")),
            ("campaign", r.get("campaign_id")), ("account", ""),
        ):
            if key is not None:
                groups[level].setdefault(str(key), []).append(m)
    out: dict[str, dict[str, dict[str, float | None]]] = {}
    for level, by_id in groups.items():
        out[level] = {}
        for key, ms in by_id.items():
            agg = sum_metrics(ms)
            if level == "ad" and len(ms) == 1:
                agg["reach"], agg["frequency"] = ms[0].get("reach"), ms[0].get("frequency")
            out[level][key] = agg
    return out


def _clone(e: Entity, *, effective_status: str = "ACTIVE") -> Entity:
    return Entity(
        e.platform, e.tenant_id, e.level, e.id, e.name, parent_id=e.parent_id, campaign_id=e.campaign_id,
        adset_id=e.adset_id, status="ACTIVE", effective_status=effective_status, created_at=e.created_at,
        attrs=dict(e.attrs),
    )


def day_snapshot(
    tenant: Any,
    template: Snapshot,
    by_day: dict[date, list[dict[str, Any]]],
    day: date,
    tz: ZoneInfo,
) -> Snapshot:
    """Rebuild a deliverable tree for ``day`` from insights, carrying attrs from ``template`` when known."""
    today_rows = by_day.get(day, [])
    known = {e.id: e for e in template.entities}
    account = next((e for e in template.entities if e.level == "account"), None)
    entities: list[Entity] = [
        _clone(account) if account else Entity(
            PLATFORM, tenant.tenant_id, "account", tenant.account_id, tenant.name or tenant.account_id,
            status="ACTIVE", effective_status="ACTIVE",
        )
    ]
    live_a = {str(r["ad_id"]) for r in today_rows if float(r.get("spend") or 0) > 0}
    live_s = {str(r["adset_id"]) for r in today_rows if float(r.get("spend") or 0) > 0}
    live_c = {str(r["campaign_id"]) for r in today_rows if float(r.get("spend") or 0) > 0}
    born = datetime.combine(day, time(12, 0), tzinfo=tz)

    for cid in sorted(live_c):
        if cid in known and known[cid].level == "campaign":
            entities.append(_clone(known[cid]))
        else:
            entities.append(Entity(
                PLATFORM, tenant.tenant_id, "campaign", cid, cid, parent_id=tenant.account_id, campaign_id=cid,
                status="ACTIVE", effective_status="ACTIVE", created_at=born,
                attrs={"daily_budget": 100000, "budget_type": "cbo"},
            ))
    for sid in sorted(live_s):
        sample = next(r for r in today_rows if str(r["adset_id"]) == sid)
        if sid in known and known[sid].level == "adset":
            entities.append(_clone(known[sid]))
        else:
            entities.append(Entity(
                PLATFORM, tenant.tenant_id, "adset", sid, sid, parent_id=str(sample["campaign_id"]),
                campaign_id=str(sample["campaign_id"]), adset_id=sid,
                status="ACTIVE", effective_status="ACTIVE", created_at=born,
                attrs={"learning_stage_info": {"status": "LEARNING"}},
            ))
    for aid in sorted(live_a):
        sample = next(r for r in today_rows if str(r["ad_id"]) == aid)
        if aid in known and known[aid].level == "ad":
            entities.append(_clone(known[aid]))
        else:
            entities.append(Entity(
                PLATFORM, tenant.tenant_id, "ad", aid, aid, parent_id=str(sample["adset_id"]),
                campaign_id=str(sample["campaign_id"]), adset_id=str(sample["adset_id"]),
                status="ACTIVE", effective_status="ACTIVE", created_at=born, attrs={},
            ))

    empty = _empty()
    for window, (start, end) in _WINDOWS.items():
        rolled = _roll(_rows_for(by_day, day, start, end))
        for e in entities:
            key = "" if e.level == "account" else e.id
            e.metrics[window] = rolled[e.level].get(key, dict(empty))

    hourly: dict[str, dict[int, dict[str, float]]] = {}
    for e in entities:
        if e.level in ("campaign", "adset"):
            spend = float((e.metrics.get("today") or {}).get("spend") or 0)
            impr = float((e.metrics.get("today") or {}).get("impressions") or 0)
            hourly[e.id] = {17: {"spend": spend, "impressions": impr}}
    return Snapshot(tenant.tenant_id, PLATFORM, datetime.combine(day, time(18, 0), tzinfo=tz), entities, hourly)


def _history_by_day(history: list[dict[str, Any]]) -> dict[date, list[dict[str, Any]]]:
    out: dict[date, list[dict[str, Any]]] = collections.defaultdict(list)
    for r in history:
        out[date.fromisoformat(str(r["date"]))].append(r)
    return out


async def run(client: McpClient, *, days: int = 28, tenant_id: str | None = None) -> dict[str, Any]:
    ensure_loaded()
    cfg, _ = C.load()
    adapter = MetaAdapter(client)
    reports: list[dict[str, Any]] = []
    for t in cfg.tenants:
        if not t.enabled or (tenant_id and t.tenant_id != tenant_id):
            continue
        tz = ZoneInfo(t.timezone)
        today = datetime.now(tz).date()
        history = await adapter.daily_history(t, days + cfg.baselines.window_days)
        hourly = await adapter.hourly_profile(t, cfg.baselines.curve_days)
        template = await adapter.discover(t)
        by_day = _history_by_day(history)
        value = sum(float(r.get("purchase_value") or 0) for r in history)
        orders = sum(float(r.get("purchases") or 0) for r in history)
        finance = await client.call("metrics_query", G.finance_query(t.brand_id, today))
        guard = G.effective(cfg.guardrails, G.compute(
            finance, value / orders if orders else None, cfg.guardrails.target_roas_multiple, today.isoformat()))
        shop = await client.call("metrics_query", G.store_orders_query(t.brand_id, today, cfg.baselines.window_days))
        tracking = G.tracking_reference(B.pixel_by_day(history), shop)

        per_day: list[dict[str, Any]] = []
        by_rule: collections.Counter[str] = collections.Counter()
        actionable_total = 0
        for i in range(days, 0, -1):
            day = today - timedelta(days=i)
            prior = [r for r in history if date.fromisoformat(str(r["date"])) < day]
            if len({r["date"] for r in prior}) < cfg.baselines.min_days:
                continue
            rows = B.compute(
                prior, hourly, window_days=cfg.baselines.window_days,
                weekday_aware=cfg.baselines.weekday_aware, curve_days=cfg.baselines.curve_days,
                account_id=t.account_id,
            )
            book = book_from_rows(rows, cfg)
            snap = day_snapshot(t, template, by_day, day, tz)
            ctx = RuleContext(snap, book, guard, t, cfg.stats, snap.fetched_at, tracking)
            muted = cfg.model_copy(update={"rules": {**cfg.rules, **{rid: RuleSetting(enabled=False) for rid in SKIP_RULES}}})
            ev = evaluate(ctx, muted, t)
            counts = collections.Counter((f.rule_id, f.hit.severity) for f in ev.findings)
            actionable = [f for f in ev.findings if f.hit.severity in ("critical", "warning")]
            actionable_total += len(actionable)
            for f in actionable:
                by_rule[f.rule_id] += 1
            per_day.append({
                "day": day.isoformat(), "entities": len(snap.entities), "actionable": len(actionable),
                "by_rule": {f"{r}/{s}": n for (r, s), n in sorted(counts.items())},
                "titles": [
                    f"[{f.hit.severity}] {f.title} · {' › '.join(f.path) or 'Account'}: {f.hit.summary}"
                    for f in actionable[:12]
                ],
            })
        avg = (actionable_total / len(per_day)) if per_day else 0.0
        # Plan target (~5/day) is judged on the recent window: the 28-day span includes the
        # pre-13 Nov ROAS regime the baselines themselves document.
        recent = per_day[-14:] if len(per_day) >= 14 else per_day
        recent_avg = (sum(d["actionable"] for d in recent) / len(recent)) if recent else 0.0
        reports.append({
            "tenant_id": t.tenant_id, "days": len(per_day), "actionable_total": actionable_total,
            "actionable_per_day": round(avg, 2), "recent_14d_per_day": round(recent_avg, 2),
            "by_rule": dict(by_rule.most_common()),
            "target_met": recent_avg <= 5.5, "per_day": per_day,
        })
    return {"reports": reports}
