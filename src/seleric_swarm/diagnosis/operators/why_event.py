"""WHY_EVENT: which upstream driver moved and explains this event (plan P2.2). Candidates are estimated with DoWhy on an
explicit time-series DAG (calendar and lagged pre-treatment variables, HAC intervals, refuters plus temporal-precedence
tests); contribution = effect x how far the driver itself moved from its own reference. Classification is rule-based and
conservative (supported_cause / likely_contributor / correlation / insufficient_evidence). Evidence basis: causal.
Failure modes: too little history, a driver that did not move (skipped, not estimated), an estimator that failed (the
driver is reported as insufficient evidence, the failure is in the trace)."""

from __future__ import annotations

import math
from collections.abc import Callable
from itertools import combinations
from typing import Literal

from seleric_swarm.diagnosis.domain.analysis import EventAnalysis, WhatResult, WhyResult
from seleric_swarm.diagnosis.domain.config import DiagnosisConfig
from seleric_swarm.diagnosis.domain.estimates import TimeSeriesEffect
from seleric_swarm.diagnosis.domain.inputs import DiagnosisInput
from seleric_swarm.diagnosis.domain.lineage import MetricMeta, descendants
from seleric_swarm.diagnosis.domain.numeric import _finite, _sign
from seleric_swarm.diagnosis.domain.report import DriverFinding, EdgeCheck
from seleric_swarm.diagnosis.domain.subgraph import EdgeSpec
from seleric_swarm.diagnosis.operators.edge_check import verify_edges
from seleric_swarm.diagnosis.operators.measurement import check_measurements
from seleric_swarm.diagnosis.operators.why_movement import (
    NO_HISTORY,
    NO_VALUE,
    Movement,
    measure_movement,
)
from seleric_swarm.diagnosis.operators.why_screen import screen_by_lockstep, screen_by_units
from seleric_swarm.diagnosis.operators.why_terms import (
    _classify_driver,
    _PendingDriver,
    _residual_correlation,
)
from seleric_swarm.diagnosis.ports.estimator import EffectEstimator
from seleric_swarm.diagnosis.trace.active import report_recorded_failure


def _quiet_on_path(
    driver: str, inp: DiagnosisInput, movement_of: Callable[[str], Movement | str]
) -> tuple[str, float | None] | None:
    """The first metric on the driver's path (nearest the driver first) that did not move, with its z; None when every
    metric on it moved or has no verdict (too little history or no value: the benefit of the doubt)."""
    for metric in inp.driver_paths.get(driver, ()):
        if metric not in inp.series:
            continue
        found = movement_of(metric)
        if isinstance(found, Movement) and not found.moved:
            return metric, found.x_z
    return None


def _confirm_candidates(
    drivers: list[str], excluded: dict[str, str], inp: DiagnosisInput, ana: EventAnalysis, cfg: DiagnosisConfig
) -> tuple[list[EdgeCheck], list[str]]:
    """Confirm the reviewed edges under the graph-chosen candidates on the pre-event history (plan D-56). A candidate with an
    edge that is not confirmed is excluded with the reason; the edges that could not be checked are reported once."""
    chains = {c: [EdgeSpec(e[0], e[1], e[2], (int(e[3]), int(e[4])), e[5]) for e in inp.driver_chains[c]] for c in drivers if c in inp.driver_chains}
    if not chains:
        return [], []
    days = [d for d in ana.history if d not in ana.outliers]
    verdicts = verify_edges([e for chain in chains.values() for e in chain], inp.series, days, cfg)
    for c, chain in chains.items():
        failed = next((verdicts[(e.source, e.target)] for e in chain if not verdicts[(e.source, e.target)].usable), None)
        if failed is not None:
            excluded[c] = f"its path to {ana.y} rests on a reviewed edge that could not be confirmed on this brand's history ({failed.describe()})"
    used = {(e.source, e.target) for c, chain in chains.items() for e in chain}
    checks = [
        EdgeCheck(v.source, v.target, v.status, v.describe(), v.lag, v.beta, v.p_adj, v.n_rows)
        for k, v in sorted(verdicts.items()) if k in used and v.status != "definitional"
    ]
    notes = [f"could not be checked on your data: {v.describe()}" for k, v in sorted(verdicts.items()) if k in used and not v.usable]
    return checks, notes


