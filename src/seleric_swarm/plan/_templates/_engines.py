"""Templates handed to an engine: composition (bridge), why (investigate), exploration (explore)."""

from __future__ import annotations

from pydantic import JsonValue

from seleric_swarm.contracts.pipeline import AnalysisPlan
from seleric_swarm.plan._templates._base import (
    PlanContext,
    Prepared,
    Steps,
    filters_json,
    is_analysis,
    windows_json,
)


def _scope(prepared: Prepared) -> dict[str, JsonValue]:
    return {
        "windows": windows_json(prepared),
        "filters": filters_json(prepared),
        "breakdowns": list(prepared.breakdowns),
    }


class CompositionTemplate:
    """A total split into the lines the catalogue composes it from, over one or two windows."""

    id = "composition"

    def matches(self, prepared: Prepared) -> bool:
        return is_analysis(prepared) and prepared.shape == "composition" and bool(prepared.metrics)

    def build(self, prepared: Prepared, ctx: PlanContext) -> AnalysisPlan | None:
        del ctx
        steps = Steps()
        steps.engine("composition", {"metric_id": prepared.metrics[0], **_scope(prepared)})
        return steps.plan(self.id)


class WhyTemplate:
    """Diagnose one outcome; the other named measures are the user's hypotheses (drivers)."""

    id = "why"

    def matches(self, prepared: Prepared) -> bool:
        return is_analysis(prepared) and prepared.shape == "why_single_metric" and bool(prepared.metrics)

    def build(self, prepared: Prepared, ctx: PlanContext) -> AnalysisPlan | None:
        del ctx
        outcome, *drivers = prepared.metrics
        steps = Steps()
        steps.engine(
            "investigate",
            {
                "metric_id": outcome,
                "drivers": list(drivers),
                "direction": prepared.spec.direction,
                **_scope(prepared),
            },
        )
        return steps.plan(self.id)


class ExplorationTemplate:
    id = "exploration"

    def matches(self, prepared: Prepared) -> bool:
        return prepared.spec.kind == "exploration"

    def build(self, prepared: Prepared, ctx: PlanContext) -> AnalysisPlan | None:
        del ctx
        steps = Steps()
        steps.engine("explore", {"metric_ids": list(prepared.metrics), **_scope(prepared)})
        return steps.plan(self.id)
