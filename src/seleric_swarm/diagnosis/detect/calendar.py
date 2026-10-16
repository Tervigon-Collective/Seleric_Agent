"""A robust (Huber) fit of a series on trend and day-of-week, refit without the days it flags (moved from
``causal/diagnosis.py``, plan P2.2). The regression itself is in ``libs/robust_fit``; a failure falls back to least
squares and is reported in the trace, never swallowed."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from seleric_swarm.diagnosis.domain.config import DiagnosisConfig
from seleric_swarm.diagnosis.domain.errors import DiagnosisError
from seleric_swarm.diagnosis.domain.numeric import Series, _finite, _median
from seleric_swarm.diagnosis.libs.robust_fit import huber_fit
from seleric_swarm.diagnosis.trace.active import report_failure


@dataclass
class CalendarFit:
    """Robust (Huber) fit of ``y ~ trend + day-of-week`` over history days."""

    params: list[float]
    scale: float
    t0: date
    has_dow: bool
    n: int
    residual_z: dict[date, float] = field(default_factory=dict)

    def _row(self, d: date) -> list[float]:
        row = [1.0, (d - self.t0).days / 7.0]
        if self.has_dow:
            row += [1.0 if d.weekday() == k else 0.0 for k in range(1, 7)]
        return row

    def predict(self, d: date) -> float:
        return sum(p * x for p, x in zip(self.params, self._row(d), strict=True))


def fit_calendar(series: Series, days: list[date], *, cfg: DiagnosisConfig) -> CalendarFit | None:
    pts = [(d, float(series[d])) for d in sorted(days) if d in series and _finite(series[d])]
    if len(pts) < cfg.min_observation_rows:
        return None
    import numpy as np

    t0 = pts[0][0]
    has_dow = (pts[-1][0] - t0).days + 1 >= 21 and len(pts) >= 14
    fit = CalendarFit(params=[], scale=0.0, t0=t0, has_dow=has_dow, n=len(pts))
    X = np.array([fit._row(d) for d, _ in pts])
    y = np.array([v for _, v in pts])

    def robust(Xs: Any, ys: Any) -> tuple[list[float], float]:
        try:
            params, scale = huber_fit(Xs, ys)
        except DiagnosisError as exc:
            report_failure("robust_calendar_fit", exc, {"rows": len(ys)})
            params, scale = [float(p) for p in np.linalg.lstsq(Xs, ys, rcond=None)[0]], 0.0
        r = ys - Xs @ np.array(params)
        if not scale or not math.isfinite(scale):
            mad = _median([abs(v - _median(list(r))) for v in r]) * 1.4826
            scale = mad if mad > 0 else float(np.std(r)) or 1e-9
        return params, scale

    # Fit, drop the days it flags as outliers, refit — up to three rounds. A long
    # abnormal stretch (an outage week) is ~15-20% of a two-month history and
    # still inflates a single Huber pass enough to hide a real event.
    keep = np.ones(len(y), dtype=bool)
    params, scale = robust(X, y)
    for _ in range(3):
        z = (y - X @ np.array(params)) / scale
        new_keep = np.abs(z) < cfg.outlier_z
        if new_keep.sum() < max(cfg.min_observation_rows, X.shape[1] + 3) or (new_keep == keep).all():
            break
        keep = new_keep
        params, scale = robust(X[keep], y[keep])
    fit.params = params
    fit.scale = scale
    resid = y - X @ np.array(params)
    fit.residual_z = {d: float(r / scale) for (d, _), r in zip(pts, resid, strict=True)}
    return fit
