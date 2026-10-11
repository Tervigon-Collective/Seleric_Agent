"""Run an ``AnalysisPlan``: query steps (plan.execute) → derive steps → engine steps.

Produces the typed evidence the v2 answer stage cites. Engines: ``investigate`` (M4.3);
others are reported as unsupported so the caller can hand the question to the agent loop.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from seleric_swarm.context import AsyncContextProvider, ContextProvider, HealthSource
from seleric_swarm.contracts.pipeline import (
    AnalysisPlan,
    Derivation,
    DeriveStep,
    EngineStep,
    FilterSpec,
    QueryStep,
    QuestionSpec,
    Ref,
    ResultSet,
    StageError,
    WindowSpec,
)
from seleric_swarm.derive import derive
from seleric_swarm.graph import MetricGraph, build_graph
from seleric_swarm.investigate import (
    Attributor,
    Finding,
    InvestigatePolicy,
    InvestigateRequest,
    Investigation,
    InvestigationState,
    Selector,
)
from seleric_swarm.pipeline._context import build_context
from seleric_swarm.pipeline._engines import ExplorationMap, run_composition, run_explore
from seleric_swarm.pipeline._gcm import GcmAttributor
from seleric_swarm.plan import execute
from seleric_swarm.semantic import SemanticPort, SemanticReadModel

_log = logging.getLogger("seleric.pipeline.run_plan")


class PlanReader:
    """``investigate.MetricReader`` over ``plan.execute`` (same filters/time rules as plan steps)."""

    def __init__(self, port: SemanticPort | None, read_model: SemanticReadModel, as_of: datetime) -> None:
        self.port, self.model, self.as_of = port, read_model, as_of
        self._n = 0

    async def _rows(self, metric_id: str, window: WindowSpec, breakdowns: tuple[str, ...], filters: tuple[FilterSpec, ...]) -> ResultSet:
        self._n += 1
        step = QueryStep(step_id=f"inv{self._n}", metric_id=metric_id, breakdowns=breakdowns, filters=filters,
                         window_role=window.role, grain=None)
        (rs,) = await execute([step], windows={window.role: window}, port=self.port, read_model=self.model, as_of=self.as_of)
        return rs

    async def total(self, metric_id: str, window: WindowSpec, filters: tuple[FilterSpec, ...]) -> float | None:
        rs = await self._rows(metric_id, window, (), filters)
        if rs.error is not None or not rs.rows:
            return None
        values = [r.value for r in rs.rows if r.value is not None]
        return values[0] if len(values) == 1 else (sum(values) if values else None)

    async def breakdown(self, metric_id: str, window: WindowSpec, dimension: str, filters: tuple[FilterSpec, ...]) -> dict[str, float]:
        rs = await self._rows(metric_id, window, (dimension,), filters)
        out: dict[str, float] = {}
        for r in rs.rows:
            key = r.dimensions.get(dimension)
            if key is not None and r.value is not None:
                out[key] = out.get(key, 0.0) + r.value
        return out


@dataclass
class PlanRun:
    result_sets: list[ResultSet] = field(default_factory=list)
    derivations: list[Derivation] = field(default_factory=list)
    investigations: dict[str, InvestigationState] = field(default_factory=dict)
    explorations: dict[str, ExplorationMap] = field(default_factory=dict)
    unsupported: list[str] = field(default_factory=list)  # engine ids the v2 path cannot run yet
    deadline_hit: list[str] = field(default_factory=list)  # steps skipped or cut by the latency tier

    @property
    def evidence(self) -> list[ResultSet | Derivation]:
        return [*self.result_sets, *self.derivations]

    def summary(self) -> dict[str, Any]:
        return {
            "result_sets": len(self.result_sets),
            "step_errors": [rs.error.code for rs in self.result_sets if rs.error],
            "derivations": len(self.derivations),
            "derive_errors": [d.error.code for d in self.derivations if d.error],
            "investigations": {k: v.status for k, v in self.investigations.items()},
            "explorations": {k: len(v.insights) for k, v in self.explorations.items()},
            "unsupported": self.unsupported,
            "deadline_hit": self.deadline_hit,
        }


async def run_plan(
    plan: AnalysisPlan,
    spec: QuestionSpec,
    *,
    port: SemanticPort | None,
    read_model: SemanticReadModel,
    as_of: datetime,
    graph: MetricGraph | None = None,
    providers: Sequence[ContextProvider | AsyncContextProvider] | None = None,
    health_sources: Sequence[HealthSource] | None = None,
    selector: Selector | None = None,
    policy: InvestigatePolicy | None = None,
    graph_gcm: bool = False,
    deadline_s: float | None = None,
) -> PlanRun:
    """``deadline_s`` (M5.3): after it, no new step starts and a running one is cut; the run
    keeps what it has and lists the rest in ``deadline_hit``."""
    run = PlanRun()
    loop = asyncio.get_running_loop()
    end = loop.time() + deadline_s if deadline_s else None

    def left() -> float | None:
        return None if end is None else max(0.0, end - loop.time())

    async def bounded(step_id: str, coro: Any) -> Any:
        remaining = left()
        if remaining is not None and remaining <= 0:
            coro.close()
            run.deadline_hit.append(step_id)
            return None
        try:
            return await asyncio.wait_for(coro, timeout=remaining)
        except TimeoutError:
            run.deadline_hit.append(step_id)
            return None

    windows: dict[str, WindowSpec] = {w.role: w for w in spec.windows}
    queries = [s for s in plan.steps if isinstance(s, QueryStep)]
    if queries:
        sets = await bounded("queries", execute(queries, windows=windows, port=port, read_model=read_model, as_of=as_of))
        run.result_sets = list(sets or ())
    items: dict[str, ResultSet | Derivation] = {rs.step_id: rs for rs in run.result_sets}
    for step in plan.steps:
        if isinstance(step, DeriveStep):
            d = _derive(step, items, read_model)
            items[step.step_id] = d
            run.derivations.append(d)
        elif isinstance(step, EngineStep):
            g = graph or build_graph(read_model)
            if step.engine == "investigate":
                reader = PlanReader(port, read_model, as_of)
                if providers is None or health_sources is None:
                    built = build_context(reader)
                    providers = built[0] if providers is None else providers
                    health_sources = built[1] if health_sources is None else health_sources
                attributor = GcmAttributor(port, read_model, g, as_of) if graph_gcm else None
                state = await bounded(step.step_id, _investigate(step, spec, reader, read_model,
                                           g, providers, health_sources, selector, policy, attributor))
                if state is not None:
                    run.investigations[step.step_id] = state
                    run.derivations += findings_as_derivations(step.step_id, state)
            elif step.engine == "explore":
                got = await bounded(step.step_id, run_explore(step, spec, port=port, read_model=read_model, graph=g, as_of=as_of))
                if got is not None:
                    run.explorations[step.step_id] = got[0]
                    run.result_sets += got[1]
            elif step.engine == "composition":
                got = await bounded(step.step_id, run_composition(step, spec, port=port, read_model=read_model, graph=g, as_of=as_of))
                if got is not None:
                    run.result_sets += got[0]
                    run.derivations += got[1]
                    items.update({rs.step_id: rs for rs in got[0]})
            else:
                run.unsupported.append(step.engine)
    return run


def _derive(step: DeriveStep, items: Mapping[str, ResultSet | Derivation], read_model: SemanticReadModel) -> Derivation:
    ref = Ref(kind="derivation", id=step.step_id)
    inputs = []
    for raw in step.inputs:
        key = raw.id if isinstance(raw, Ref) else raw
        item = items.get(key)
        if item is None:
            return _failed(ref, step, "missing_input", f"no step {key!r}")
        inputs.append(item)
    try:
        return derive(step.op, inputs, step.params, read_model=read_model, ref=ref)
    except ValueError as exc:  # unknown op
        return _failed(ref, step, "unknown_op", str(exc))


def _failed(ref: Ref, step: DeriveStep, code: str, message: str) -> Derivation:
    return Derivation(ref=ref, op=step.op, inputs=(), params=dict(step.params), value=None, unit=None,
                      error=StageError(stage="derive", code=code, message=message))


async def _investigate(
    step: EngineStep,
    spec: QuestionSpec,
    reader: PlanReader,
    read_model: SemanticReadModel,
    graph: MetricGraph,
    providers: Sequence[ContextProvider | AsyncContextProvider],
    health_sources: Sequence[HealthSource],
    selector: Selector | None,
    policy: InvestigatePolicy | None,
    attributor: Attributor | None = None,
) -> InvestigationState | None:
    metric = step.params.get("metric_id")
    windows = {w.role: w for w in spec.windows}
    if not isinstance(metric, str) or "event" not in windows or "baseline" not in windows:
        _log.info("investigate_skipped", extra={"step": step.step_id})
        return None
    request = InvestigateRequest(
        metric_id=metric, event=windows["event"], baseline=windows["baseline"],
        filters=tuple(f for f in spec.filters), direction=spec.direction,
    )
    inv = Investigation(reader, read_model, graph, providers=providers, health_sources=health_sources,
                        selector=selector, policy=policy, attributor=attributor)
    return await inv.run(request)


def findings_as_derivations(step_id: str, state: InvestigationState) -> list[Derivation]:
    """Each numeric finding as a citable Derivation (``op = "finding:<kind>"``)."""
    out: list[Derivation] = []
    for i, f in enumerate(state.findings):
        out.append(Derivation(
            ref=Ref(kind="derivation", id=f"{step_id}.f{i}"),
            op=f"finding:{f.kind}",
            inputs=(),
            params=_finding_params(f),
            value=f.delta,
            unit=None,
        ))
    return out


def _finding_params(f: Finding) -> dict[str, Any]:
    params: dict[str, Any] = {"subject": f.subject, "verdict": f.verdict, "depth": f.depth, "stage": f.stage}
    for key in ("baseline", "event", "share_of_change"):
        value = getattr(f, key)
        if value is not None:
            params[key] = value
    if f.kind in ("premise", "component", "driver") and f.subject:
        params["metric_id"] = f.subject
    if f.detail:
        params["detail"] = f.detail
    return params
