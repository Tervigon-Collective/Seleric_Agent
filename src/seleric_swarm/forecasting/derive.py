"""Horizon totals and derived-ratio recomposition (code only, never LLM)."""

from __future__ import annotations

from collections.abc import Iterable

from seleric_swarm.models.service import Z_80
from seleric_swarm.forecasting.types import DailyPoint, TargetForecast


def horizon_totals(
    days: list[DailyPoint],
    *,
    total_error_quantiles: dict[str, list[float]] | None = None,
    horizon_key: str | None = None,
    total_sd: float | None = None,
    empirical: tuple[float, float, int] | None = None,
) -> tuple[float, float, float, list[str]]:
    """Return (total_mean, total_p10, total_p90, warnings).

    Best: the series' own rolling-origin errors at this horizon (``empirical``). Else, when
    calibrated relative-error quantiles exist for the horizon, interval =
    point × (1 + q) (nearest backtested horizon when this one has none). Else the engine's own total SD (shared-shock aware). Last
    resort: the comonotonic sum of daily quantiles (very conservative) with a warning.
    """
    warnings: list[str] = []
    if not days:
        return 0.0, 0.0, 0.0, ["empty_horizon"]
    total_mean = sum(d.mean for d in days)
    n = len(days)
    floor = 0.0 if all(d.mean >= 0 for d in days) else float("-inf")  # a non-negative metric has no negative total
    if empirical is not None:
        q_lo, q_hi, windows = empirical
        warnings.append(f"total_interval_backtested:{windows}_windows")
        return total_mean, max(floor, total_mean * (1 + q_lo)), max(floor, total_mean * (1 + q_hi)), warnings
    key = horizon_key or f"h{n}"
    errors = total_error_quantiles or {}
    calibrated = errors.get(key)
    scale = 1.0
    if not calibrated:
        # Nearest backtested horizon. Shorter horizons average less daily noise, so widen
        # by sqrt(h_cal / n); longer ones keep the measured relative error (level misses persist).
        near = sorted(
            ((abs(int(k[1:]) - n), int(k[1:]), v) for k, v in errors.items()
             if k[1:].isdigit() and isinstance(v, list) and len(v) >= 2)
        )
        if near:
            _, h_cal, calibrated = near[0]
            scale = max(1.0, (h_cal / n) ** 0.5)
            warnings.append(f"total_interval_scaled_from_h{h_cal}")
    if calibrated and len(calibrated) >= 2:
        # calibrated = [q_lo, q_hi] relative errors
        q_lo, q_hi = calibrated[0] * scale, calibrated[-1] * scale
        return (
            total_mean,
            max(floor, total_mean * (1.0 + q_lo)),
            max(floor, total_mean * (1.0 + q_hi)),
            warnings,
        )
    if total_sd is not None:
        z = Z_80  # same 80% band the daily P10/P90 carry
        return (
            total_mean,
            max(floor, total_mean - z * total_sd),
            max(floor, total_mean + z * total_sd),
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
