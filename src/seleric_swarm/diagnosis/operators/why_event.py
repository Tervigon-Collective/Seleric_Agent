"""WHY_EVENT: which upstream driver moved and explains this event (plan P2.2). Candidates are estimated with DoWhy on an
explicit time-series DAG (calendar and lagged pre-treatment variables, HAC intervals, refuters plus temporal-precedence
tests); contribution = effect x how far the driver itself moved from its own reference. Classification is rule-based and
conservative (supported_cause / likely_contributor / correlation / insufficient_evidence). Evidence basis: causal.
Failure modes: too little history, a driver that did not move (skipped, not estimated), an estimator that failed (the
driver is reported as insufficient evidence, the failure is in the trace)."""

from __future__ import annotations

import math
from datetime import date, timedelta
from itertools import combinations
from typing import Literal

from seleric_swarm.diagnosis.detect.seasonal import _err, _lockstep_correlation, seasonal_profile
from seleric_swarm.diagnosis.detect.window import _additive_window, _daily_window, _ref_value
from seleric_swarm.diagnosis.domain.analysis import EventAnalysis, WhatResult, WhyResult
from seleric_swarm.diagnosis.domain.config import DiagnosisConfig
from seleric_swarm.diagnosis.domain.estimates import TimeSeriesEffect
from seleric_swarm.diagnosis.domain.lineage import MetricMeta, descendants, unit_count_metric
from seleric_swarm.diagnosis.domain.numeric import Series, _finite, _mean, _sign
from seleric_swarm.diagnosis.domain.report import DriverFinding
from seleric_swarm.diagnosis.domain.window import EventWindow
from seleric_swarm.diagnosis.operators.why_terms import (
    _classify_driver,
    _PendingDriver,
    _residual_correlation,
)
from seleric_swarm.diagnosis.ports.estimator import EffectEstimator
from seleric_swarm.diagnosis.trace.active import report_recorded_failure


