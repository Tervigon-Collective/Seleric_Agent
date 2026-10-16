"""Event and reference values over a window, additive (sum) or daily-mean, with the level factors that keep references
exactly additive (moved from ``causal/diagnosis.py``, plan P2.2)."""

from __future__ import annotations

import math
from datetime import date, timedelta

from seleric_swarm.diagnosis.domain.numeric import Series, _finite, _mean
from seleric_swarm.diagnosis.domain.window import EventWindow


def _ref_value(
    series: Series, refs: list[date], factors: dict[date, float] | None = None, *, min_days: int = 1,
) -> float | None:
    vals = [float(series[r]) * (factors or {}).get(r, 1.0) for r in refs if r in series and _finite(series[r])]
    return _mean(vals) if len(vals) >= max(1, min_days) else None


def _additive_window(series: Series, window: EventWindow, *, missing_as_zero: bool) -> tuple[float | None, float | None]:
    """(event sum, reference sum) of an additive series."""
    ev = 0.0
    rf = 0.0
    for d in window.event_days:
        v = series.get(d)
        if not _finite(v):
            if not missing_as_zero:
                return None, None
            v = 0.0
        ev += float(v)
        vals = [
            (float(series.get(r, 0.0)) if missing_as_zero else float(series[r])) * window.factor(d, r)
            for r in window.reference[d] if (r in series or missing_as_zero)
        ]
        vals = [x for x in vals if math.isfinite(x)]
        if not vals:
            return ev, None
        rf += _mean(vals)
    return ev, rf


def _daily_window(series: Series, window: EventWindow) -> tuple[float | None, float | None]:
    """(event mean, reference mean) of a non-additive daily series."""
    ev = [float(series[d]) for d in window.event_days if d in series and _finite(series[d])]
    if len(ev) != len(window.event_days):
        return None, None
    refs = [_ref_value(series, window.reference[d], window.factors.get(d)) for d in window.event_days]
    if any(r is None for r in refs):
        return _mean(ev), None
    return _mean(ev), _mean([r for r in refs if r is not None])


def reference_days(
    event_days: list[date], history: list[date], outliers: set[date], weeks: int
) -> tuple[dict[date, list[date]], str]:
    hist = set(history)
    ref: dict[date, list[date]] = {}
    kind = "same weekday"
    for d in event_days:
        picks = [
            d - timedelta(days=7 * k)
            for k in range(1, 3 * weeks + 1)
            if (d - timedelta(days=7 * k)) in hist and (d - timedelta(days=7 * k)) not in outliers
        ][:weeks]
        ref[d] = picks
    if any(len(v) < 2 for v in ref.values()):
        # Not enough same-weekday history: fall back to the nearest normal days.
        kind = "nearest normal days (too little same-weekday history)"
        normal = sorted((h for h in hist if h not in outliers), reverse=True)
        for d in event_days:
            if len(ref[d]) < 2:
                ref[d] = [h for h in normal if h < min(event_days)][: max(weeks, 2)]
    return ref, kind
