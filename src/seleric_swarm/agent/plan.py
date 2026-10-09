"""Plan-first step — the question's shape and slots from one bounded LLM call,
the plan itself from code.

2026-10-07: with gpt-5-nano as the only model, a free-form plan was wrong on
every run of "the last 3 days' best campaigns versus today": it chose the
account-level shape, planned ``compare_periods`` (which fetches nothing), took
``be_roas`` for ROAS, invented a metric and took 10-21s. A small model can read
a question; it cannot reliably compose a multi-step analysis.

So the LLM only fills ``PlanSlots``: the shape, the entity dimension, what ranks
"best", the metrics in the user's own words (plus its best catalogue id) and the
breakdowns asked for. Code then
- resolves each metric phrase with the catalogue's own concept resolver (the
  model's id is the fallback, and only if the catalogue has it),
- checks every id against the catalogue (metric x dimension support),
- takes both windows from the deterministic time resolver, and
- builds the steps from a per-shape template: rank, then the same entities in
  both windows over the same elapsed hours when one window is today, then the
  requested breakdowns.
Nothing here names a metric; the vocabulary is the catalogue's.

The plan reaches the mission prompt as an ADVISORY block the agent may correct
from tool results. Fail-open: any error, unusable slots, or the offline stub
``TestModel`` returns no plan and the mission runs as before.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.models.test import TestModel

from seleric_swarm.services.catalogue_bootstrap import CatalogueSnapshot
from seleric_swarm.services.elapsed import covers_in_progress_day

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
    "composition",
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


class MetricSlot(BaseModel):
    words: str = Field(description="The user's term in plain words, abbreviations spelled out.")
    metric_id: str = Field(default="", description="Your best catalogue metric id for it, or empty.")


class PlanSlots(BaseModel):
    shape: PlanShape
    entity_dimension: str = Field(
        default="",
        description="For entity_comparison: the catalogue dimension id that holds the entities' names.",
    )
    rank_by: MetricSlot | None = Field(default=None, description="What makes an entity 'best' / 'top'.")
    top_n: int | None = Field(default=None, description="How many entities to keep, if the user says.")
    rank_order: Literal["desc", "asc"] = Field(
        default="desc",
        description="asc when the user asks for the worst, lowest, loss-making or declining entities; else desc.",
    )
    metrics: list[MetricSlot] = Field(default_factory=list, description="Every measure the user asks for.")
    breakdown_dimensions: list[str] = Field(
        default_factory=list, description="Catalogue dimension ids the user asks to break down or map by."
    )


_SLOT_INSTRUCTIONS = (
    "Read the user's analytics question and fill PlanSlots. Do not plan steps or answer.\n"
    "- shape: entity_comparison when the question is about particular entities (the best or "
    "worst members of some dimension) in one window compared with another; "
    "period_comparison for whole-account figures across two windows; why_single_metric for "
    "why one metric's total moved; funnel for stage drop-off; else lookup, trend, breakdown "
    "or other. The detected intent is only a hint.\n"
    "- entity_dimension: the dimension id (from the catalogue's Dimensions list) holding those "
    "entities' names.\n"
    "- rank_by: the outcome or efficiency measure that makes an entity 'best' — what it "
    "returned, never what it cost.\n"
    "- metrics: every measure asked for, one entry each, in plain words with abbreviations "
    "spelled out, plus your best metric id from the catalogue (empty if unsure).\n"
    "- breakdown_dimensions: dimension ids for any 'by …' or 'map to …' the user asks for."
)


def _slot_prompt(query: str, intent: str | None, catalogue: CatalogueSnapshot) -> str:
    parts = [f"Question: {query}"]
    if intent:
        parts.append(f"Detected intent (hint): {intent}")
    rendered = catalogue.render_compact()
    if rendered:
        parts.append(rendered)
    parts.append("Return PlanSlots now.")
    return "\n\n".join(parts)


TermResolver = Callable[[list[str]], Awaitable[dict[str, str | None]]]


async def _resolve_metrics(
    slots: Sequence[MetricSlot], resolver: TermResolver | None, catalogue: CatalogueSnapshot
) -> tuple[list[str], list[str]]:
    """Catalogue ids for the user's metric phrases, in order, and notes for misses."""
    known = catalogue.metric_ids()
    words = [m.words.strip() for m in slots if m.words.strip()]
    resolved: dict[str, str | None] = {}
    if resolver is not None and words:
        try:
            resolved = await resolver(words)
        except Exception:
            _log.warning("plan_resolver_failed", exc_info=True)
    ids: list[str] = []
    notes: list[str] = []
    for slot in slots:
        candidate = resolved.get(slot.words.strip()) or ""
        if not (candidate and (not known or candidate in known)):
            candidate = slot.metric_id.strip() if slot.metric_id.strip() in known else ""
        if not candidate:
            notes.append(f"no catalogue metric for '{slot.words}'")
        elif candidate not in ids:
            ids.append(candidate)
        else:
            # two asked measures landing on one id means one of them was lost (live 2026-10-08: "cost per
            # click" / "cost per mille" both resolved to CTR) — say so, so the agent fetches it itself
            notes.append(f"'{slot.words}' resolved to {candidate}, like an earlier measure — find its own metric id")
    return ids, notes


