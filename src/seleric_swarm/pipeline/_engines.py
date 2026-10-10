"""Engine steps besides ``investigate``: ``explore`` (M4.4) and ``composition``.

``explore`` adapts the exploration engine to typed reads and returns an ``ExplorationMap``
(insights shown + typed follow-up steps) so "what else?" continues from it.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from seleric_swarm.causal import diagnosis as dx
from seleric_swarm.contracts.pipeline import (
    Derivation,
    EngineStep,
    FilterSpec,
    QueryStep,
    QuestionSpec,
    Ref,
    ResultSet,
    WindowSpec,
)
from seleric_swarm.derive import derive
from seleric_swarm.exploration import engine as xe
from seleric_swarm.graph import MetricGraph
from seleric_swarm.plan import PERIOD_KEY, execute
from seleric_swarm.semantic import SemanticPort, SemanticReadModel
from seleric_swarm.toolsets import policy_config as P

# Legacy follow-up tool → typed engine (M4.4).
_FOLLOW_UP_ENGINE = {"explore_data": "explore", "diagnose_metric_change": "investigate", "query_metrics": "query"}
_DEFAULT_DAYS = 7
_MAX_SEGMENT_DIMS = 4


class ExplorationMap(BaseModel):
    """What one exploration showed and where it can go next (stored per thread)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = 1
    metric_ids: tuple[str, ...]
    window: tuple[date, date] | None = None
    filters: tuple[FilterSpec, ...] = ()
    shown: tuple[str, ...] = ()  # insight keys already reported
    insights: tuple[dict[str, Any], ...] = ()
    follow_ups: tuple[EngineStep, ...] = ()
    data_quality: tuple[str, ...] = ()
    tested: int = 0
    survived: int = 0
    params: dict[str, Any] = Field(default_factory=dict)


def insight_key(i: xe.Insight) -> str:
    return "|".join([i.kind, i.metric, i.dimension or "", ",".join(i.segments), i.related_metric or ""])


def follow_up_steps(report: xe.ExplorationReport, prefix: str) -> tuple[EngineStep, ...]:
    out: list[EngineStep] = []
    for i in report.insights:
        for f in i.follow_ups:
            engine = _FOLLOW_UP_ENGINE.get(f.tool)
            if engine is None:
                continue
            params = {**xe_json(f.args), "reason": f.reason}
            out.append(EngineStep(step_id=f"{prefix}.n{len(out)}", engine=engine, params=params))
    return tuple(out)


def xe_json(v: Any) -> Any:
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, dict):
        return {str(k): xe_json(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set, frozenset)):
        return [xe_json(x) for x in v]
    return v


async def _daily(
    port: SemanticPort | None, read_model: SemanticReadModel, as_of: datetime,
    metric: str, window: WindowSpec, dimension: str | None, filters: tuple[FilterSpec, ...],
) -> tuple[ResultSet, dict[str, dict[date, float]]]:
    step = QueryStep(step_id=f"x:{metric}:{dimension or ''}", metric_id=metric, breakdowns=(dimension,) if dimension else (),
                     filters=filters, window_role=window.role, grain="day")
    (rs,) = await execute([step], windows={window.role: window}, port=port, read_model=read_model, as_of=as_of)
    out: dict[str, dict[date, float]] = {}
    for r in rs.rows:
        day = r.dimensions.get(PERIOD_KEY)
        if day is None or r.value is None:
            continue
        seg = r.dimensions.get(dimension, "") if dimension else ""
        out.setdefault(seg, {})[date.fromisoformat(day)] = r.value
    return rs, out