def _within_cap(drivers: list[str], inp: DiagnosisInput, notes: list[str]) -> list[str]:
    """Estimate at most ``estimate_cap`` of the graph-chosen candidates that survived, in the order the graph offered them
    (named drivers and fallback lists are not capped here)."""
    if not inp.estimate_cap or not inp.driver_chains:
        return drivers
    graph_chosen = [c for c in drivers if c in inp.driver_chains]
    keep = set(graph_chosen[: inp.estimate_cap])
    left_out = [c for c in graph_chosen if c not in keep]
    if left_out:
        notes.append(f"{len(left_out)} further candidate cause(s) passed the checks but were not estimated (limit {inp.estimate_cap}): {', '.join(left_out)}")
    return [c for c in drivers if c not in inp.driver_chains or c in keep]


def _name_root_anomaly(
    found: DriverFinding, inp: DiagnosisInput, confirmed: set[str], movement_of: Callable[[str], Movement | str], cfg: DiagnosisConfig
) -> None:
    """Let the reviewed graph supply the direction the history cannot (plan D-60).

    A daily history rarely holds enough abrupt shifts of one driver to show which way an association runs, so an estimated,
    placebo-clean driver stays "correlation: same-day association only". When the driver is a graph-chosen candidate whose
    every reviewed edge to the outcome was confirmed on this brand's history (the direction is the reviewed prior, the
    association and its sign are the data's), it moved abnormally (beyond ``outlier_z``), and nothing upstream of it on a
    confirmed path did, it is the root of the anomaly: a likely contributor, never a supported cause (that label stays
    for direction shown by the data). Anything downstream of an abnormal metric stays an association: the upstream one
    explains it. An upstream metric counts only if its own path was confirmed."""
    chain = inp.driver_chains.get(found.driver)
    refs = {r.get("name"): bool(r.get("passed")) for r in found.refutations}
    lo, hi = (found.effect_ci + [math.nan, math.nan])[:2]
    clean = (
        found.status == "implicated" and found.classification == "correlation" and chain
        and found.direction_evidence.get("direction") == "assumed" and (lo > 0 or hi < 0)
        and refs.get("time_shift_placebo") and refs.get("future_treatment_placebo")
        and found.driver_z is not None and abs(found.driver_z) >= cfg.outlier_z
    )
    if not clean:
        return
    for other, other_chain in inp.driver_chains.items():
        if other in confirmed and other != found.driver and any(found.driver == edge[1] for edge in other_chain) and other in inp.series:
            moved = movement_of(other)
            if isinstance(moved, Movement) and moved.x_z is not None and abs(moved.x_z) >= cfg.outlier_z:
                return
    path = " -> ".join([found.driver, *(edge[1] for edge in chain or [])])
    found.classification = "likely_contributor"
    found.reason = (
        f"the effect is identified and clears the placebo checks; the history alone cannot show which way the association runs, "
        f"the reviewed mechanism ({path}, every step confirmed on this brand's history) gives the direction, "
        f"{found.driver} moved abnormally (z={found.driver_z:.1f}) and nothing upstream of it did"
    )