def _pairs(slots: Sequence[MetricSlot], ids: list[str]) -> list[tuple[MetricSlot, str]]:
    """(slot, id) for slots whose resolution survived, in order (ids are deduped, so
    pair by position only while counts match; otherwise list the ids alone)."""
    if len(slots) == len(ids):
        return list(zip(slots, ids, strict=True))
    return [(MetricSlot(words=i, metric_id=i), i) for i in ids]


def _window_text(window: tuple[date, date] | None, fallback: str) -> str:
    if window is None:
        return fallback
    start, end = window
    return start.isoformat() if start == end else f"{start}..{end}"


def compose_plan(
    slots: PlanSlots,
    *,
    metric_ids: list[str],
    rank_id: str | None,
    windows: Sequence[tuple[date, date]],
    catalogue: CatalogueSnapshot,
    as_of: datetime | None,
) -> tuple[MissionPlan | None, list[str]]:
    """The plan for the question's shape, built from resolved slots."""
    notes: list[str] = []
    dims = set(catalogue.dimensions)

    def dim_ok(dim: str) -> bool:
        return bool(dim) and (not dims or dim in dims)

    reference = windows[0] if windows else None
    comparison = windows[1] if len(windows) > 1 else None
    ref_text = _window_text(reference, "the reference window the question names")
    cmp_text = _window_text(comparison, "the comparison window the question names")
    today_running = bool(
        comparison and as_of is not None and covers_in_progress_day(comparison[0], comparison[1], as_of)
    )
    additive = [m for m in metric_ids if catalogue.aggregation_for(m) == "additive"]
    if today_running:
        hours = (
            f" Additive metrics ({', '.join(additive) or 'none'}) with elapsed_only=True in BOTH windows, so "
            "each day counts the same hours as today; compare per-day figures. Ratios as they are."
        )
    else:
        hours = ""
    breakdowns = [d for d in slots.breakdown_dimensions if dim_ok(d)]
    notes.extend(f"no dimension {d}" for d in slots.breakdown_dimensions if not dim_ok(d))
    shape = slots.shape
    if shape == "why_single_metric" and today_running:
        notes.append("the window includes today, which is still running: compared instead of diagnosed")
        shape = "period_comparison"
    entity = slots.entity_dimension.strip()
    # The entities are ranked by the rank metric: an entity dimension it cannot carry is a mis-pick (golden Q16
    # 2026-10-09: "which campaigns, products and channels contributed" ranked net profit by entity_name, a field of
    # the ad change log only). Rank by the first breakdown the user asked for that the rank metric carries.
    ranker = rank_id or (metric_ids[0] if metric_ids else None)
    if entity and ranker and dim_ok(entity) and not catalogue.carries(ranker, entity):
        if alt := next((d for d in breakdowns if d != entity and catalogue.carries(ranker, d)), None):
            notes.append(f"entity '{entity}' is not a slice of {ranker}: ranked by {alt} instead")
            entity = alt
    # The slots decide, not the label: an entity dimension plus a "best by" measure across
    # two windows is an entity comparison even when the model called it a period
    # comparison (live 2026-10-07, gpt-5-nano, 2 of 4 runs).
    if (
        shape in ("period_comparison", "breakdown", "other")
        and dim_ok(entity)
        and slots.rank_by is not None
        and comparison is not None
    ):
        shape = "entity_comparison"
    if shape == "entity_comparison" and not dim_ok(entity):
        notes.append(f"no entity dimension '{entity}': compared whole-account figures instead")
        shape = "period_comparison"
    steps: list[PlanStep] = []
    if shape == "entity_comparison":
        rank = rank_id or (metric_ids[0] if metric_ids else None)
        if rank is None:
            return None, [*notes, "no metric to rank the entities by"]
        n = slots.top_n or 10
        volume = catalogue.volume_metric_for(rank)
        guard = f", among entities with meaningful {volume}" if volume else ""
        steps.append(
            PlanStep(
                tool="query_metrics",
                metric_ids=[rank],
                dimensions=[entity],
                period=ref_text,
                ranking=f"order='{slots.rank_order}', limit={n}{guard}",
                purpose=f"Rank the {entity} values over {ref_text} by {rank}; these are the entities to compare.",
            )
        )
        compared = [m for m in dict.fromkeys([rank, *metric_ids])]
        for window_text, label in ((ref_text, "reference"), (cmp_text, "comparison")):
            steps.append(
                PlanStep(
                    tool="query_metrics",
                    metric_ids=compared,
                    dimensions=[entity],
                    period=window_text,
                    uses_entities_from_step=1,
                    purpose=(
                        f"The same {entity} values (dimensions={{'{entity}': [the step-1 list]}}) over the "
                        f"{label} window {window_text}, one query per metric.{hours}"
                    ),
                )
            )
    elif shape == "period_comparison" and metric_ids:
        # Only with measures named: a metric-less plan read to the agent as missing
        # information, and "compare this month to the same days last month" was
        # answered with a request to pick metrics (live 2026-10-08, golden Q6).
        # With none named, no plan — the agent picks the business headline itself.
        for window_text, label in ((ref_text, "reference"), (cmp_text, "comparison")):
            steps.append(
                PlanStep(
                    tool="query_metrics",
                    metric_ids=list(metric_ids),
                    period=window_text,
                    purpose=f"Every requested metric over the {label} window {window_text}.{hours}",
                )
            )
    elif shape == "composition" and metric_ids:
        # A total split into the lines it is built from (one period), or the change between two periods split
        # by line: the catalogue's verified composition, so the lines reconcile (break_down_metric).
        steps.append(
            PlanStep(
                tool="break_down_metric",
                metric_ids=[metric_ids[0]],
                period=ref_text if comparison is None else f"{ref_text} compared with {cmp_text}",
                purpose="Break the total into its reconciling lines; answer from its table and waterfall.",
            )
        )
    elif shape == "why_single_metric" and metric_ids:
        steps.append(
            PlanStep(
                tool="diagnose_metric_change",
                metric_ids=[metric_ids[0]],
                period=ref_text,
                purpose="Diagnose the change once and answer from its ANSWER SKELETON.",
            )
        )
    elif metric_ids:
        steps.append(
            PlanStep(
                tool="query_metrics",
                metric_ids=list(metric_ids),
                dimensions=breakdowns,
                period=ref_text,
                purpose="Fetch each requested metric (one query per metric) at the breakdown asked for.",
            )
        )
        breakdowns = []
    if breakdowns and steps:
        mapped = [m for m in additive if any(d in catalogue.supported_dimensions_for(m) for d in breakdowns)]
        if mapped:
            entity_dims = [entity] if shape == "entity_comparison" else []
            steps.append(
                PlanStep(
                    tool="query_metrics",
                    metric_ids=mapped,
                    dimensions=list(dict.fromkeys([*entity_dims, *breakdowns])),
                    period=cmp_text if comparison else ref_text,
                    uses_entities_from_step=1 if entity_dims else None,
                    purpose="Map these amounts to the breakdowns asked for (each dimension its metrics support).",
                )
            )
        else:
            notes.append(f"no requested metric can be broken down by {', '.join(breakdowns)}")
    if not steps:
        return None, [*notes, "no usable step"]
    if shape in ("entity_comparison", "period_comparison"):
        last = steps[-1]
        steps[-1] = last.model_copy(
            update={
                "purpose": last.purpose
                + " Then explain each change from the entity's own metrics (delivery and price, "
                "engagement, conversion, value); never substitute account totals for entities."
            }
        )
    return MissionPlan(shape=shape, steps=steps[:_MAX_STEPS]), notes


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
        # Some models qualify the name with their tool namespace ("functions.query_metrics",
        # live 2026-10-07 on gpt-5-nano); the registered name is the last segment.
        step = step.model_copy(update={"tool": step.tool.strip().rsplit(".", 1)[-1]})
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
            # conformed siblings and grain twins count: query_metrics answers through them
            unsupported = [m for m in metrics if not catalogue.carries(m, dimension)]
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
        lines.append("Planning notes: " + "; ".join(notes))
    return "\n".join(lines)


