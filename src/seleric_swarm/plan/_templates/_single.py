"""One-window templates: lookup, breakdown, trend, funnel."""

from __future__ import annotations

from itertools import pairwise

from seleric_swarm.contracts.pipeline import AnalysisPlan
from seleric_swarm.plan._templates._base import PlanContext, Prepared, Steps, carried, is_analysis

# A trend with no grain named is read day by day (a machine grain, not a catalogue name).
_TREND_GRAIN = "day"


class _PerMetric:
    """One query per metric over the event window, sliced by what each metric carries."""

    id = ""
    shapes: frozenset[str] = frozenset()

    def matches(self, prepared: Prepared) -> bool:
        return is_analysis(prepared) and prepared.shape in self.shapes and bool(prepared.metrics or prepared.ranking)

    def grain(self, prepared: Prepared) -> str | None:
        return prepared.spec.grain

    def build(self, prepared: Prepared, ctx: PlanContext) -> AnalysisPlan | None:
        model = ctx.read_model
        steps = Steps()
        ranking = prepared.ranking
        dims = tuple(dict.fromkeys([*([prepared.entity] if prepared.entity else []), *prepared.breakdowns]))
        metrics = list(prepared.metrics)
        if ranking is not None and ranking.by_metric_id not in metrics:
            metrics.insert(0, ranking.by_metric_id)
        for metric_id in metrics:
            sliced = carried(model, metric_id, dims)
            steps.query(
                metric_id,
                role=prepared.event,
                breakdowns=sliced,
                filters=prepared.spec.filters,
                grain=self.grain(prepared),
                # A ranking only orders a sliced read of the metric it ranks by.
                ranking=ranking if ranking is not None and ranking.by_metric_id == metric_id and sliced else None,
            )
        return steps.plan(self.id)


class LookupTemplate(_PerMetric):
    id = "lookup"
    shapes = frozenset({"lookup"})


class BreakdownTemplate(_PerMetric):
    id = "breakdown"
    shapes = frozenset({"breakdown"})

    def matches(self, prepared: Prepared) -> bool:
        return super().matches(prepared) and bool(prepared.breakdowns or prepared.entity)


class TrendTemplate(_PerMetric):
    id = "trend"
    shapes = frozenset({"trend"})

    def grain(self, prepared: Prepared) -> str | None:
        return prepared.spec.grain or _TREND_GRAIN


class FunnelTemplate:
    """Each named stage over the event window, then each stage's share of the one before."""

    id = "funnel"

    def matches(self, prepared: Prepared) -> bool:
        return is_analysis(prepared) and prepared.shape == "funnel" and len(prepared.metrics) >= 2

    def build(self, prepared: Prepared, ctx: PlanContext) -> AnalysisPlan | None:
        model = ctx.read_model
        steps = Steps()
        stage_ids = [
            steps.query(
                metric_id,
                role=prepared.event,
                breakdowns=carried(model, metric_id, prepared.breakdowns),
                filters=prepared.spec.filters,
                grain=prepared.spec.grain,
            )
            for metric_id in prepared.metrics
        ]
        for before, after in pairwise(stage_ids):
            steps.derive("share", [after, before])
        return steps.plan(self.id)
