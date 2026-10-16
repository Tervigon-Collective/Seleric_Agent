"""The terms of WHAT: the Shapley decomposition of the change over a verified identity, the next-link rate x base chain
and the additive bridge, each with its share of the change (moved from ``causal/diagnosis.py``, plan P2.2)."""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import date

from seleric_swarm.diagnosis.detect.reference import Referencer
from seleric_swarm.diagnosis.detect.seasonal import (
    _PERIOD_KIND,
    SeasonalProfile,
    _reference_kind,
    seasonal_profile,
)
from seleric_swarm.diagnosis.detect.window import _additive_window, _daily_window, _ref_value
from seleric_swarm.diagnosis.domain.config import DiagnosisConfig
from seleric_swarm.diagnosis.domain.identity import Identity
from seleric_swarm.diagnosis.domain.inputs import DiagnosisInput
from seleric_swarm.diagnosis.domain.lineage import MetricMeta
from seleric_swarm.diagnosis.domain.numeric import Series, _finite
from seleric_swarm.diagnosis.domain.report import BridgeTerm, DecompositionTerm
from seleric_swarm.diagnosis.domain.window import EventWindow
from seleric_swarm.diagnosis.operators.bridge_search import discover_bridge_tree, shapley


def _linked_outliers(inp: DiagnosisInput, window: EventWindow, metrics: list[str], *, cfg: DiagnosisConfig) -> set[date]:
    """Abnormal days of the outcome plus every linked metric."""
    out = set(window.excluded_outliers)
    for m in metrics:
        meta = inp.lineage.get(m, MetricMeta(m))
        prof = seasonal_profile(inp.series.get(m, {}), window.history_days, len(window.event_days), additive=meta.additive, cfg=cfg)
        if prof is not None:
            out |= prof.outliers
    return out


def _additive_factor_values(
    inp: DiagnosisInput, window: EventWindow, identity: Identity, linked: set[date] | None = None, *, cfg: DiagnosisConfig,
) -> dict[str, tuple[float | None, float | None]]:
    """(event, reference) per ADDITIVE identity factor, each on its own
    level-adjusted reference with the linked metrics' abnormal days excluded."""
    if linked is None:
        linked = _linked_outliers(inp, window, [m for m, _ in identity.factors], cfg=cfg)
    out: dict[str, tuple[float | None, float | None]] = {}
    for m, _ in identity.factors:
        if not inp.lineage.get(m, MetricMeta(m)).additive:
            continue
        own, _ = _own_window(
            inp.series.get(m, {}), window.history_days, window.event_days, additive=True,
            kind=window.reference_kind, extra_outliers=linked, cfg=cfg,
        )
        out[m] = _additive_window(
            inp.series.get(m, {}), own or window, missing_as_zero=window.reference_kind == _PERIOD_KIND
        )
    return out


def _decompose(
    inp: DiagnosisInput, window: EventWindow, identity: Identity, y_ev: float, y_rf: float, *, cfg: DiagnosisConfig
) -> list[DecompositionTerm]:
    evs: list[float | None] = []
    rfs: list[float | None] = []
    # Each volume on its OWN level-adjusted reference; the per-unit factor is
    # then solved from the identity, so the split stays exact.
    additive_vals = _additive_factor_values(inp, window, identity, cfg=cfg)
    for m, _ in identity.factors:
        meta = inp.lineage.get(m, MetricMeta(m))
        if meta.additive:
            e, r = additive_vals[m]
        else:
            e, r = None, None  # solved below from the identity
        evs.append(e)
        rfs.append(r)
    unknown = [i for i, v in enumerate(evs) if v is None]
    if len(unknown) == 1:
        # Solve the one non-additive factor from the identity so the split stays
        # exact (mean of daily ratios != ratio of the summed components).
        i = unknown[0]
        e_i = identity.factors[i][1]

        def solve(y: float, xs: list[float | None]) -> float | None:
            rest = identity.k
            for j, ((_, e), x) in enumerate(zip(identity.factors, xs, strict=True)):
                if j == i:
                    continue
                if x is None or x <= 0:
                    return None
                rest *= x**e
            if rest == 0 or y <= 0:
                return None
            return float((y / rest) ** (1.0 / e_i))

        evs[i] = solve(y_ev, evs)
        rfs[i] = solve(y_rf, rfs)
    elif unknown and len(window.event_days) == 1:
        d = window.event_days[0]
        for i in unknown:
            m = identity.factors[i][0]
            evs[i] = inp.series.get(m, {}).get(d)
            rfs[i] = _ref_value(inp.series.get(m, {}), window.reference[d], window.factors.get(d))
    if any(v is None or not _finite(v) for v in (*evs, *rfs)):
        return []
    x0 = [float(v) for v in rfs]  # type: ignore[arg-type]
    x1 = [float(v) for v in evs]  # type: ignore[arg-type]
    try:
        contrib = shapley(identity.value, x0, x1)
    except (ZeroDivisionError, ValueError, OverflowError):
        return []
    if not all(math.isfinite(c) for c in contrib):
        return []
    total = identity.value(x1) - identity.value(x0)
    return [
        DecompositionTerm(
            metric=m, exponent=e, event=x1[i], reference=x0[i], contribution=contrib[i],
            share_of_change=(contrib[i] / total) if abs(total) > 1e-12 else None,
        )
        for i, (m, e) in enumerate(identity.factors)
    ]