async def build_plan(
    model: Model | None,
    *,
    query: str,
    intent: str | None,
    manifest: str,
    catalogue: CatalogueSnapshot,
    tool_names: frozenset[str] = frozenset(),
    resolver: TermResolver | None = None,
    windows: Sequence[tuple[date, date]] = (),
    as_of: datetime | None = None,
) -> PlanOutcome:
    """Slots from the model, plan from code. ``PlanOutcome.plan`` is None when
    planning is unavailable, failed or unusable; ``stats`` always says why.
    ``manifest`` is kept for the call signature; the template names the tools."""
    del manifest
    if model is None or isinstance(model, TestModel):
        return PlanOutcome(stats={"status": "skipped"})
    started = time.perf_counter()
    stats: dict[str, Any] = {}
    try:
        reader: Agent[None, PlanSlots] = Agent(
            model=model,
            output_type=PlanSlots,
            instructions=_SLOT_INSTRUCTIONS,
            name="seleric_planner",
        )
        result = await reader.run(_slot_prompt(query, intent, catalogue))
        usage = result.usage() if callable(result.usage) else result.usage
        stats.update(input_tokens=usage.input_tokens, output_tokens=usage.output_tokens)
        slots = result.output
    except Exception as exc:
        _log.warning("plan_step_failed", exc_info=True)
        stats.update(status="failed", error=type(exc).__name__)
        return PlanOutcome(stats=_finish(stats, started))
    return await plan_from_slots(
        slots, catalogue=catalogue, tool_names=tool_names, resolver=resolver,
        windows=windows, as_of=as_of, stats=stats, started=started,
    )


