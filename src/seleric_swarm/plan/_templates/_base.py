"""What every template reads: the spec, normalised once (``Prepared``), and step builders.

``prepare`` ports the slot-correction logic of ``agent/plan.py::plan_from_slots`` /
``compose_plan`` onto a ``QuestionSpec``. It reads catalogue facts only through the read
model (aggregation, volume metric, carried dimensions, families); no metric, dimension or
value names live here (A2).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Literal, Protocol

from pydantic import JsonValue

from seleric_swarm.contracts.pipeline import (
    AnalysisPlan,
    DeriveStep,
    EngineStep,
    FilterSpec,
    QueryStep,
    QuestionSpec,
    RankingSpec,
    WindowSpec,
)
from seleric_swarm.semantic import SemanticReadModel

Role = Literal["event", "baseline", "context"]
Step = QueryStep | EngineStep | DeriveStep

# Shapes the spec may carry when an entity dimension, a ranking and two windows say the
# question compares entities (live 2026-10-07: the model called it a period comparison).
_PROMOTABLE = frozenset({"period_comparison", "breakdown", "other"})
# Shapes whose ratio metrics are read with their volume base (live 2026-10-10: a return rate
# ranked one-order products at 100% with no order count beside them).
_WITH_VOLUME = frozenset({"breakdown", "entity_comparison", "lookup", "trend"})
_NEEDS_TWO_WINDOWS = frozenset({"period_comparison", "entity_comparison"})


@dataclass(frozen=True)
class PlanContext:
    """What the planner needs besides the spec."""

    read_model: SemanticReadModel
    as_of: date | None = None
    # The configured business headline, used when a comparison names no measure (golden Q6
    # 2026-10-08). Injected by the pipeline from settings, never written here.
    headline_metric_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Prepared:
    """The spec after the planner's corrections. Templates read this, never the question."""

    spec: QuestionSpec
    shape: str
    metrics: tuple[str, ...]
    entity: str | None
    breakdowns: tuple[str, ...]
    ranking: RankingSpec | None
    windows: dict[Role, WindowSpec]
    today_running: bool
    notes: tuple[str, ...] = field(default=())

    @property
    def event(self) -> Role:
        """The role a single-window step reads: the event window, else the first named."""
        if "event" in self.windows or not self.windows:
            return "event"
        return next(iter(self.windows))

    @property
    def compares_periods(self) -> bool:
        return "event" in self.windows and "baseline" in self.windows


class Template(Protocol):
    """One question shape → a plan. ``matches`` is cheap; ``build`` may decline with None."""

    id: str

    def matches(self, prepared: Prepared) -> bool: ...

    def build(self, prepared: Prepared, ctx: PlanContext) -> AnalysisPlan | None: ...


def prepare(spec: QuestionSpec, ctx: PlanContext) -> Prepared:
    """Resolve metrics, entity, shape and windows once, recording each correction."""
    model = ctx.read_model
    notes: list[str] = []
    windows: dict[Role, WindowSpec] = {}
    for window in spec.windows:
        windows.setdefault(window.role, window)
    today_running = bool(
        ctx.as_of is not None and any(w.start <= ctx.as_of <= w.end for w in windows.values())
    )

    metrics = [m.metric_id for m in spec.measures if m.metric_id and _known(model, m.metric_id)]
    notes.extend(f"no metric {m.metric_id}" for m in spec.measures if m.metric_id and not _known(model, m.metric_id))
    metrics = list(dict.fromkeys(metrics))
    ranking = spec.ranking if spec.ranking and _known(model, spec.ranking.by_metric_id) else None

    entity = spec.entities[0].dimension if spec.entities else None
    if entity is None and spec.breakdowns and (
        ranking is not None or spec.shape == "entity_comparison"
    ):
        entity = spec.breakdowns[0]
    breakdowns = tuple(d for d in spec.breakdowns if not model.is_time_dimension(d))

    shape = spec.shape
    if shape == "why_single_metric" and today_running:
        notes.append("the window includes today, which is still running: compared instead of diagnosed")
        shape = "period_comparison"

    if not metrics and ctx.headline_metric_ids and shape in _NEEDS_TWO_WINDOWS:
        headline = [m for m in ctx.headline_metric_ids if _known(model, m)]
        if shape == "entity_comparison" and entity:
            headline = [m for m in headline if model.carries(m, entity)]
        if not headline and shape == "entity_comparison" and entity:
            # No headline measure is sliced by this entity: compare it on the measures that are.
            headline = model.metrics_supporting_dimension(entity, n=3)
        if headline:
            metrics = headline
            notes.append("no measure named: compared the business headline measures")

    # The entities are ranked by the rank metric: an entity dimension it cannot carry is a
    # mis-pick (golden Q16 2026-10-09). Rank by the first breakdown it does carry.
    ranker = ranking.by_metric_id if ranking else (metrics[0] if metrics else None)
    if entity and ranker and not model.carries(ranker, entity):
        alt = next((d for d in breakdowns if d != entity and model.carries(ranker, d)), None)
        if alt is not None:
            notes.append(f"entity '{entity}' is not a slice of {ranker}: ranked by {alt} instead")
            entity = alt

    if shape in _PROMOTABLE and entity and ranking is not None and "baseline" in windows:
        shape = "entity_comparison"
    if shape == "entity_comparison" and not entity:
        notes.append("no entity dimension: compared whole-account figures instead")
        shape = "period_comparison"

    if (breakdowns or entity) and shape in _WITH_VOLUME:
        for mid in list(metrics):
            volume = model.volume_metric_for(mid) if model.aggregation_for(mid) == "ratio" else None
            if volume and volume not in metrics and _known(model, volume):
                metrics.append(volume)
                notes.append(f"{volume} fetched beside {mid}: the base each entity's rate rests on")

    return Prepared(
        spec=spec,
        shape=shape,
        metrics=tuple(metrics),
        entity=entity,
        breakdowns=breakdowns,
        ranking=ranking,
        windows=windows,
        today_running=today_running,
        notes=tuple(notes),
    )


