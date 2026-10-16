"""WHERE: where the change sat (plan P2.2). Segment decomposition over the catalogue's dimensions: additive metrics split
exactly, a rate splits into a mix effect and a within-segment rate effect (where Simpson's paradox shows). Each
dimension's specificity is compared with what a normal day shows. Evidence basis: statistical. Failure modes: a
dimension that cannot be split or whose segments do not cover the whole is reported as a slice, not a finding."""

from __future__ import annotations

from seleric_swarm.diagnosis.domain.analysis import EventAnalysis
from seleric_swarm.diagnosis.domain.config import DiagnosisConfig
from seleric_swarm.diagnosis.domain.report import DimensionFinding
from seleric_swarm.diagnosis.operators.where_dimensions import (
    _analyse_additive_dimension,
    _covers_whole,
)
from seleric_swarm.diagnosis.operators.where_rate import (
    _analyse_rate_dimension,
    _analyse_ratio_dimension,
)


def run_where(ana: EventAnalysis, *, cfg: DiagnosisConfig) -> list[DimensionFinding]:
    inp, y, ys, baseline, meta_y = ana.inp, ana.y, ana.ys, ana.baseline, ana.meta_y
    weight_metric, window, same_weekday, y_rf, delta = ana.weight_metric, ana.window, ana.same_weekday, ana.y_rf, ana.delta
    # ---- layer 3: localisation ------------------------------------------------------
    dims: list[DimensionFinding] = []
    for dim, seg in (inp.segments.get(y) or {}).items():
        finding: DimensionFinding | None = None
        if meta_y.additive:
            finding = _analyse_additive_dimension(dim, seg, window, delta, y_rf, same_weekday, cfg=cfg)
        elif weight_metric and dim in (inp.segments.get(weight_metric) or {}):
            finding = _analyse_rate_dimension(
                dim, seg, inp.segments[weight_metric][dim], window, ys, inp.series.get(weight_metric), cfg=cfg
            )
            num_metric = inp.numerators.get(y)
            if finding is None and num_metric and dim in (inp.segments.get(num_metric) or {}):
                finding = _analyse_ratio_dimension(
                    dim, inp.segments[num_metric][dim], inp.segments[weight_metric][dim], window, ys,
                    inp.series.get(weight_metric), cfg=cfg,
                )
                if finding is not None:
                    finding.numerator, finding.denominator = num_metric, weight_metric
        if finding is not None:
            dims.append(finding)
    # Dimensions whose segments do not add back up to the whole (attribution
    # fields populated for a subset of rows) describe a slice, not the change.
    full = [f for f in dims if _covers_whole(f, cfg=cfg)]
    partial = [f for f in dims if f not in full]
    for f in partial:
        f.note = (f.note + "; " if f.note else "") + (
            f"its segments cover only {f.volume_coverage:.0%} of {y}, so it describes a slice of the change"
            if (f.volume_coverage or 0.0) < 1.0 else
            f"its segments add up to {f.volume_coverage:.0%} of {y} (units counted more than once), so its "
            "shares are not shares of the change"
        )
    if not baseline:
        # A short event leads with where it concentrated; a period comparison keeps
        # the planned order (the business hierarchy, coarsest first) so the
        # difference is walked top-down.
        full.sort(key=lambda f: (not f.simpsons_paradox, not f.localised, f.broad_based, -abs(f.hot_share or 0.0)))
    partial.sort(key=lambda f: (not f.localised, -abs(f.hot_share or 0.0)))
    dims = full + partial
    return dims