def run_why_event(ana: EventAnalysis, what: WhatResult, *, cfg: DiagnosisConfig, estimator: EffectEstimator) -> WhyResult:
    inp, y, ys, event_days, n_ev = ana.inp, ana.y, ana.ys, ana.event_days, ana.n_ev
    start, history, baseline, meta_y = ana.start, ana.history, ana.baseline, ana.meta_y
    window, ref_kind = ana.window, ana.ref_kind
    identity, delta = ana.identity, ana.delta
    assumptions: list[str] = []
    out: list[DriverFinding] = []
    event_is_real = ana.unusual or ana.notable or bool(baseline)  # the verdict's own test (report/assemble.py)
    measurement = check_measurements(ana, cfg)
    diverged = next((f for f in measurement if f.status == "diverged" and cfg.measurement_acts), None)
    # No behavioural cause is accepted for a day inside its normal range (a regression finds an association in noise as
    # readily as anywhere), nor while two systems that count the same events disagree (one of them is the suspect).
    no_cause_because = (
        f"{diverged.metric} and {diverged.peer} count the same events and disagree, so their data is in question before any cause"
        if diverged is not None
        else ("" if event_is_real else "the change is within normal day-to-day variation, so no cause is accepted for it")
    )
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
    if not inp.candidates_from_graph:
        screen_by_units(candidates, excluded, ana, cfg)
    drivers = [c for c in candidates if c not in excluded]
    edge_checks, notes = _confirm_candidates(drivers, excluded, inp, ana, cfg)
    drivers = [c for c in drivers if c not in excluded]
    confirmed = {c for c in drivers if c in inp.driver_chains}  # candidates whose whole path the history confirmed
    drivers = _within_cap(drivers, inp, notes)
    if not inp.candidates_from_graph:
        # Candidates the reviewed graph chose are already clear of the metrics that count the same events; for a list
        # chosen from the catalogue's lineage the data have to say so.
        screen_by_lockstep(drivers, excluded, ana, what, cfg)
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
        if no_cause_because and cls in ("supported_cause", "likely_contributor"):
            # Report what the regression found, accept no cause (see `no_cause_because`).
            cls, why = "correlation", f"{why}; {no_cause_because}"
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

    movements: dict[str, Movement | str] = {}

    def movement_of(metric: str) -> Movement | str:
        if metric not in movements:
            movements[metric] = measure_movement(
                inp.series[metric], inp.lineage.get(metric, MetricMeta(metric)), history=history, event_days=event_days,
                start=start, n_ev=n_ev, window=window, ref_kind=ref_kind, outcome_additive=meta_y.additive, cfg=cfg,
            )
        return movements[metric]

    for c in drivers:
        xs = inp.series[c]
        others = {o: inp.series[o] for o in drivers if o != c and o not in clusters[c]}
        # How far did the driver itself move at the event, against its own reference?
        x_meta = inp.lineage.get(c, MetricMeta(c))
        mv = measure_movement(
            xs, x_meta, history=history, event_days=event_days, start=start, n_ev=n_ev, window=window, ref_kind=ref_kind,
            outcome_additive=meta_y.additive, cfg=cfg,
        )
        if mv == NO_HISTORY:
            out.append(DriverFinding(
                driver=c, classification="insufficient_evidence", status="insufficient_evidence",
                reason=f"{c} has too little history to know its normal day-to-day range",
            ))
            continue
        if mv == NO_VALUE:
            out.append(DriverFinding(
                driver=c, classification="insufficient_evidence", status="insufficient_evidence",
                reason=f"{c} has no value on the event day(s), or too little history for a reference",
            ))
            continue
        assert isinstance(mv, Movement)
        dx, x_ev, x_rf, x_z, moved = mv.dx, mv.x_ev, mv.x_rf, mv.x_z, mv.moved
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
        if (quiet := _quiet_on_path(c, inp, movement_of)) is not None:
            quiet_metric, quiet_z = quiet
            out.append(DriverFinding(
                driver=c, classification=None, status="ruled_out", ruled_out_because="path_did_not_move",
                reason=(
                    f"{c} acts on {y} through {', '.join(inp.driver_paths[c])}; {quiet_metric} did not move unusually on or just before "
                    f"the event" + (f" (z={quiet_z:.1f})" if quiet_z is not None else "") + ", so the effect did not travel to it"
                ),
                driver_event=x_ev, driver_reference=x_rf, driver_delta=dx, driver_z=x_z,
                driver_moved_before_or_at_event=True,
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
        if no_cause_because == "":
            _name_root_anomaly(found, inp, confirmed, movement_of, cfg)
        out[slot] = found
        if found.status == "implicated" and found.classification in ("supported_cause", "likely_contributor"):
            implicated.append(found)

    rank = {"supported_cause": 0, "likely_contributor": 1, "correlation": 2, "insufficient_evidence": 3, None: 4}
    status_rank = {"implicated": 0, "ruled_out": 1, "insufficient_evidence": 2, "excluded": 3}
    out.sort(key=lambda f: (status_rank[f.status], rank[f.classification], -abs(f.share_of_change or 0.0)))
    return WhyResult(drivers=out, implicated=implicated, assumptions=assumptions, measurement=measurement, edge_checks=edge_checks, notes=notes)
