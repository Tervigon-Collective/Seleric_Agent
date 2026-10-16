"""The event and reference values IS_REAL compares, exact whenever lineage allows (moved from ``causal/diagnosis.py``,
plan P2.2)."""

from __future__ import annotations

from seleric_swarm.diagnosis.detect.seasonal import _PERIOD_KIND
from seleric_swarm.diagnosis.detect.window import _additive_window, _daily_window
from seleric_swarm.diagnosis.domain.config import DiagnosisConfig
from seleric_swarm.diagnosis.domain.identity import Identity
from seleric_swarm.diagnosis.domain.inputs import DiagnosisInput
from seleric_swarm.diagnosis.domain.lineage import MetricMeta
from seleric_swarm.diagnosis.domain.window import EventWindow
from seleric_swarm.diagnosis.operators.what_terms import _additive_factor_values


def _event_value(
    metric: str, inp: DiagnosisInput, window: EventWindow, identity: Identity | None, *, cfg: DiagnosisConfig
) -> tuple[float | None, float | None, str]:
    """(event, reference, note) for a metric, exact whenever lineage allows it."""
    meta = inp.lineage.get(metric)
    series = inp.series.get(metric, {})
    if meta is None or meta.additive:
        ev, rf = _additive_window(series, window, missing_as_zero=window.reference_kind == _PERIOD_KIND)
        return ev, rf, ""
    if identity is not None and all(inp.lineage.get(m, MetricMeta(m)).additive for m, _ in identity.factors):
        vals = _additive_factor_values(inp, window, identity, cfg=cfg)
        evs = [vals[m][0] for m, _ in identity.factors]
        rfs = [vals[m][1] for m, _ in identity.factors]
        evs_f = [v for v in evs if v is not None]
        rfs_f = [v for v in rfs if v is not None]
        if len(evs_f) == len(evs) and len(rfs_f) == len(rfs):
            return identity.value(evs_f), identity.value(rfs_f), f"recomputed from {identity.describe()}"
    ev, rf = _daily_window(series, window)
    note = "" if len(window.event_days) == 1 else "multi-day rate taken as the mean of daily values (approximate)"
    return ev, rf, note
