"""Screens WHY_EVENT applies to a candidate list that was NOT chosen by the reviewed graph (the catalogue's lineage
fallback): metrics that count the same events as the outcome are not causes of it. With the graph's candidates the
graph's own edges (twin, co-measure, subset, allocation) have already set those aside, and these data heuristics, one of
them built on name tokens, only risk excluding a real upstream cause that moves in step with the outcome."""

from __future__ import annotations

import math

from seleric_swarm.diagnosis.detect.seasonal import _lockstep_correlation
from seleric_swarm.diagnosis.domain.analysis import EventAnalysis, WhatResult
from seleric_swarm.diagnosis.domain.config import DiagnosisConfig
from seleric_swarm.diagnosis.domain.lineage import MetricMeta, unit_count_metric
from seleric_swarm.diagnosis.domain.numeric import Series, _finite, _mean


def screen_by_units(candidates: list[str], excluded: dict[str, str], ana: EventAnalysis, cfg: DiagnosisConfig) -> None:
    """A candidate measured on the same units as the outcome describes the same events (the grain names the unit)."""
    inp, y, meta_y = ana.inp, ana.y, ana.meta_y
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


def screen_by_lockstep(drivers: list[str], excluded: dict[str, str], ana: EventAnalysis, what: WhatResult, cfg: DiagnosisConfig) -> None:
    """A candidate that moves in lockstep with the outcome or one of its components on normal days measures the same
    events. Removes what it excludes from ``drivers`` and records why in ``excluded``."""
    inp, y, ys, history, weight_metric = ana.inp, ana.y, ana.ys, ana.history, ana.weight_metric
    outliers, same_weekday, identities = ana.outliers, ana.same_weekday, ana.identities
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