def is_analysis(prepared: Prepared) -> bool:
    return prepared.spec.kind == "analysis"


class Steps:
    """Builds steps with stable, readable ids (``q1``, ``q2``, ``d1`` …)."""

    def __init__(self) -> None:
        self._steps: list[Step] = []
        self._counts: dict[str, int] = {}

    def _id(self, prefix: str) -> str:
        self._counts[prefix] = self._counts.get(prefix, 0) + 1
        return f"{prefix}{self._counts[prefix]}"

    def query(
        self,
        metric_id: str,
        *,
        role: Role,
        breakdowns: Sequence[str] = (),
        filters: Sequence[FilterSpec] = (),
        grain: str | None = None,
        ranking: RankingSpec | None = None,
        entities_from_step: str | None = None,
        elapsed_only: bool = False,
        step_id: str | None = None,
    ) -> str:
        sid = step_id or self._id("q")
        self._steps.append(
            QueryStep(
                step_id=sid,
                metric_id=metric_id,
                breakdowns=tuple(breakdowns),
                filters=tuple(filters),
                window_role=role,
                grain=grain,
                ranking=ranking,
                entities_from_step=entities_from_step,
                elapsed_only=elapsed_only,
            )
        )
        return sid

    def derive(self, op: str, inputs: Sequence[str], **params: JsonValue) -> str:
        sid = self._id("d")
        self._steps.append(DeriveStep(step_id=sid, op=op, inputs=tuple(inputs), params=dict(params)))
        return sid

    def engine(self, engine: str, params: dict[str, JsonValue], inputs: Sequence[str] = ()) -> str:
        sid = self._id("e")
        self._steps.append(EngineStep(step_id=sid, engine=engine, params=params, inputs=tuple(inputs)))
        return sid

    def plan(self, template_id: str) -> AnalysisPlan | None:
        if not self._steps:
            return None
        return AnalysisPlan(template_id=template_id, steps=tuple(self._steps))

    @property
    def has_steps(self) -> bool:
        return bool(self._steps)


def carried(model: SemanticReadModel, metric_id: str, dimensions: Sequence[str]) -> tuple[str, ...]:
    """The dimensions ``metric_id`` can be sliced by (directly, via a twin or a conformed sibling)."""
    return tuple(d for d in dimensions if model.carries(metric_id, d))


def carried_reading(model: SemanticReadModel, metric_id: str, dimension: str) -> str | None:
    """``dimension`` when the metric carries it, else a member of its conformed family that it does."""
    if model.carries(metric_id, dimension):
        return dimension
    for candidate in sorted(model.family_members(dimension) - {dimension}):
        if model.carries(metric_id, candidate):
            return candidate
    return None


def elapsed(prepared: Prepared, model: SemanticReadModel, metric_id: str) -> bool:
    """Additive metrics compared while today runs count the same hours in every window."""
    return prepared.today_running and model.aggregation_for(metric_id) == "additive"


def change_params(prepared: Prepared) -> dict[str, JsonValue]:
    """``per_day`` when the two compared windows differ in length (the derive op refuses otherwise)."""
    event, baseline = prepared.windows.get("event"), prepared.windows.get("baseline")
    if event is None or baseline is None:
        return {}
    unequal = (event.end - event.start) != (baseline.end - baseline.start)
    return {"per_day": True} if unequal else {}


def windows_json(prepared: Prepared) -> list[JsonValue]:
    return [w.model_dump(mode="json") for w in prepared.windows.values()]


def filters_json(prepared: Prepared) -> list[JsonValue]:
    return [f.model_dump(mode="json") for f in prepared.spec.filters]


def _known(model: SemanticReadModel, metric_id: str) -> bool:
    return not model.metric_ids() or model.has_metric(metric_id)
