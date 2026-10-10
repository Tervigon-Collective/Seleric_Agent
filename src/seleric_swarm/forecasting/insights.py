"""Generic, metric-agnostic facts about a forecast, computed in code.

The agent turns these into an interpretation. Nothing here knows what a metric
*is*: every fact comes from the shape of the history and of the forecast path
(level vs recent actuals, direction inside the horizon, weekday rhythm, band
width, calendar events falling in the window, how stable the history was).
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from seleric_swarm.forecasting.calendar import load_calendar_config
from seleric_swarm.forecasting.types import DailyPoint

_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _pct(new: float, base: float) -> float | None:
    return None if base == 0 else (new - base) / abs(base) * 100.0


def _observed(values: list[float | None]) -> list[float]:
    return [float(v) for v in values if v is not None]


def build_insight_facts(
    *,
    history: list[float | None],
    history_dates: list[date],
    days: list[DailyPoint],
    nonnegative: bool = True,
) -> dict[str, Any]:
    """Facts about one target's forecast relative to its own history."""
    facts: dict[str, Any] = {}
    if not days:
        return facts
    n = len(days)
    fc = [d.p50 for d in days]
    fc_total = sum(d.mean for d in days)
    obs = _observed(history)

    # 1. Level vs the same-length stretch just before the cutoff, and vs a
    #    longer baseline — "is the future above or below what we have been doing?"
    recent_vals = obs[-n:]
    if recent_vals:
        recent_mean = statistics.mean(recent_vals)
        facts["recent_daily_mean"] = recent_mean
        facts["recent_window_days"] = len(recent_vals)
        facts["forecast_daily_mean"] = fc_total / n
        facts["level_vs_recent_pct"] = _pct(fc_total / n, recent_mean)
    longer = obs[-90:]
    if len(longer) >= 30:
        base = statistics.mean(longer)
        facts["baseline_90d_daily_mean"] = base
        facts["level_vs_90d_pct"] = _pct(fc_total / n, base)

    # 2. How steady the history was (coefficient of variation) — drives how much
    #    weight a single-number answer deserves.
    if len(obs) >= 14:
        m = statistics.mean(obs[-90:])
        sd = statistics.pstdev(obs[-90:]) if len(obs[-90:]) > 1 else 0.0
        facts["history_volatility_cv"] = (sd / abs(m)) if m else None

    # 3. Direction inside the horizon (first half vs second half of the median path).
    if n >= 6:
        half = n // 2
        first, second = statistics.mean(fc[:half]), statistics.mean(fc[-half:])
        facts["within_horizon_change_pct"] = _pct(second, first)

    # 4. Weekly rhythm in the forecast: strongest / weakest weekday vs the average.
    if n >= 7:
        by_dow: dict[int, list[float]] = defaultdict(list)
        for d in days:
            by_dow[date.fromisoformat(d.date[:10]).weekday()].append(d.p50)
        avg = statistics.mean(fc)
        if avg:
            rel = {k: statistics.mean(v) / avg - 1.0 for k, v in by_dow.items()}
            hi, lo = max(rel, key=rel.get), min(rel, key=rel.get)
            facts["weekday_peak"] = {"day": _WEEKDAYS[hi], "vs_avg_pct": rel[hi] * 100}
            facts["weekday_trough"] = {"day": _WEEKDAYS[lo], "vs_avg_pct": rel[lo] * 100}
            facts["weekday_spread_pct"] = (rel[hi] - rel[lo]) * 100

    # 5. Peak and low days in the window.
    peak = max(days, key=lambda d: d.p50)
    low = min(days, key=lambda d: d.p50)
    facts["peak_day"] = {"date": peak.date, "p50": peak.p50}
    facts["low_day"] = {"date": low.date, "p50": low.p50}

    # 6. Uncertainty: relative width of the 80% band, and floor pressure.
    widths = [(d.p90 - d.p10) / abs(d.p50) for d in days if d.p50]
    if widths:
        facts["band_width_rel_median"] = statistics.median(widths)
        facts["band_width_rel_end"] = widths[-1]
    if nonnegative:
        facts["days_with_p10_at_floor"] = sum(1 for d in days if d.p10 <= 0)

    # 7. Calendar events inside the window (from the versioned calendar file).
    try:
        cfg = load_calendar_config()
        start = date.fromisoformat(days[0].date[:10])
        end = date.fromisoformat(days[-1].date[:10])
        events = []
        for entry in cfg.festivals:
            for iso in entry.dates:
                d = date.fromisoformat(iso[:10])
                lead = timedelta(days=cfg.pre_festival_days)
                if start - timedelta(days=0) <= d <= end:
                    events.append({"event": entry.id.replace("_", " "), "date": iso[:10], "kind": "in_window"})
                elif d > end and d - end <= lead + timedelta(days=0):
                    events.append({"event": entry.id.replace("_", " "), "date": iso[:10], "kind": "just_after"})
        if events:
            facts["calendar_events"] = events
    except Exception:  # noqa: BLE001 - advisory only
        pass

    # 8. Same-day prior-year anchor when the history reaches back far enough.
    try:
        by_date = {d: v for d, v in zip(history_dates, history, strict=False) if v is not None}
        ly = []
        for d in days:
            dd = date.fromisoformat(d.date[:10]) - timedelta(days=364)  # same weekday
            if dd in by_date:
                ly.append(by_date[dd])
        if len(ly) >= max(3, n // 2):
            facts["same_period_last_year_daily_mean"] = statistics.mean(ly)
            facts["level_vs_last_year_pct"] = _pct(fc_total / n, statistics.mean(ly))
    except Exception:  # noqa: BLE001
        pass
    return facts


def render_insight_facts(label: str, facts: dict[str, Any], *, unit: str | None = None) -> list[str]:
    """Compact, labelled lines for the agent. Numbers are exact; wording is neutral."""
    if not facts:
        return []
    u = f" {unit}" if unit else ""

    def n(x: Any) -> str:
        return f"{x:,.0f}" if isinstance(x, (int, float)) and abs(x) >= 100 else f"{x:,.3g}"

    def p(x: Any) -> str:
        return "n/a" if x is None else f"{x:+.1f}%"

    lines = [f"INSIGHT FACTS for {label} (computed in code; interpret them, do not just repeat them):"]
    if "forecast_daily_mean" in facts and "recent_daily_mean" in facts:
        lines.append(
            f"- forecast daily average {n(facts['forecast_daily_mean'])}{u} vs the last "
            f"{facts.get('recent_window_days')} days' average {n(facts['recent_daily_mean'])}{u} "
            f"({p(facts.get('level_vs_recent_pct'))})"
        )
    if facts.get("level_vs_90d_pct") is not None:
        lines.append(f"- vs the 90-day baseline: {p(facts['level_vs_90d_pct'])}")
    if facts.get("level_vs_last_year_pct") is not None:
        lines.append(f"- vs the same weekdays a year earlier: {p(facts['level_vs_last_year_pct'])}")
    if facts.get("within_horizon_change_pct") is not None:
        lines.append(f"- direction inside the window (second half vs first half): {p(facts['within_horizon_change_pct'])}")
    if "weekday_peak" in facts:
        pk, tr = facts["weekday_peak"], facts["weekday_trough"]
        lines.append(
            f"- weekly rhythm: strongest {pk['day']} ({p(pk['vs_avg_pct'])} vs average), "
            f"weakest {tr['day']} ({p(tr['vs_avg_pct'])}); spread {facts['weekday_spread_pct']:.0f} pts"
        )
    pd_, ld = facts["peak_day"], facts["low_day"]
    lines.append(f"- highest day {pd_['date']} ({n(pd_['p50'])}{u}); lowest day {ld['date']} ({n(ld['p50'])}{u})")
    if facts.get("history_volatility_cv") is not None:
        lines.append(f"- history volatility (std/mean, last 90 days): {facts['history_volatility_cv']:.2f}")
    if "band_width_rel_median" in facts:
        lines.append(
            f"- uncertainty: the 80% band is typically {facts['band_width_rel_median']:.0%} of the median day "
            f"(widest at the end: {facts['band_width_rel_end']:.0%})"
        )
    if facts.get("days_with_p10_at_floor"):
        lines.append(f"- {facts['days_with_p10_at_floor']} day(s) have a lower bound at zero (a real chance of a near-empty day)")
    if facts.get("calendar_events"):
        ev = "; ".join(f"{e['event']} on {e['date']} ({e['kind'].replace('_', ' ')})" for e in facts["calendar_events"])
        lines.append(f"- calendar events around the window: {ev}")
    return lines
