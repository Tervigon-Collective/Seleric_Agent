"""Plan templates (M2.1). Private to ``seleric_swarm.plan``; the registry orders them."""

from seleric_swarm.plan._templates._base import PlanContext, Prepared, Template, prepare
from seleric_swarm.plan._templates._compare import (
    EntityComparisonTemplate,
    PeriodComparisonTemplate,
)
from seleric_swarm.plan._templates._engines import (
    CompositionTemplate,
    ExplorationTemplate,
    WhyTemplate,
)
from seleric_swarm.plan._templates._single import (
    BreakdownTemplate,
    FunnelTemplate,
    LookupTemplate,
    TrendTemplate,
)

__all__ = [
    "BreakdownTemplate",
    "CompositionTemplate",
    "EntityComparisonTemplate",
    "ExplorationTemplate",
    "FunnelTemplate",
    "LookupTemplate",
    "PeriodComparisonTemplate",
    "PlanContext",
    "Prepared",
    "Template",
    "TrendTemplate",
    "WhyTemplate",
    "prepare",
]
