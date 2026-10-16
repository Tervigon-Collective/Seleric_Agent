"""IS_REAL: is the change real and how big is it (plan P2.2). Compares the event with the same weekday in recent weeks
(outlier weeks skipped) and judges its size against a robust day-of-week + trend fit of the history; a change inside
normal variation is reported as such. Evidence basis: statistical. Failure modes: too little history, an undefined
reference, a partial day in the window: each returns the "insufficient data" report with the reason, never a guess."""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any, Literal

from seleric_swarm.diagnosis.detect.seasonal import (
    _PERIOD_KIND,
    _err,
    _period_shift_z,
    _reference_kind,
    seasonal_profile,
)
from seleric_swarm.diagnosis.detect.window import reference_days
from seleric_swarm.diagnosis.domain.analysis import EventAnalysis, EventResult
from seleric_swarm.diagnosis.domain.config import DiagnosisConfig
from seleric_swarm.diagnosis.domain.inputs import DiagnosisInput
from seleric_swarm.diagnosis.domain.lineage import MetricMeta
from seleric_swarm.diagnosis.domain.numeric import _finite, _mean, _rel, _sign
from seleric_swarm.diagnosis.domain.report import DiagnosisReport, Direction, EventSummary
from seleric_swarm.diagnosis.domain.window import EventWindow
from seleric_swarm.diagnosis.operators.identities import discover_identities
from seleric_swarm.diagnosis.operators.is_real_terms import _event_value


def _stop(**fields: Any) -> EventResult:
    return EventResult(early=DiagnosisReport(**fields))


