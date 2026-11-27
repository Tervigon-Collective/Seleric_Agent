"""Nightly baselines: per entity and level, p10 / p50 / p90 of daily values and the pooled value over the window,
plus the account's hour-of-day curves (spend and impressions). Pure computation; storage is in ``store``."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from seleric_swarm.services.live_monitor.core.model import ADDITIVE, sum_metrics
from seleric_swarm.services.live_monitor.core.stats import quantiles

DAILY_METRICS = ("spend", "impressions", "purchases", "cpm", "ctr_link", "cpa", "roas", "aov", "lpv_per_click", "cvr_lpv")
WEEKDAY_METRICS = ("spend", "impressions", "purchases")
WEEKDAY_LEVELS = ("account", "campaign")
POOLED = {"cpm", "ctr_link", "cpa", "roas", "aov", "lpv_per_click", "cvr_lpv"}


def _days_by_entity(history: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, list[dict[str, float]]]]:
    """(level, id) -> date -> list of ad rows."""
    out: dict[tuple[str, str], dict[str, list[dict[str, float]]]] = defaultdict(lambda: defaultdict(list))
    for r in history:
        parts = {k: float(r.get(k) or 0.0) for k in ADDITIVE}
        for level, key in (("ad", r.get("ad_id")), ("adset", r.get("adset_id")), ("campaign", r.get("campaign_id")), ("account", "")):
            if key is not None and r.get("date"):
                out[(level, str(key))][str(r["date"])].append(parts)
    return out


def compute(history: list[dict[str, Any]], hourly: dict[int, dict[str, float]] | None, *, window_days: int,
            weekday_aware: bool, curve_days: int, account_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for (level, key), by_day in _days_by_entity(history).items():
        entity_id = account_id if level == "account" else key
        daily = {d: sum_metrics(parts) for d, parts in by_day.items()}
        active = {d: m for d, m in daily.items() if (m.get("spend") or 0) > 0}
        if not active:
            continue
        pooled = sum_metrics(list(active.values()))
        n_days = len(active)
        for metric in DAILY_METRICS:
            values = [m[metric] for m in active.values() if m.get(metric) is not None]
            q = quantiles(values)
            if q is None:
                continue
            mean = pooled.get(metric) if metric in POOLED else sum(values) / len(values)
            rows.append(_row(level, entity_id, metric, None, window_days, q, mean, len(values), "daily_quantiles"))
        spend = pooled.get("spend") or 0.0
        rate = (pooled.get("purchases") or 0.0) / spend if spend else None
        rows.append(_row(level, entity_id, "purchases_per_inr", None, window_days, (None, None, None), rate, n_days, "pooled"))
        if weekday_aware and level in WEEKDAY_LEVELS:
            by_wd: dict[int, list[dict[str, float | None]]] = defaultdict(list)
            for d, m in active.items():
                by_wd[date.fromisoformat(d[:10]).weekday()].append(m)
            for wd, ms in by_wd.items():
                for metric in WEEKDAY_METRICS:
                    q = quantiles([m[metric] for m in ms if m.get(metric) is not None])
                    if q is not None:
                        rows.append(_row(level, entity_id, metric, wd, window_days, q, q[1], len(ms), "weekday_quantiles"))
    if hourly:
        for metric in ("spend", "impressions"):
            curve = cumulative_curve(hourly, metric)
            if curve:
                rows.append(_row("account", account_id, f"curve_{metric}", None, curve_days, (None, None, None), None, curve_days,
                                 "hour_of_day_share", extra={"curve": curve}))
    return rows


def cumulative_curve(hourly: dict[int, dict[str, float]], metric: str) -> list[float] | None:
    per_hour = [float((hourly.get(h) or {}).get(metric) or 0.0) for h in range(24)]
    total = sum(per_hour)
    if total <= 0:
        return None
    out, run = [], 0.0
    for v in per_hour:
        run += v
        out.append(round(run / total, 6))
    out[-1] = 1.0
    return out


def _row(level: str, entity_id: str, metric: str, weekday: int | None, window_days: int,
         q: tuple[float | None, float | None, float | None], mean: float | None, n: int, method: str,
         extra: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"level": level, "entity_id": entity_id, "metric": metric, "weekday": weekday, "window_days": window_days,
            "p10": q[0], "p50": q[1], "p90": q[2], "mean": mean, "n": n, "method": method, "extra": extra}


def pixel_by_day(history: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """Account totals per day: pixel purchases and add-to-carts (tracking reference)."""
    out: dict[str, dict[str, float]] = defaultdict(lambda: {"purchases": 0.0, "atc": 0.0, "spend": 0.0})
    for r in history:
        d = str(r.get("date") or "")[:10]
        if d:
            for k in ("purchases", "atc", "spend"):
                out[d][k] += float(r.get(k) or 0.0)
    return dict(out)