async def plan_from_slots(
    slots: PlanSlots,
    *,
    catalogue: CatalogueSnapshot,
    tool_names: frozenset[str] = frozenset(),
    resolver: TermResolver | None = None,
    windows: Sequence[tuple[date, date]] = (),
    as_of: datetime | None = None,
    stats: dict[str, Any] | None = None,
    started: float | None = None,
    headline_metric_ids: Sequence[str] = (),
    question: str = "",
) -> PlanOutcome:
    """The plan from slots already read (``agent/understand.py``) — code only, no LLM.

    ``headline_metric_ids``: the configured business headline, used when a period
    comparison names no measure ("compare this month to the same days last month"
    was answered with break-even ROAS alone, golden Q6 2026-10-08)."""
    stats = {} if stats is None else stats
    started = time.perf_counter() if started is None else started
    metric_ids, notes = await _resolve_metrics(slots.metrics, resolver, catalogue)
    if not metric_ids and slots.shape == "period_comparison" and headline_metric_ids:
        metric_ids = [m for m in headline_metric_ids if not catalogue.metrics or catalogue.has_metric(m)]
        notes.append("no measure named: compared the business headline measures")
    entity = slots.entity_dimension.strip()
    if not metric_ids and entity and slots.shape == "entity_comparison" and headline_metric_ids:
        # Entities compared with no measure named: the headline measures that carry the entity,
        # never an invalid plan (live 2026-10-09 MS3-0f473daefd, "compare performance of meta ads
        # campaigns": no plan, and the answer gave four account totals instead of campaigns).
        metric_ids = [
            m for m in headline_metric_ids
            if (not catalogue.metrics or catalogue.has_metric(m)) and catalogue.carries(m, entity)
        ]
        notes.append(f"no measure named: compared the headline measures that carry {entity}")
    if slots.shape in ("why_single_metric", "composition") and metric_ids and resolver is not None and question.strip():
        # One outcome is diagnosed / broken down. The understanding sometimes reads one named measure as several
        # variants ("ROAS" as gross and net: gross ROAS was diagnosed for a net ROAS question), or words a slot
        # with qualifiers the user never said ("P&L net profit": a September waterfall on the event-date Finance
        # P&L, −1.53M, instead of the order-date default, −170k — regression 2026-10-09 golden Q22). The
        # catalogue's own reading of the question names the measure asked about when it is one of them or the
        # same measure on the other date basis.
        try:
            asked = (await resolver([question.strip()])).get(question.strip())
        except Exception:
            asked = None
        twins = {m: catalogue.date_basis_for(m)[1] for m in metric_ids}
        if asked and asked != metric_ids[0] and (asked in metric_ids or asked == twins.get(metric_ids[0])):
            replaced = metric_ids[0] if asked == twins.get(metric_ids[0]) else None
            metric_ids = [asked, *[m for m in metric_ids if m not in (asked, replaced)]]
            notes.append(f"the outcome is {asked}: the measure the question itself names")
    rank_id = None
    if slots.rank_by is not None:
        rank_ids, rank_notes = await _resolve_metrics([slots.rank_by], resolver, catalogue)
        rank_id = rank_ids[0] if rank_ids else None
        notes.extend(rank_notes)
    plan, compose_notes = compose_plan(
        slots, metric_ids=metric_ids, rank_id=rank_id, windows=windows, catalogue=catalogue, as_of=as_of
    )
    notes.extend(compose_notes)
    stats.update(shape=slots.shape, metrics=metric_ids, rank_by=rank_id, entity=slots.entity_dimension)
    if plan is not None:
        plan, prune_notes = sanitize_plan(plan, tool_names=tool_names, catalogue=catalogue)
        notes.extend(prune_notes)
    if notes:
        stats["notes"] = notes
    resolved_words = [f"'{m.words}' = {mid}" for m, mid in _pairs(slots.metrics, metric_ids)]
    if resolved_words:
        notes = [
            "metrics already resolved by the catalogue (use these ids, no find_metrics needed): "
            + ", ".join(resolved_words),
            *notes,
        ]
    if plan is None:
        stats["status"] = "invalid"
        return PlanOutcome(stats=_finish(stats, started))
    stats.update(status="valid", steps=len(plan.steps))
    return PlanOutcome(plan=plan, text=render_plan(plan, notes), stats=_finish(stats, started))


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