def run_is_real(inp: DiagnosisInput, *, cfg: DiagnosisConfig) -> EventResult:
    y = inp.outcome
    ys = inp.series.get(y, {})
    event_days = sorted(inp.event_days)
    window_label = [d.isoformat() for d in event_days]
    quality: list[str] = []
    assumptions: list[str] = []
    if not event_days:
        return _stop(outcome=y, event_window=[], verdict="insufficient_data", headline="No event window given.", event=None)
    if any(d in inp.partial_days for d in event_days):
        quality.append(
            "The event window includes a day that is still in progress; a partial day always looks "
            "like a fall against complete days, so this comparison is not meaningful yet."
        )
        return _stop(
            outcome=y, event_window=window_label, verdict="insufficient_data",
            headline="The period asked about is not complete yet, so its change cannot be diagnosed.",
            event=None, data_quality=quality,
        )
    missing_event = [d for d in event_days if not _finite(ys.get(d))]
    # A period ratio is rebuilt from its components' totals, so a day where the
    # ratio is undefined (no spend that day) still counts; a short event cannot.
    if missing_event and inp.baseline_days and len(missing_event) < len(event_days):
        quality.append(
            f"{y} is undefined on {len(missing_event)} day(s) of the period (its denominator was zero); the period "
            "value is rebuilt from its components' totals"
        )
        missing_event = []
    if missing_event:
        return _stop(
            outcome=y, event_window=window_label, verdict="insufficient_data",
            headline=f"No data for {y} on {', '.join(d.isoformat() for d in missing_event)}.",
            event=None, data_quality=[f"{y} has no value on the event day(s)"],
        )
    start = min(event_days)
    history = sorted(d for d in ys if d < start and d not in inp.partial_days and _finite(ys[d]))
    expected_span = (start - min(history)).days if history else 0
    if history and len(history) < expected_span * 0.8:
        quality.append(f"{y} is missing {expected_span - len(history)} of {expected_span} history days")
    meta_y = inp.lineage.get(y, MetricMeta(y))
    n_ev = len(event_days)
    weight_metric = inp.denominators.get(y)
    baseline = sorted(inp.baseline_days)
    if baseline and len(baseline) != n_ev:
        return _stop(
            outcome=y, event_window=window_label, verdict="insufficient_data",
            headline="The comparison period must be as long as the period asked about.", event=None, data_quality=quality,
        )
    if baseline and (missing_base := [d for d in baseline if not _finite(ys.get(d))]):
        quality.append(f"{y} has no value on {len(missing_base)} day(s) of the comparison period")
    profile = seasonal_profile(ys, history, n_ev if not baseline else 1, additive=meta_y.additive, cfg=cfg)
    if profile is None and not baseline:
        return _stop(
            outcome=y, event_window=window_label, verdict="insufficient_data",
            headline=(
                f"Only {len(history)} days of history for {y}; too few to learn what a normal day looks like "
                "and tell a real change from noise."
            ),
            event=None, data_quality=quality,
        )
    same_weekday = bool(profile and profile.same_weekday)
    if baseline:
        # Day i of the event is referenced on day i of the comparison period, so
        # every sum (and every ratio of sums) is exactly the comparison period's.
        outliers: set[date] = set()
        refs = {d: [b] for d, b in zip(event_days, baseline, strict=True)}
        ref_kind = _PERIOD_KIND
        window = EventWindow(event_days, history, refs, [], ref_kind, {})
    else:
        assert profile is not None
        outliers = profile.outliers
        referencer = profile.referencer
        refs = {d: referencer.refs(d) for d in event_days}
        ref_kind = _reference_kind(profile)
        if any(len(v) < 2 for v in refs.values()):
            refs, ref_kind = reference_days(event_days, history, outliers, cfg.reference_weeks)
            factors: dict[date, dict[date, float]] = {}
        else:
            factors = {}
            for d in event_days:
                refs[d], factors[d] = referencer.trimmed(start, refs[d])
        window = EventWindow(event_days, history, refs, sorted(outliers), ref_kind, factors)
    if outliers:
        quality.append(
            f"{len(outliers)} abnormal history day(s) excluded from the reference: "
            + ", ".join(d.isoformat() for d in sorted(outliers)[:8])
            + (" …" if len(outliers) > 8 else "")
        )

    identities = discover_identities(y, inp.lineage, inp.series, history, cfg=cfg)
    identity = identities[0] if identities else None
    y_ev, y_rf, agg_note = _event_value(y, inp, window, identity, cfg=cfg)
    if y_ev is None or y_rf is None or abs(y_rf) < 1e-12:
        return _stop(
            outcome=y, event_window=window_label, verdict="insufficient_data",
            headline=f"Could not form a reference for {y} from comparable past days.", event=None, data_quality=quality,
        )
    delta = y_ev - y_rf
    if baseline:
        z, p_period = _period_shift_z(ys, event_days, baseline)
        rel_err = None
    else:
        assert profile is not None
        rel_err = _err(y_ev, y_rf, profile.use_log, profile.offset * (n_ev if meta_y.additive else 1))
        if rel_err is None:
            return _stop(
                outcome=y, event_window=window_label, verdict="insufficient_data",
                headline=f"{y} cannot be compared with its reference on a common scale.", event=None, data_quality=quality,
            )
        z = profile.z(rel_err)
    unusual = abs(z) >= cfg.event_z and _sign(delta) == _sign(z)
    notable = not unusual and abs(z) >= cfg.event_z_notable and _sign(delta) == _sign(z)
    direction: Direction | None = None if _sign(delta) == 0 else ("up" if delta > 0 else "down")
    prev_days = [d - timedelta(days=n_ev) for d in event_days]
    prev_vals = [ys.get(d) for d in prev_days]
    previous: dict[str, Any] = {"days": [d.isoformat() for d in prev_days]}
    prev_finite = [v for v in prev_vals if _finite(v)]
    if len(prev_finite) == len(prev_vals):
        pv = sum(prev_finite) if meta_y.additive else _mean(prev_finite)
        ev_vals = [float(ys[d]) for d in event_days if _finite(ys.get(d))]
        ev_simple = sum(ev_vals) if meta_y.additive else _mean(ev_vals)
        previous.update({"value": pv, "delta": ev_simple - pv, "delta_pct": _rel(ev_simple - pv, pv)})
    prev_direction: Direction | None = None
    if previous.get("delta") and abs(previous.get("delta_pct") or 0.0) >= 0.05:
        prev_direction = "up" if previous["delta"] > 0 else "down"
    premise: Literal["confirmed", "contradicted", "not_unusual", "not_stated", "vs_previous_only"]
    if inp.claimed_direction is None:
        premise = "not_stated"
    elif direction is not None and direction != inp.claimed_direction and (
        abs(z) >= 0.5 or abs(_rel(delta, y_rf) or 0.0) >= 0.10
    ):
        # it moved the other way by a material amount — unless the user's baseline
        # is the day before, where the asked direction does hold
        premise = "vs_previous_only" if prev_direction == inp.claimed_direction else "contradicted"
    elif not (unusual or notable):
        premise = "not_unusual"
    else:
        premise = "confirmed" if inp.claimed_direction == direction else "contradicted"

    sampling_extra: dict[str, Any] = {}
    if weight_metric and not meta_y.additive and 0.0 <= y_ev <= 1.0 and 0.0 <= y_rf <= 1.0:
        w_ev = sum(float(inp.series.get(weight_metric, {}).get(d, 0.0)) for d in event_days)
        if w_ev > 0 and 0 < y_rf < 1:
            sampling_extra["binomial_z"] = (y_ev - y_rf) / math.sqrt(y_rf * (1 - y_rf) / w_ev)
            sampling_extra["events_counted"] = y_ev * w_ev
    profile_sampling: dict[str, Any] = {}
    if not baseline and profile is not None and rel_err is not None:
        profile_sampling = {
            "typical_deviation": profile.scale, "scale": "log" if profile.use_log else "relative",
            "history_windows": len(profile.errors), "percentile_vs_history": profile.percentile(rel_err),
        }
    event = EventSummary(
        metric=y, actual=y_ev, reference=y_rf, delta=delta, delta_pct=_rel(delta, y_rf),
        expected_by_model=None, z_score=z, unusual=unusual, direction=direction, premise=premise,
        strength="strong" if unusual else ("moderate" if notable else "none"),
        previous_period=previous, reference_kind=ref_kind,
        reference_days={d.isoformat(): [r.isoformat() for r in refs[d]] for d in event_days},
        level_factors={d.isoformat(): {r.isoformat(): round(f, 4) for r, f in window.factors.get(d, {}).items()} for d in event_days},
        excluded_outlier_days=[d.isoformat() for d in sorted(outliers)], aggregation_note=agg_note,
        sampling=({
            "test": "two-sample rank test of the daily values in the two periods", "p_value": p_period,
            **sampling_extra,
        } if baseline else {**profile_sampling, **sampling_extra}),
    )

    if baseline:
        assumptions.append(
            f"Reference = the comparison period {baseline[0].isoformat()}..{baseline[-1].isoformat()} as booked; "
            "significance = a rank test of the two periods' daily values (days are not independent, so treat it as "
            "a guide)."
        )
    else:
        assumptions.append(
            f"Reference = {ref_kind} (whichever predicted this metric's own history better): "
            + (f"the {cfg.reference_weeks} most recent normal same-weekday days" if same_weekday
               else "the 7 most recent normal days")
            + ", each scaled by how "
            "the trailing 7-normal-day level changed since then, highest and lowest dropped, the rest averaged; "
            "significance = how far the event sits from its reference compared with how far every past day sat from "
            "its own reference (median/MAD of those deviations)."
        )
    return EventResult(analysis=EventAnalysis(
        inp=inp,
        y=y,
        ys=ys,
        event_days=event_days,
        window_label=window_label,
        n_ev=n_ev,
        start=start,
        history=history,
        baseline=baseline,
        meta_y=meta_y,
        weight_metric=weight_metric,
        window=window,
        same_weekday=same_weekday,
        outliers=outliers,
        ref_kind=ref_kind,
        identities=identities,
        identity=identity,
        y_ev=y_ev,
        y_rf=y_rf,
        delta=delta,
        unusual=unusual,
        notable=notable,
        prev_days=prev_days,
        previous=previous,
        quality=quality,
        event=event,
        assumptions=assumptions,
    ))
