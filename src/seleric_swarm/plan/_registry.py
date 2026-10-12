"""The template registry (M2.1): the first template that matches the prepared spec builds the plan.

Failure policy (A4): no template, or a template that cannot build, routes the question to the
agent loop (``needs_agent=True``) so open questions stay possible.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from seleric_swarm.contracts.pipeline import AnalysisPlan, QuestionSpec
from seleric_swarm.plan._templates import (
    BreakdownTemplate,
    CompositionTemplate,
    EntityComparisonTemplate,
    ExplorationTemplate,
    FunnelTemplate,
    LookupTemplate,
    PeriodComparisonTemplate,
    PlanContext,
    RankingTemplate,
    Template,
    TrendTemplate,
    WhyTemplate,
    prepare,
)
from seleric_swarm.semantic import SemanticReadModel

AGENT_TEMPLATE_ID = "agent"

# Order matters only where two templates could match: exploration is decided by kind, before shapes.
TEMPLATES: tuple[Template, ...] = (
    ExplorationTemplate(),
    WhyTemplate(),
    CompositionTemplate(),
    FunnelTemplate(),
    RankingTemplate(),
    EntityComparisonTemplate(),
    PeriodComparisonTemplate(),
    TrendTemplate(),
    BreakdownTemplate(),
    LookupTemplate(),
)


def plan(
    spec: QuestionSpec,
    read_model: SemanticReadModel,
    *,
    as_of: date | None = None,
    headline_metric_ids: Sequence[str] = (),
) -> AnalysisPlan:
    """The plan for ``spec``; ``needs_agent=True`` (no steps) when no template fits."""
    ctx = PlanContext(read_model=read_model, as_of=as_of, headline_metric_ids=tuple(headline_metric_ids))
    prepared = prepare(spec, ctx)
    for template in TEMPLATES:
        if not template.matches(prepared):
            continue
        built = template.build(prepared, ctx)
        if built is not None:
            return built
    return AnalysisPlan(template_id=AGENT_TEMPLATE_ID, steps=(), needs_agent=True)


def plan_notes(
    spec: QuestionSpec,
    read_model: SemanticReadModel,
    *,
    as_of: date | None = None,
    headline_metric_ids: Sequence[str] = (),
) -> tuple[str, ...]:
    """The corrections the planner made to ``spec`` (for the stage span and the agent's advisory)."""
    ctx = PlanContext(read_model=read_model, as_of=as_of, headline_metric_ids=tuple(headline_metric_ids))
    return prepare(spec, ctx).notes


def template_ids() -> tuple[str, ...]:
    """The shapes the registry can plan (``QuestionSpec.shape`` values come from here)."""
    return tuple(t.id for t in TEMPLATES)
