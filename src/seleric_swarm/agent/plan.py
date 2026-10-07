"""Plan-first step — one bounded LLM call that drafts a structured plan before
the main agent loop, so the ReAct loop starts in the right direction.

Inputs: the user query, the Jev intent label (``agent/intent.py`` routing
hint), the capability manifest (every tool the agent can call) and the whole
catalogue snapshot. The output is a typed ``MissionPlan``, checked against the
registered tools and the catalogue (metric ids, dimensions, metric × dimension
support): what does not exist is pruned and named, the rest is used. It reaches the mission prompt as an ADVISORY block
that the agent may correct from tool results, and is stored as a ``ui``
artifact for observability.

Why structured (2026-10-07): the free-text plan named valid ids but the wrong
shape. For "the last 3 days' best campaigns versus today", every plan fetched
account totals and none ranked the campaigns (MS3-4c6633d347, MS3-4f7be7ba30);
one sent a why-question to ``diagnose_metric_change`` on a half-finished day.
The shape is now an explicit field, and the rules below are about question
shapes, never about particular metrics.

Fail-open: any error, a plan with no usable step, or the offline stub
``TestModel`` returns no plan and the mission runs exactly as it did before this step existed.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.models.test import TestModel

from seleric_swarm.services.catalogue_bootstrap import CatalogueSnapshot

_log = logging.getLogger("seleric.agent.plan")

_MAX_STEPS = 6

PlanShape = Literal[
    "lookup",
    "trend",
    "breakdown",
    "period_comparison",
    "entity_comparison",
    "why_single_metric",
    "funnel",
    "other",
]


class PlanStep(BaseModel):
    tool: str = Field(description="Exact name of a tool from the capability list.")
    metric_ids: list[str] = Field(
        default_factory=list, description="Exact catalogue metric ids this step reads (one query per id)."
    )
    dimensions: list[str] = Field(
        default_factory=list, description="Catalogue dimension ids the step breaks down or filters by."
    )
    period: str = Field(default="", description="The window this step covers, in words.")
    ranking: str = Field(default="", description="How entities are ranked and how many are kept, if any.")
    uses_entities_from_step: int | None = Field(
        default=None, description="1-based number of an earlier step whose entities this step reuses."
    )
    # No max_length: a long purpose failed schema validation and cost a whole
    # second planner call (live 2026-10-07); it is clipped when rendered instead.
    purpose: str = Field(min_length=3)


class MissionPlan(BaseModel):
    shape: PlanShape
    steps: list[PlanStep] = Field(min_length=1, max_length=_MAX_STEPS)


@dataclass
class PlanOutcome:
    """The validated plan (or None), its prompt rendering, and planner telemetry."""

    plan: MissionPlan | None = None
    text: str | None = None
    stats: dict[str, Any] = field(default_factory=dict)


_PLAN_INSTRUCTIONS = (
    "You are the planning step for the Seleric analytics agent. Given a user question, "
    "its detected intent (a hint that can be wrong), the available tools and the metric "
    "catalogue, return a MissionPlan: the question's shape and 1-6 ordered steps. Use "
    "tool names, metric ids and dimension ids exactly as listed — never invent one. "
    "Do not answer the question or produce numbers.\n\n"
    "Choose the shape from the question itself:\n"
    "- entity_comparison: the question is about particular entities (the leading or "
    "lagging campaigns, ads, products, channels, …) of one window compared with another "
    "window. Plan: (1) rank the entities over the reference window — query_metrics broken "
    "down by the entity's name dimension, ranked by the performance metric the user named "
    "(for a ratio, keep only entities with meaningful volume); (2) fetch the SAME entities "
    "in the comparison window (uses_entities_from_step=1) for every metric the user asked "
    "for; (3) explain each entity's change from its own metrics — delivery and price, "
    "engagement, conversion, value. Never substitute account-wide totals for the entities. "
    "'Best' or 'top performing' means ranked by an outcome or efficiency metric the user "
    "named (what the entity returned), never by what it cost.\n"
    "- period_comparison: whole-account metrics in one window against another.\n"
    "- why_single_metric: why ONE metric's total moved over a COMPLETE window — call "
    "diagnose_metric_change once for it. If the why is about particular entities, or the "
    "window includes today, plan comparison steps instead: the diagnosis needs complete days.\n"
    "- funnel: one base count metric plus the rate metrics that divide by it, on the same "
    "view and grain, then funnel_decomposition with their evidence.\n"
    "- lookup / trend / breakdown / other: the fewest steps that answer it.\n\n"
    "Today is still in progress. When a window includes today, comparisons of additive "
    "metrics must use query_metrics with elapsed_only=True on every compared window and "
    "compare per-day figures, never a multi-day total against one day.\n\n"
    "Each listed metric id is fetched by its own query_metrics call. If the user names a "
    "measure the catalogue does not have, plan to derive it from the catalogue metrics that "
    "define it and say so in the purpose — never substitute a metric with another definition. "
    "A step's dimensions must be ones its metrics support (the catalogue lists dims per metric)."
)


def _plan_prompt(
    query: str,
    intent: str | None,
    manifest: str,
    catalogue: CatalogueSnapshot,
) -> str:
    parts = [f"Question: {query}"]
    if intent:
        parts.append(f"Detected intent (hint): {intent}")
    parts.append(manifest)
    rendered = catalogue.render()
    if rendered:
        parts.append(rendered)
    parts.append("Return the MissionPlan now.")
    return "\n\n".join(parts)


def sanitize_plan(
    plan: MissionPlan, *, tool_names: frozenset[str], catalogue: CatalogueSnapshot
) -> tuple[MissionPlan | None, list[str]]:
    """Drop what the plan names that does not exist, keep the rest.

    A step with an unknown tool is dropped; an unknown metric id or dimension, or a
    dimension the step's metrics cannot carry, is removed from its step. Each removal
    is returned as a note the advisory block shows, so the agent does not try it.
    The plan is rejected only when no step survives. Live 2026-10-07: rejecting the
    whole plan over one step's unsupported attribution dimensions threw away a
    correct rank-then-compare plan, and the mission never compared with today.
    Catalogue checks are skipped when the snapshot does not carry the facts
    (Cube stays the authority at query time)."""
    metric_ids = catalogue.metric_ids()
    dimensions = set(catalogue.dimensions)
    notes: list[str] = []
    kept: list[PlanStep] = []
    renumber: dict[int, int] = {}
    for number, step in enumerate(plan.steps, 1):
        if tool_names and step.tool not in tool_names:
            notes.append(f"step {number}: no tool named {step.tool}")
            continue
        metrics = [m for m in step.metric_ids if not metric_ids or m in metric_ids]
        notes.extend(f"step {number}: no metric {m}" for m in step.metric_ids if m not in metrics)
        dims: list[str] = []
        for dimension in step.dimensions:
            if dimensions and dimension not in dimensions:
                notes.append(f"step {number}: no dimension {dimension}")
                continue
            unsupported = [
                m
                for m in metrics
                if (supported := catalogue.supported_dimensions_for(m))
                and dimension not in supported
                and not catalogue.is_time_dimension(dimension)
            ]
            if unsupported:
                notes.append(f"step {number}: {', '.join(unsupported)} cannot be broken down by {dimension}")
                continue
            dims.append(dimension)
        ref = step.uses_entities_from_step
        ref = renumber.get(ref) if ref is not None and 1 <= ref < number else None
        renumber[number] = len(kept) + 1
        kept.append(step.model_copy(update={"metric_ids": metrics, "dimensions": dims, "uses_entities_from_step": ref}))
    if not kept:
        return None, notes
    return plan.model_copy(update={"steps": kept}), notes


def _clip(text: str, limit: int = 160) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def render_plan(plan: MissionPlan, notes: list[str] | None = None) -> str:
    """The advisory block the mission prompt carries."""
    lines = [
        (
            "Advisory plan, drafted before any data was fetched. Follow it while it fits the "
            "tool results; if a step proves wrong (unknown metric, unsupported dimension, empty "
            "result), adapt instead of forcing it."
        ),
        f"Question shape: {plan.shape.replace('_', ' ')}",
    ]
    for number, step in enumerate(plan.steps, 1):
        bits = [step.tool]
        if step.metric_ids:
            bits.append(f"metrics: {', '.join(step.metric_ids)}")
        if step.dimensions:
            bits.append(f"by: {', '.join(step.dimensions)}")
        if step.period:
            bits.append(f"period: {_clip(step.period)}")
        if step.ranking:
            bits.append(f"ranking: {_clip(step.ranking)}")
        if step.uses_entities_from_step:
            bits.append(f"same entities as step {step.uses_entities_from_step}")
        lines.append(f"{number}. {' — '.join(bits)} — {_clip(step.purpose, 320)}")
    if notes:
        lines.append("Removed from the plan because the catalogue does not have it: " + "; ".join(notes))
    return "\n".join(lines)


async def build_plan(
    model: Model | None,
    *,
    query: str,
    intent: str | None,
    manifest: str,
    catalogue: CatalogueSnapshot,
    tool_names: frozenset[str] = frozenset(),
) -> PlanOutcome:
    """Draft and check a plan. ``PlanOutcome.plan`` is None when planning is
    unavailable, failed or produced an invalid plan; ``stats`` always says why."""
    if model is None or isinstance(model, TestModel):
        return PlanOutcome(stats={"status": "skipped"})
    started = time.perf_counter()
    stats: dict[str, Any] = {}
    try:
        planner: Agent[None, MissionPlan] = Agent(
            model=model,
            output_type=MissionPlan,
            instructions=_PLAN_INSTRUCTIONS,
            name="seleric_planner",
        )
        result = await planner.run(_plan_prompt(query, intent, manifest, catalogue))
        usage = result.usage() if callable(result.usage) else result.usage
        stats.update(input_tokens=usage.input_tokens, output_tokens=usage.output_tokens)
        plan = result.output
    except Exception as exc:
        _log.warning("plan_step_failed", exc_info=True)
        stats.update(status="failed", error=type(exc).__name__)
        return PlanOutcome(stats=_finish(stats, started))
    stats.update(shape=plan.shape, steps=len(plan.steps))
    cleaned, notes = sanitize_plan(plan, tool_names=tool_names, catalogue=catalogue)
    if notes:
        _log.warning("plan_pruned notes=%s", notes)
        stats["pruned"] = notes
    if cleaned is None:
        stats["status"] = "invalid"
        return PlanOutcome(stats=_finish(stats, started))
    stats.update(status="pruned" if notes else "valid", steps=len(cleaned.steps))
    return PlanOutcome(plan=cleaned, text=render_plan(cleaned, notes), stats=_finish(stats, started))


def _finish(stats: dict[str, Any], started: float) -> dict[str, Any]:
    stats["latency_ms"] = round((time.perf_counter() - started) * 1000)
    _log.info("planner_stats %s", stats)
    return stats


def plan_adherence(plan: MissionPlan, steps: list[dict[str, Any]] | None) -> float | None:
    """Share of planned steps the mission actually ran: the step's tool was called,
    and with one of its metric ids when it names any. None without a trace."""
    if not steps:
        return None
    calls = [s for s in steps if s.get("kind") == "tool_call"]
    if not calls:
        return 0.0
    followed = 0
    for step in plan.steps:
        for call in calls:
            if call.get("tool") != step.tool:
                continue
            args = str(call.get("args") or "")
            if not step.metric_ids or any(m in args for m in step.metric_ids):
                followed += 1
                break
    return round(followed / len(plan.steps), 2)