def _own_window(
    series: Series, history: list[date], event_days: list[date], *, additive: bool, kind: str,
    extra_outliers: frozenset[date] | set[date] = frozenset(), cfg: DiagnosisConfig,
) -> tuple[EventWindow | None, SeasonalProfile | None]:
    """An event window referenced on a series' OWN level-adjusted references.

    ``extra_outliers``: days abnormal for a linked metric (the outcome or a
    partner component describe the same events), excluded here too.
    A period comparison has one shared reference (the comparison period), so
    every series uses the caller's window there (None = use it).
    """
    if kind == _PERIOD_KIND:
        return None, None
    prof = seasonal_profile(series, history, len(event_days), additive=additive, cfg=cfg)
    if prof is None:
        return None, None
    if extra_outliers:
        prof.outliers = set(prof.outliers) | set(extra_outliers)
        prof.referencer = Referencer(series, history, prof.outliers, prof.same_weekday, prof.referencer.weeks)
    refs = {d: prof.referencer.refs(d) for d in event_days}
    if not all(len(v) >= 2 for v in refs.values()):
        return None, prof
    fac: dict[date, dict[date, float]] = {}
    start = min(event_days)
    for d in event_days:
        refs[d], fac[d] = prof.referencer.trimmed(start, refs[d])
    return EventWindow(event_days, history, refs, sorted(prof.outliers), _reference_kind(prof), fac), prof


def _chain_step(
    inp: DiagnosisInput, window: EventWindow, ident: Identity, f_ev: float, f_rf: float, *, cfg: DiagnosisConfig,
) -> list[DecompositionTerm]:
    """Shapley split of an additive metric's change over ``k x rate x base``.

    The base is additive (summed); the rate is the *effective* rate implied by
    the identity (metric / (k x base)), so the split is exact for the metric's
    own change — the approximation lives only in reading that effective rate
    as the catalogue rate, which the report says.
    """
    (rate, _), (base, _) = ident.factors
    linked = _linked_outliers(inp, window, [ident.outcome, rate, base], cfg=cfg)
    own, _ = _own_window(
        inp.series.get(base, {}), window.history_days, window.event_days, additive=True,
        kind=window.reference_kind, extra_outliers=linked, cfg=cfg,
    )
    b_ev, b_rf = _additive_window(
        inp.series.get(base, {}), own or window, missing_as_zero=window.reference_kind == _PERIOD_KIND
    )
    if b_ev is None or b_rf is None or b_ev <= 0 or b_rf <= 0:
        return []
    r_ev, r_rf = f_ev / (ident.k * b_ev), f_rf / (ident.k * b_rf)

    def f(x: list[float]) -> float:
        return ident.k * x[0] * x[1]

    parts = shapley(f, [r_rf, b_rf], [r_ev, b_ev])
    total = f_ev - f_rf
    # Show the catalogue rate as observed (its own reference); the split above
    # used the rate implied by the identity, which differs only by the
    # cross-system approximation.
    r_win, _ = _own_window(
        inp.series.get(rate, {}), window.history_days, window.event_days, additive=False,
        kind=window.reference_kind, extra_outliers=linked, cfg=cfg,
    )
    obs_ev, obs_rf = _daily_window(inp.series.get(rate, {}), r_win or window)
    if obs_ev is not None and obs_rf is not None:
        r_ev, r_rf = obs_ev, obs_rf
    return [
        DecompositionTerm(metric=m, exponent=1.0, event=e, reference=r, contribution=c,
                          share_of_change=(c / total) if abs(total) > 1e-12 else None, basis="approximate identity")
        for m, e, r, c in ((rate, r_ev, r_rf, parts[0]), (base, b_ev, b_rf, parts[1]))
    ]


def _bridge(
    inp: DiagnosisInput, window: EventWindow, history: list[date], prev_days: list[date], total: float,
    total_vs_previous: float | None, *, cfg: DiagnosisConfig, clock: Callable[[], float],
) -> tuple[list[BridgeTerm], list[str], float | None, float | None]:
    """The exact additive bridge, every level, against the reference and the day(s) before."""
    tree = discover_bridge_tree(inp.outcome, inp.bridge_candidates, inp.series, history, cfg=cfg, clock=clock)
    if inp.outcome not in tree:
        return [], [], None, None
    terms: list[BridgeTerm] = []

    def walk(node: str, depth: int, outer: float) -> None:
        for m, sign in tree.get(node, []):
            ser = inp.series.get(m, {})
            ev, rf = _additive_window(ser, window, missing_as_zero=True)
            if ev is None or rf is None:
                continue
            prev = sum(float(ser[d]) if _finite(ser.get(d)) else 0.0 for d in prev_days)
            ev_days = sum(float(ser[d]) if _finite(ser.get(d)) else 0.0 for d in window.event_days)
            effective = outer * sign
            terms.append(BridgeTerm(
                metric=m, parent=node, sign=sign, depth=depth, event=ev, reference=rf,
                contribution=sign * (ev - rf),
                share_of_change=(effective * (ev - rf) / total) if abs(total) > 1e-12 else None,
                previous=prev, contribution_vs_previous=sign * (ev_days - prev),
            ))
            walk(m, depth + 1, effective)

    walk(inp.outcome, 1, 1.0)
    top = [t for t in terms if t.depth == 1]
    residual = total - sum(t.contribution for t in top)
    residual_prev = (
        total_vs_previous - sum(t.contribution_vs_previous or 0.0 for t in top)
        if total_vs_previous is not None else None
    )
    described = [
        f"{node} = " + " ".join(
            f"{'' if i == 0 and sign > 0 else ('+ ' if sign > 0 else '− ')}{m}" for i, (m, sign) in enumerate(parts)
        )
        for node, parts in tree.items()
    ]
    return terms, described, residual, residual_prev
