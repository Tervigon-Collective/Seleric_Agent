"""Did a metric move at the event, against its own normal range? (the test WHY_EVENT applies to every candidate driver, and
to the metrics a candidate's effect must pass through, plan P3.2).

A metric moved when, on the event day or the day before, it was beyond one typical deviation of its own reference in the
direction of its net shift over the event. Without a normal range of its own (too little history) or a value on the event
days there is no telling, and the caller is told which."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from seleric_swarm.diagnosis.detect.seasonal import _err, seasonal_profile
from seleric_swarm.diagnosis.detect.window import _additive_window, _daily_window, _ref_value
from seleric_swarm.diagnosis.domain.config import DiagnosisConfig
from seleric_swarm.diagnosis.domain.lineage import MetricMeta
from seleric_swarm.diagnosis.domain.numeric import Series, _finite, _mean, _sign
from seleric_swarm.diagnosis.domain.window import EventWindow

NO_HISTORY = "no_history"  # too little history to know its normal day-to-day range
NO_VALUE = "no_value"  # no value on the event day(s), or too little history for a reference


@dataclass(frozen=True)
class Movement:
    dx: float  # its shift over the event against its reference (summed for an additive outcome, averaged for a rate)
    x_ev: float | None
    x_rf: float | None
    x_z: float | None
    moved: bool


def measure_movement(
    xs: Series, x_meta: MetricMeta, *, history: list[date], event_days: list[date], start: date, n_ev: int,
    window: EventWindow, ref_kind: str, outcome_additive: bool, cfg: DiagnosisConfig,
) -> Movement | str:
    x_prof = seasonal_profile(xs, history, n_ev, additive=x_meta.additive, cfg=cfg)
    if x_prof is None:
        return NO_HISTORY  # without its own normal range there is no telling whether it moved
    x_window = window
    x_refs = {d: x_prof.referencer.refs(d) for d in event_days}
    if all(len(v) >= 2 for v in x_refs.values()):
        x_fac: dict[date, dict[date, float]] = {}
        for d in event_days:
            x_refs[d], x_fac[d] = x_prof.referencer.trimmed(start, x_refs[d])
        x_window = EventWindow(event_days, history, x_refs, sorted(x_prof.outliers), ref_kind, x_fac)
    per_day_dx: list[float] = []
    for d in event_days:
        # Same bar as the outcome's own reference: at least two observed days.
        xr = _ref_value(xs, x_window.reference[d], x_window.factors.get(d), min_days=2)
        xv = xs.get(d)
        if xr is None or not _finite(xv):
            return NO_VALUE
        per_day_dx.append(xv - xr)
    dx = sum(per_day_dx) if outcome_additive else _mean(per_day_dx)
    x_ev, x_rf = (_additive_window(xs, x_window, missing_as_zero=False) if x_meta.additive else _daily_window(xs, x_window))
    x_z = None
    if x_rf is not None and x_ev is not None:
        e = _err(x_ev, x_rf, x_prof.use_log, x_prof.offset * (n_ev if x_meta.additive else 1))
        x_z = x_prof.z(e) if e is not None else None
    # Did the metric move on the event day or the day before (cause precedes effect)?
    lead_moves: list[float] = []
    for d in [start - timedelta(days=1), *event_days]:
        if not _finite(xs.get(d)):
            continue
        rr = x_prof.referencer.refs(d)
        if len(rr) < 2:
            continue
        rr, ref_factors = x_prof.referencer.trimmed(d, rr)
        e = _err(float(xs[d]), _mean([float(xs[r]) * ref_factors[r] for r in rr]), x_prof.use_log, x_prof.offset)
        if e is not None:
            lead_moves.append(x_prof.day_z(e))
    # Moved = beyond one typical deviation on the event day or the day before, in the same direction as the shift that
    # would explain the event.
    moved = any(abs(v) >= 1.0 and _sign(v) == _sign(dx) for v in lead_moves) or (
        x_z is not None and abs(x_z) >= 1.0 and _sign(x_z) == _sign(dx)
    )
    return Movement(dx, x_ev, x_rf, x_z, moved)
