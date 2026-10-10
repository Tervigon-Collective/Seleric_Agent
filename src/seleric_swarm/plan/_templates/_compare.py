"""Two-window templates: period comparison and entity comparison (rank, then compare)."""

from __future__ import annotations

from seleric_swarm.contracts.pipeline import AnalysisPlan, RankingSpec
from seleric_swarm.plan._templates._base import (
    PlanContext,
    Prepared,
    Role,
    Steps,
    carried,
    carried_reading,
    change_params,
    elapsed,
    is_analysis,
)

_ROLES: tuple[Role, Role] = ("baseline", "event")


def _compare(
    steps: Steps,
    prepared: Prepared,
    ctx: PlanContext,
    metric_id: str,
    *,
    breakdowns: tuple[str, ...] = (),
    entities_from_step: str | None = None,
) -> None:
    """The metric over both windows, then its change (baseline → event)."""
    ids = [
        steps.query(
            metric_id,
            role=role,
            filters=prepared.spec.filters,
            breakdowns=breakdowns,
            entities_from_step=entities_from_step,
            elapsed_only=elapsed(prepared, ctx.read_model, metric_id),
        )
        for role in _ROLES
    ]
    params = change_params(prepared)
    steps.derive("delta", ids, **params)
    steps.derive("pct_change", ids, **params)


class PeriodComparisonTemplate:
    id = "period_comparison"

    def matches(self, prepared: Prepared) -> bool:
        return (
            is_analysis(prepared)
            and prepared.shape == "period_comparison"
            and prepared.compares_periods
            and bool(prepared.metrics)
        )

    def build(self, prepared: Prepared, ctx: PlanContext) -> AnalysisPlan | None:
        model = ctx.read_model
        steps = Steps()
        for metric_id in prepared.metrics:
            _compare(steps, prepared, ctx, metric_id)
        # The breakdowns asked for map the event window's amounts (each by what its metric carries).
        if prepared.breakdowns:
            for metric_id in prepared.metrics:
                sliced = carried(model, metric_id, prepared.breakdowns)
                if sliced and model.aggregation_for(metric_id) == "additive":
                    steps.query(metric_id, role="event", breakdowns=sliced, filters=prepared.spec.filters)
        return steps.plan(self.id)


class EntityComparisonTemplate:
    id = "entity_comparison"

    def matches(self, prepared: Prepared) -> bool:
        return (
            is_analysis(prepared)
            and prepared.shape == "entity_comparison"
            and prepared.entity is not None
            and prepared.compares_periods
            and bool(prepared.ranking or prepared.metrics)
        )

    def build(self, prepared: Prepared, ctx: PlanContext) -> AnalysisPlan | None:
        model = ctx.read_model
        entity = prepared.entity
        assert entity is not None  # matches() checked
        ranking = prepared.ranking or RankingSpec(by_metric_id=prepared.metrics[0], order="desc", limit=10)
        rank = ranking.by_metric_id
        steps = Steps()
        rank_step = steps.query(
            rank, role="event", breakdowns=(entity,), filters=prepared.spec.filters, ranking=ranking,
            step_id="rank",
        )
        # Every other entity kind the user listed is its own ranking, never one cross-product
        # query (regression 2026-10-10 Q16): a family member the rank metric carries stands in.
        for other in prepared.breakdowns:
            if other == entity:
                continue
            reading = carried_reading(model, rank, other)
            if reading is not None:
                steps.query(rank, role="event", breakdowns=(reading,), filters=prepared.spec.filters, ranking=ranking)
        for metric_id in dict.fromkeys([rank, *prepared.metrics]):
            if not model.carries(metric_id, entity):
                continue
            _compare(steps, prepared, ctx, metric_id, breakdowns=(entity,), entities_from_step=rank_step)
        return steps.plan(self.id)