async def run_explore(
    step: EngineStep,
    spec: QuestionSpec,
    *,
    port: SemanticPort | None,
    read_model: SemanticReadModel,
    graph: MetricGraph,
    as_of: datetime,
) -> tuple[ExplorationMap, list[ResultSet]]:
    raw_metrics = step.params.get("metric_ids")
    metrics = [m for m in (raw_metrics if isinstance(raw_metrics, list) else []) if isinstance(m, str) and read_model.has_metric(m)]
    metrics = metrics or [m.metric_id for m in spec.measures if m.metric_id and read_model.has_metric(m.metric_id)]
    windows = {w.role: w for w in spec.windows}
    today = as_of.date()
    if "event" in windows:
        start, end = windows["event"].start, min(windows["event"].end, today - timedelta(days=1))
    else:
        end = today - timedelta(days=1)
        start = end - timedelta(days=_DEFAULT_DAYS - 1)
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)] if end >= start else []
    filters = tuple(spec.filters)
    if not metrics or not days:
        return ExplorationMap(metric_ids=tuple(metrics), data_quality=("nothing to explore",)), []
    history = P.EXPLORE_HISTORY_WINDOWS
    span = xe.windows(days, history)
    span_window = WindowSpec(role="context", start=min(d for w in span for d in w), end=days[-1])
    sets: list[ResultSet] = []
    series: dict[str, dict[date, float]] = {}
    segments: dict[str, dict[str, dict[str, dict[date, float]]]] = {}
    lineage = {m: dx.MetricMeta(id=m, aggregation=read_model.aggregation_for(m) or "", label=read_model.label_for(m) or "",
                                depends_on=graph.components(m)) for m in metrics}
    used = {f.dimension for f in filters}
    jobs: list[tuple[str, str | None]] = []
    for m in metrics:
        jobs.append((m, None))
        if lineage[m].additive:
            dims = [d for d in read_model.supported_dimensions_for(m) if d not in used and not read_model.is_time_dimension(d)]
            jobs += [(m, d) for d in dims[:_MAX_SEGMENT_DIMS]]
    results = await asyncio.gather(*(_daily(port, read_model, as_of, m, span_window, d, filters) for m, d in jobs))
    for (m, d), (rs, parsed) in zip(jobs, results, strict=True):
        sets.append(rs)
        if d is None:
            if parsed.get(""):
                series[m] = parsed[""]
        elif len(parsed) >= 2:
            segments.setdefault(m, {})[d] = parsed
    inp = xe.ExplorationInput(window=days, series=series, lineage=lineage, segments=segments, history_windows=history,
                              filters={f.dimension: list(f.values) for f in filters if f.operator == "in"})
    report = await asyncio.to_thread(xe.explore, inp)
    shown_before = step.params.get("exclude")
    exclude = {x for x in (shown_before if isinstance(shown_before, list) else []) if isinstance(x, str)}
    fresh = [i for i in report.insights if insight_key(i) not in exclude]
    report.insights = fresh
    doc = report.as_dict()
    return ExplorationMap(
        metric_ids=tuple(metrics), window=(days[0], days[-1]), filters=filters,
        shown=tuple(sorted(exclude | {insight_key(i) for i in fresh})),
        insights=tuple(doc.get("insights") or ()), follow_ups=follow_up_steps(report, step.step_id),
        data_quality=tuple(report.data_quality), tested=report.tested, survived=report.survived,
        params={k: v for k, v in step.params.items() if k != "exclude"},
    ), sets


def continue_exploration(previous: ExplorationMap, step_id: str = "e1") -> EngineStep:
    """The typed step for "what else?": same scope, skipping what was already shown."""
    return EngineStep(step_id=step_id, engine="explore",
                      params={**previous.params, "metric_ids": list(previous.metric_ids), "exclude": list(previous.shown)})


async def run_composition(
    step: EngineStep,
    spec: QuestionSpec,
    *,
    port: SemanticPort | None,
    read_model: SemanticReadModel,
    graph: MetricGraph,
    as_of: datetime,
) -> tuple[list[ResultSet], list[Derivation]]:
    """The metric and each catalogue component over every spec window, plus per-component change."""
    metric = step.params.get("metric_id")
    if not isinstance(metric, str):
        return [], []
    members = [metric, *graph.components(metric)]
    windows: dict[str, WindowSpec] = {w.role: w for w in spec.windows}
    steps = [QueryStep(step_id=f"{step.step_id}.{m}.{w.role}", metric_id=m, breakdowns=(), filters=tuple(spec.filters),
                       window_role=w.role, grain=None) for m in members for w in windows.values()]
    sets = list(await execute(steps, windows=windows, port=port, read_model=read_model, as_of=as_of))
    derivations: list[Derivation] = []
    if {"event", "baseline"} <= set(windows):
        by = {rs.step_id: rs for rs in sets}
        for m in members:
            b, e = by.get(f"{step.step_id}.{m}.baseline"), by.get(f"{step.step_id}.{m}.event")
            if b is not None and e is not None:
                derivations.append(derive("delta", [b, e], {}, read_model=read_model,
                                          ref=Ref(kind="derivation", id=f"{step.step_id}.{m}.delta")))
    return sets, derivations