def run_why_event(ana: EventAnalysis, what: WhatResult, *, cfg: DiagnosisConfig, estimator: EffectEstimator) -> WhyResult:
    inp, y, ys, event_days, n_ev = ana.inp, ana.y, ana.ys, ana.event_days, ana.n_ev
    start, history, baseline, meta_y, weight_metric = ana.start, ana.history, ana.baseline, ana.meta_y, ana.weight_metric
    window, same_weekday, outliers, ref_kind, identities = ana.window, ana.same_weekday, ana.outliers, ana.ref_kind, ana.identities
    identity, delta = ana.identity, ana.delta
    assumptions: list[str] = []
    out: list[DriverFinding] = []
    # ---- layer 4: drivers --------------------------------------------------------------
    # A daily causal estimate explains a short event against normal days; it does
    # not apportion a whole period against another, so period comparisons stop at
    # what changed and where (decomposition and segments).
    candidates = [] if baseline else list(inp.candidate_drivers)
    if baseline and inp.candidate_drivers:
        assumptions.append(
            "Upstream drivers are estimated for short events only; a period comparison is explained by its "
            "components and segments."
        )
    excluded: dict[str, str] = {}
    desc = descendants(y, inp.lineage)
    identity_factors = {m for m, _ in identity.factors} if identity else set()
    identity_factors |= {t.metric for t in what.bridge}
    for c in candidates:
        if c == y:
            excluded[c] = "is the outcome itself"
        elif c in desc:
            excluded[c] = "is computed from the outcome (post-outcome variable), so it cannot cause it"
        elif c in identity_factors:
            excluded[c] = "is an arithmetic component of the outcome (see decomposition), not an upstream driver"
        elif c not in inp.series or len([d for d in history if _finite(inp.series[c].get(d))]) < cfg.min_observation_rows:
            excluded[c] = "has too little history in the window"
    y_units = meta_y.entity_tokens(inp.scope_tokens, cfg.calendar_grain_tokens)
    for c in candidates:
        if c in excluded:
            continue
        shared_units = y_units & inp.lineage.get(c, MetricMeta(c)).entity_tokens(
            inp.scope_tokens, cfg.calendar_grain_tokens
        )
        if shared_units:
            excluded[c] = (
                f"is measured on the same units as {y} ({', '.join(sorted(shared_units))}), so it describes the "
                "same events rather than causing them — see the decomposition and segments instead"
            )
    drivers = [c for c in candidates if c not in excluded]
    numerator: Series = {}
    if weight_metric and weight_metric in inp.series:
        wser = inp.series[weight_metric]
        numerator = {d: float(ys[d]) * float(wser[d]) for d in ys if _finite(ys.get(d)) and _finite(wser.get(d))}
    # Additive factors of EVERY verified identity, not only the one decomposed: on
    # 2026-09-24 gross sales were decomposed as spend x ROAS, so orders (of
    # orders x AOV) were never compared and funnel purchases — the same orders,
    # counted by the web funnel — was named "the cause" (live MS3-73dac57c40).
    components = list(dict.fromkeys([
        *(t.metric for t in what.decomposition),
        *(m for ident in identities for m, _ in ident.factors),
        *(t.metric for t in what.bridge),
    ]))
    components = [m for m in components if m != y and inp.lineage.get(m, MetricMeta(m)).additive]
    if (unit_count := unit_count_metric(y, inp.lineage)) and unit_count not in components:
        components.append(unit_count)
    for c in list(drivers):
        twin = next(
            (m for m in components if m in inp.series and (rc := _lockstep_correlation(inp.series[c], inp.series[m], history, outliers, same_weekday, cfg=cfg)) is not None
             and abs(rc) >= (cfg.unit_count_r if m == unit_count else cfg.comeasure_r)),
            None,
        )
        if twin is not None:
            excluded[c] = f"counts the same events as {twin}, a component of {y} (lockstep on normal days); not a cause"
            drivers.remove(c)
            continue
        r = _lockstep_correlation(inp.series[c], ys, history, outliers, same_weekday, cfg=cfg)
        rn = _lockstep_correlation(inp.series[c], numerator, history, outliers, same_weekday, cfg=cfg) if numerator else None
        if rn is not None and abs(rn) >= cfg.comeasure_r:
            excluded[c] = (
                f"moves in lockstep with the events {y} counts (rate × {weight_metric}, r={rn:.2f}); it measures "
                "the result, not a cause"
            )
            drivers.remove(c)
            continue
        if r is not None and abs(r) >= cfg.comeasure_r:
            excluded[c] = (
                f"moves in lockstep with {y} even on normal days (r={r:.2f}), i.e. it measures the same events; "
                "not an upstream cause"
            )
            drivers.remove(c)
    # Same quantity measured elsewhere (ratio to the outcome nearly constant).
    for c in list(drivers):
        ratios = [
            float(inp.series[c][d]) / float(ys[d])
            for d in history if _finite(inp.series[c].get(d)) and _finite(ys.get(d)) and float(ys[d]) != 0
        ]
        if len(ratios) >= cfg.min_observation_rows:
            m = _mean(ratios)
            sd = math.sqrt(_mean([(r - m) ** 2 for r in ratios]))
            if abs(m) > 1e-12 and sd / abs(m) < cfg.comeasure_cv:
                excluded[c] = "moves in fixed proportion to the outcome (same quantity measured another way), not a cause"
                drivers.remove(c)
    for c, why in excluded.items():
        out.append(DriverFinding(driver=c, classification=None, status="excluded", reason=why))

    # Inseparable (highly correlated after calendar adjustment) candidate groups.
    clusters: dict[str, list[str]] = {c: [] for c in drivers}
    for a, b in combinations(drivers, 2):
        r = _residual_correlation(inp.series[a], inp.series[b], history, cfg=cfg)
        if r is not None and abs(r) >= cfg.treatment_cluster_r:
            clusters[a].append(b)
            clusters[b].append(a)

    implicated: list[DriverFinding] = []
    pending: list[tuple[int, _PendingDriver]] = []

    def finish(pend: _PendingDriver, est: TimeSeriesEffect) -> DriverFinding:
        c, moved, x_z, x_ev, x_rf, dx = pend.driver, pend.moved, pend.x_z, pend.x_ev, pend.x_rf, pend.dx
        driver_finding = DriverFinding(
            driver=c, classification=None, status="insufficient_evidence", reason=est.reason,
            n_rows=est.n_rows, estimator=f"DoWhy {est.estimator} on an explicit DAG; {est.uncertainty_method} interval",
            naive_association=est.naive_association, inseparable_from=sorted(clusters[c]),
        )
        if est.status != "estimated" or est.effect is None:
            driver_finding.classification = "insufficient_evidence"
            return driver_finding
        beta = est.effect
        lo, hi = est.confidence_interval
        contribution = beta * dx
        c_lo, c_hi = sorted((lo * dx, hi * dx))
        aligned = _sign(contribution) == _sign(delta) and _sign(delta) != 0
        cls, why = _classify_driver(est, moved, aligned, cfg=cfg)
        # classification = what the history says about X -> Y in general;
        # status = whether X explains THIS event (it must have moved, the right way).
        status: Literal["implicated", "ruled_out"] = "implicated"
        lo_, hi_ = est.confidence_interval
        if not (lo_ > 0 or hi_ < 0):
            status = "ruled_out"  # no adjusted effect: the reason already says so
            driver_finding.ruled_out_because = "no_effect"
        elif not moved:
            status = "ruled_out"
            driver_finding.ruled_out_because = "did_not_move"
            why = f"{c} did not move unusually on or just before the event" + (f" (z={x_z:.1f})" if x_z is not None else "") + f"; {why}"
        elif not aligned:
            status = "ruled_out"
            driver_finding.ruled_out_because = "wrong_direction"
            why = f"{c} moved in the direction that would push {y} the other way; {why}"
        driver_finding.classification = cls
        driver_finding.status = status
        driver_finding.reason = why
        driver_finding.effect_per_unit = beta
        driver_finding.effect_ci = [lo, hi]
        driver_finding.p_value = est.p_value
        driver_finding.driver_event = x_ev
        driver_finding.driver_reference = x_rf
        driver_finding.driver_delta = dx
        driver_finding.driver_z = x_z
        driver_finding.driver_moved_before_or_at_event = moved
        driver_finding.contribution = contribution
        driver_finding.contribution_ci = [c_lo, c_hi]
        # The model is daily: additive outcomes sum the event days, rates average them.
        driver_finding.share_of_change = contribution / delta if abs(delta) > 1e-12 else None
        driver_finding.causal_path = f"{c} → {y}"
        driver_finding.adjustment_set = est.adjustment_set
        driver_finding.graph_edges = [list(e) for e in est.graph_edges]
        driver_finding.identified_estimand = est.identified_estimand
        driver_finding.direction_evidence = est.temporal
        driver_finding.refutations = est.refutations
        driver_finding.assumptions = [
            f"No unmeasured same-day common cause of {c} and {y} beyond day-of-week, trend and yesterday's values.",
            f"{y} does not cause {c} within the same day.",
            "Effect is linear in the range observed and stable over the history window.",
        ]
        return driver_finding

    for c in drivers:
        xs = inp.series[c]
        others = {o: inp.series[o] for o in drivers if o != c and o not in clusters[c]}
        # How far did the driver itself move at the event, against its own reference?
        x_meta = inp.lineage.get(c, MetricMeta(c))
        x_prof = seasonal_profile(xs, history, n_ev, additive=x_meta.additive, cfg=cfg)
        if x_prof is None:
            # Without its own normal range there is no telling whether it moved.
            out.append(DriverFinding(
                driver=c, classification="insufficient_evidence", status="insufficient_evidence",
                reason=f"{c} has too little history to know its normal day-to-day range",
            ))
            continue
        x_window = window
        if x_prof is not None:
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
                per_day_dx = []
                break
            per_day_dx.append(xv - xr)
        if not per_day_dx:
            out.append(DriverFinding(
                driver=c, classification="insufficient_evidence", status="insufficient_evidence",
                reason=f"{c} has no value on the event day(s), or too little history for a reference",
            ))
            continue
        dx = sum(per_day_dx) if meta_y.additive else _mean(per_day_dx)
        x_ev, x_rf = (_additive_window(xs, x_window, missing_as_zero=False) if x_meta.additive else _daily_window(xs, x_window))
        x_z = None
        if x_prof is not None and x_rf is not None and x_ev is not None:
            e = _err(x_ev, x_rf, x_prof.use_log, x_prof.offset * (n_ev if x_meta.additive else 1))
            x_z = x_prof.z(e) if e is not None else None
        # Did the driver move on the event day or the day before (cause precedes effect)?
        lead_moves: list[float] = []
        if x_prof is not None:
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
        # Moved = beyond one typical deviation on the event day or the day before,
        # in the same direction as the shift that would explain the event.
        moved = any(abs(v) >= 1.0 and _sign(v) == _sign(dx) for v in lead_moves) or (
            x_z is not None and abs(x_z) >= 1.0 and _sign(x_z) == _sign(dx)
        )
        if not moved:
            # A driver that did not move cannot explain this event, whatever its
            # effect in general — skip the (expensive) estimation and say so.
            out.append(DriverFinding(
                driver=c, classification=None, status="ruled_out", ruled_out_because="did_not_move",
                reason=(
                    f"{c} did not move unusually on or just before the event"
                    + (f" (z={x_z:.1f})" if x_z is not None else "")
                    + ", so it cannot explain it"
                ),
                driver_event=x_ev, driver_reference=x_rf, driver_delta=dx, driver_z=x_z,
                driver_moved_before_or_at_event=False,
            ))
            continue
        pending.append((len(out), _PendingDriver(
            driver=c, moved=moved, x_z=x_z, x_ev=x_ev, x_rf=x_rf, dx=dx,
            kwargs={
                "outcome": ys, "treatment": xs, "others": others, "days": history,
                "treatment_name": c, "outcome_name": y, "alpha": cfg.alpha,
                "refuter_tolerance": cfg.refuter_tolerance,
                "min_rows_per_covariate": cfg.min_rows_per_covariate,
                "min_rows": cfg.min_observation_rows, "placebo_shifts": cfg.placebo_shifts,
            },
        )))
        # Holds this driver's place in the report until the estimates, run side by side below, are in.
        out.append(DriverFinding(driver=c, classification=None, status="insufficient_evidence", reason=""))

    for (slot, pend), est in zip(pending, estimator.estimate_many([p.kwargs for _, p in pending]), strict=True):
        for failure in est.failures:  # a step inside the estimate failed and was worked around; the trace says so
            report_recorded_failure(
                f"estimate:{failure['site']}", failure["error_type"], failure["message"], failure["traceback"],
                {"treatment": est.treatment, "outcome": est.outcome},
            )
        found = finish(pend, est)
        out[slot] = found
        if found.status == "implicated" and found.classification in ("supported_cause", "likely_contributor"):
            implicated.append(found)

    rank = {"supported_cause": 0, "likely_contributor": 1, "correlation": 2, "insufficient_evidence": 3, None: 4}
    status_rank = {"implicated": 0, "ruled_out": 1, "insufficient_evidence": 2, "excluded": 3}
    out.sort(key=lambda f: (status_rank[f.status], rank[f.classification], -abs(f.share_of_change or 0.0)))
    return WhyResult(drivers=out, implicated=implicated, assumptions=assumptions)
