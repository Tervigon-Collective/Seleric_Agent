"""Horizon totals and derived-ratio recomposition (code only, never LLM)."""

from __future__ import annotations

from typing import Iterable

from seleric_swarm.forecasting.types import DailyPoint, TargetForecast


def horizon_totals(
    days: list[DailyPoint],
    *,
    total_error_quantiles: dict[str, list[float]] | None = None,
    horizon_key: str | None = None,
) -> tuple[float, float, float, list[str]]:
    """Return (total_mean, total_p10, total_p90, warnings).

    When calibrated relative-error quantiles exist for the horizon, interval =
    point × (1 + q). Otherwise use the comonotonic sum of daily quantiles
    (conservative) and warn.
    """
    warnings: list[str] = []
    if not days:
        return 0.0, 0.0, 0.0, ["empty_horizon"]
    total_mean = sum(d.mean for d in days)
    key = horizon_key or f"h{len(days)}"
    calibrated = (total_error_quantiles or {}).get(key)
    if calibrated and len(calibrated) >= 2:
        # calibrated = [q_lo, q_hi] relative errors
        q_lo, q_hi = calibrated[0], calibrated[-1]
        return (
            total_mean,
            total_mean * (1.0 + q_lo),
            total_mean * (1.0 + q_hi),
            warnings,
        )
    total_p10 = sum(d.p10 for d in days)
    total_p90 = sum(d.p90 for d in days)
    warnings.append("comonotonic_total_interval")
    return total_mean, total_p10, total_p90, warnings


def ratio_from_components(
    numerator: TargetForecast,
    denominator: TargetForecast,
    *,
    metric_id: str,
    label: str = "",
) -> TargetForecast:
    """Point = ratio of component points; interval from component quantiles (approximate)."""
    warnings = ["approximate_ratio_interval"]
    n_days = min(len(numerator.days), len(denominator.days))
    days: list[DailyPoint] = []
    for i in range(n_days):
        n, d = numerator.days[i], denominator.days[i]
        if d.mean == 0:
            mean = p10 = p50 = p90 = 0.0
        else:
            mean = n.mean / d.mean
            # Bounds from component quantile ratios (not causal; labelled approximate).
            candidates = [
                n.p10 / d.p90 if d.p90 else mean,
                n.p10 / d.p10 if d.p10 else mean,
                n.p90 / d.p10 if d.p10 else mean,
                n.p90 / d.p90 if d.p90 else mean,
            ]
            p10 = min(candidates)
            p90 = max(candidates)
            p50 = mean
        days.append(DailyPoint(date=n.date, mean=mean, p10=p10, p50=p50, p90=p90))
    total_n = numerator.total_mean or sum(d.mean for d in numerator.days)
    total_d = denominator.total_mean or sum(d.mean for d in denominator.days)
    total_mean = (total_n / total_d) if total_d else 0.0
    return TargetForecast(
        metric_id=metric_id,
        label=label or metric_id,
        status=numerator.status if numerator.status == denominator.status else "provisional",
        days=days,
        total_mean=total_mean,
        total_p10=None,
        total_p90=None,
        reason_codes=list(dict.fromkeys([*numerator.reason_codes, *denominator.reason_codes, *warnings])),
    )


def aggregate_days(days: Iterable[DailyPoint], *, grain: str) -> list[DailyPoint]:
    """Roll daily points up to week/month by summing means and comonotonic quantiles."""
    grouped: dict[str, list[DailyPoint]] = {}
    for d in days:
        key = d.date[:7] if grain == "month" else _iso_week(d.date)
        grouped.setdefault(key, []).append(d)
    out: list[DailyPoint] = []
    for key, pts in grouped.items():
        out.append(
            DailyPoint(
                date=key,
                mean=sum(p.mean for p in pts),
                p10=sum(p.p10 for p in pts),
                p50=sum(p.p50 for p in pts),
                p90=sum(p.p90 for p in pts),
            )
        )
    return out


def _iso_week(iso_day: str) -> str:
    from datetime import date

    d = date.fromisoformat(iso_day[:10])
    iso = d.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"
