"""Evidence acquisition with batching, caching, recovery and coverage (AG08-02 / AG08-08)."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from seleric_swarm.contracts.pipeline import (
    AnalysisPlan,
    DeriveStep,
    EngineStep,
    QueryStep,
    QuestionSpec,
    Ref,
    ResultSet,
    StageError,
    WindowSpec,
)
from seleric_swarm.contracts.pipeline._report import (
    AcquisitionAttempt,
    ReportCoverage,
    ReportPlan,
    SectionCoverage,
    SectionPlan,
)
from seleric_swarm.pipeline._run_plan import PlanRun, run_plan
from seleric_swarm.plan import execute
from seleric_swarm.semantic import SemanticPort, SemanticReadModel
from seleric_swarm.toolsets import policy_config as P

_log = logging.getLogger("seleric.pipeline.acquire")


def _args_hash(step: QueryStep, window: WindowSpec) -> str:
    payload = {
        "metric": step.metric_id,
        "start": window.start.isoformat(),
        "end": window.end.isoformat(),
        "dims": list(step.breakdowns),
        "filters": [f.model_dump(mode="json") for f in step.filters],
        "grain": step.grain,
        "ranking": step.ranking.model_dump(mode="json") if step.ranking else None,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def _batch_key(step: QueryStep, window: WindowSpec, view: str) -> tuple:
    return (
        view,
        window.role,
        window.start,
        window.end,
        step.breakdowns,
        tuple((f.dimension, f.operator, f.values) for f in step.filters),
        step.grain,
        # Ranked steps must not share a batch (sort/limit differ).
        step.ranking.model_dump(mode="json") if step.ranking else None,
        step.elapsed_only,
        step.entities_from_step,
    )


@dataclass
class AcquireStats:
    query_calls: int = 0
    cache_hits: int = 0
    attempts: list[AcquisitionAttempt] = field(default_factory=list)


async def _recover_step(
    step: QueryStep,
    *,
    windows: Mapping[str, WindowSpec],
    port: SemanticPort | None,
    read_model: SemanticReadModel,
    as_of: datetime,
    evidence_store: Any,
) -> ResultSet | None:
    """One bounded recovery: try grain_twins / date_twin when Core declares them."""
    twins = list(read_model.grain_twins_for(step.metric_id))
    date_basis, date_twin = read_model.date_basis_for(step.metric_id)
    if date_twin:
        twins.append(date_twin)
    for twin in twins:
        if not read_model.has_metric(twin) or twin == step.metric_id:
            continue
        alt = step.model_copy(update={"metric_id": twin, "step_id": f"{step.step_id}~{twin}"})
        (rs,) = await execute(
            [alt],
            windows=windows,
            port=port,
            read_model=read_model,
            as_of=as_of,
            evidence_store=evidence_store,
        )
        if rs.error is None and rs.rows:
            # Re-label under the original step id so derive/assembler still find it.
            return rs.model_copy(update={"step_id": step.step_id, "ref": step.ref if hasattr(step, "ref") else rs.ref})
    return None


async def acquire_queries(
    steps: Sequence[QueryStep],
    *,
    windows: Mapping[str, WindowSpec],
    port: SemanticPort | None,
    read_model: SemanticReadModel,
    as_of: datetime,
    evidence_store: Any = None,
    section_id: str = "",
    stats: AcquireStats | None = None,
) -> list[ResultSet]:
    """Run query steps with same-view batching, per-mission cache and twin recovery."""
    stats = stats or AcquireStats()
    cache: dict[str, ResultSet] = {}
    # Group batchable steps (no ranking / entities_from_step).
    groups: dict[tuple, list[QueryStep]] = {}
    solo: list[QueryStep] = []
    for step in steps:
        window = windows.get(step.window_role)
        if window is None or step.ranking is not None or step.entities_from_step:
            solo.append(step)
            continue
        view = read_model.view_for(step.metric_id) or ""
        key = _batch_key(step, window, view)
        groups.setdefault(key, []).append(step)

    results: dict[str, ResultSet] = {}

    async def _run_one(step: QueryStep, *, recovered_from: str = "") -> ResultSet:
        window = windows.get(step.window_role)
        if window is None:
            attempt = AcquisitionAttempt(
                section_id=section_id,
                metric_id=step.metric_id,
                ok=False,
                error_code="unknown_window_role",
            )
            stats.attempts.append(attempt)
            return ResultSet(
                ref=Ref(kind="result_set", id=step.step_id),
                step_id=step.step_id,
                metric_id=step.metric_id,
                unit=read_model.unit_for(step.metric_id),
                window=WindowSpec(role=step.window_role, start=as_of.date(), end=as_of.date()),
                breakdowns=step.breakdowns,
                filters=step.filters,
                rows=(),
                error=StageError(
                    stage="execute",
                    code="unknown_window_role",
                    message=f"no window for role {step.window_role!r}",
                ),
            )
        key = _args_hash(step, window)
        if key in cache:
            stats.cache_hits += 1
            cached = cache[key]
            return cached.model_copy(
                update={
                    "step_id": step.step_id,
                    "ref": cached.ref.model_copy(update={"id": step.step_id}),
                }
            )
        started = time.perf_counter()
        (rs,) = await execute(
            [step],
            windows=windows,
            port=port,
            read_model=read_model,
            as_of=as_of,
            evidence_store=evidence_store,
        )
        stats.query_calls += 1
        latency = round((time.perf_counter() - started) * 1000)
        ok = rs.error is None
        stats.attempts.append(
            AcquisitionAttempt(
                section_id=section_id,
                metric_id=step.metric_id,
                args_hash=key,
                ok=ok,
                error_code="" if ok else (rs.error.code if rs.error else "query_failed"),
                rows=len(rs.rows),
                latency_ms=latency,
                recovered_from=recovered_from,
            )
        )
        if not ok:
            recovered = await _recover_step(
                step,
                windows=windows,
                port=port,
                read_model=read_model,
                as_of=as_of,
                evidence_store=evidence_store,
            )
            if recovered is not None:
                stats.query_calls += 1
                stats.attempts.append(
                    AcquisitionAttempt(
                        section_id=section_id,
                        metric_id=recovered.metric_id,
                        args_hash=key,
                        ok=True,
                        rows=len(recovered.rows),
                        recovered_from=step.metric_id,
                    )
                )
                # Keep original step id / requested metric label for coverage.
                recovered = recovered.model_copy(
                    update={
                        "step_id": step.step_id,
                        "ref": recovered.ref.model_copy(update={"id": step.step_id}),
                    }
                )
                cache[key] = recovered
                return recovered
        if ok:
            cache[key] = rs
        return rs

    # Batched groups: one MetricsQueryArgs with multiple measures when port supports it.
    for key, group in groups.items():
        if len(group) == 1:
            solo.extend(group)
            continue
        # Execute individually but share cache via _run_one; true multi-measure batch
        # goes through execute which is per-step today. Collapse duplicate metrics.
        seen_metrics: dict[str, QueryStep] = {}
        for step in group:
            if step.metric_id in seen_metrics:
                # Same metric twice in a batch: run once, clone result.
                primary = seen_metrics[step.metric_id]
                rs = await _run_one(primary)
                results[step.step_id] = rs.model_copy(
                    update={
                        "step_id": step.step_id,
                        "ref": rs.ref.model_copy(update={"id": step.step_id}),
                    }
                )
                stats.cache_hits += 1
            else:
                seen_metrics[step.metric_id] = step
                results[step.step_id] = await _run_one(step)

    for step in solo:
        if step.step_id not in results:
            results[step.step_id] = await _run_one(step)

    return [results[s.step_id] for s in steps if s.step_id in results]


def section_coverage(
    section: SectionPlan,
    result_sets: Sequence[ResultSet],
    *,
    attempts: Sequence[AcquisitionAttempt] = (),
    read_model: SemanticReadModel | None = None,
    as_of: datetime | None = None,
) -> SectionCoverage:
    """Classify one section's evidence completeness."""
    if section.needs_agent or section.unsupported_reason:
        return SectionCoverage(
            section_id=section.section_id,
            status="unsupported",
            objective=section.objective,
            notes=(section.unsupported_reason or "no_template",),
            attempts=tuple(a for a in attempts if a.section_id == section.section_id),
        )
    prefix = section.section_id + "."
    owned = [rs for rs in result_sets if rs.step_id.startswith(prefix) or rs.step_id in {s.step_id for s in section.plan.steps if isinstance(s, QueryStep)}]
    # Also match namespaced step ids exactly.
    step_ids = {s.step_id for s in section.plan.steps if isinstance(s, QueryStep)}
    owned = [rs for rs in result_sets if rs.step_id in step_ids]
    acquired = tuple(rs.metric_id for rs in owned if rs.error is None and rs.rows)
    failed = tuple(rs.metric_id for rs in owned if rs.error is not None)
    required = []
    for step in section.plan.steps:
        if isinstance(step, QueryStep):
            required.append(step.metric_id)
    missing = tuple(m for m in dict.fromkeys(required) if m not in acquired and m not in failed)
    notes: list[str] = []
    status: str
    if not owned and not required:
        status = "unsupported"
        notes.append("no_query_steps")
    elif failed and not acquired:
        status = "query_failed"
        notes.append("all_queries_failed")
    elif missing and not acquired:
        # Catalogue was checked (required listed) and queries attempted → unavailable.
        status = "unavailable" if attempts else "unsupported"
        notes.append("required_metrics_missing")
    elif missing or failed:
        status = "partial"
        if missing:
            notes.append("missing:" + ",".join(missing))
        if failed:
            notes.append("failed:" + ",".join(failed))
    else:
        status = "complete"

    # Provisional when a view is stale or the window includes today.
    if status == "complete" and as_of is not None and read_model is not None:
        for rs in owned:
            view = read_model.view_for(rs.metric_id) or ""
            freshness = read_model.view_freshness(view) if view else None
            if freshness and freshness.casefold() in ("stale", "delayed", "provisional"):
                status = "provisional"
                notes.append(f"freshness:{view}:{freshness}")
                break
            if rs.window.start <= as_of.date() <= rs.window.end:
                status = "provisional"
                notes.append("window_includes_today")
                break

    return SectionCoverage(
        section_id=section.section_id,
        status=status,  # type: ignore[arg-type]
        objective=section.objective,
        acquired_metrics=acquired,
        missing_metrics=missing,
        failed_metrics=failed,
        notes=tuple(notes),
        attempts=tuple(a for a in attempts if a.section_id == section.section_id),
    )


def build_report_coverage(
    report_plan: ReportPlan,
    run: PlanRun,
    *,
    stats: AcquireStats,
    read_model: SemanticReadModel | None = None,
    as_of: datetime | None = None,
) -> ReportCoverage:
    sections = tuple(
        section_coverage(
            section,
            run.result_sets,
            attempts=stats.attempts,
            read_model=read_model,
            as_of=as_of,
        )
        for section in report_plan.sections
    )
    statuses = {s.status for s in sections}
    if not sections:
        overall = "unsupported"
    elif statuses <= {"complete"}:
        overall = "complete"
    elif statuses <= {"complete", "provisional"}:
        overall = "provisional"
    elif any(s.status in ("complete", "partial", "provisional") for s in sections):
        overall = "partial"
    elif statuses <= {"query_failed"}:
        overall = "query_failed"
    elif statuses <= {"unavailable"}:
        overall = "unavailable"
    else:
        overall = "unsupported"
    limitations = tuple(
        f"{s.section_id}:{n}" for s in sections for n in s.notes if s.status != "complete"
    )
    return ReportCoverage(
        status=overall,  # type: ignore[arg-type]
        sections=sections,
        query_calls=stats.query_calls,
        cache_hits=stats.cache_hits,
        attempts=tuple(stats.attempts),
        limitations=limitations,
    )


async def run_report_plan(
    report_plan: ReportPlan,
    spec: QuestionSpec,
    *,
    port: SemanticPort | None,
    read_model: SemanticReadModel,
    as_of: datetime,
    evidence_store: Any = None,
    graph_gcm: bool = False,
    deadline_s: float | None = None,
) -> tuple[PlanRun, ReportCoverage, AcquireStats]:
    """Acquire + derive for every section; never discard successful sections."""
    from seleric_swarm.plan._report import report_as_flat_plan

    stats = AcquireStats()
    flat = report_as_flat_plan(report_plan)
    # Prefer per-section acquisition so coverage is attributed correctly.
    windows = {w.role: w for w in (report_plan.windows or spec.windows)}
    run = PlanRun()
    all_sets: list[ResultSet] = []
    for section in report_plan.sections:
        if section.needs_agent:
            continue
        queries = [s for s in section.plan.steps if isinstance(s, QueryStep)]
        if queries:
            sets = await acquire_queries(
                queries,
                windows=windows,
                port=port,
                read_model=read_model,
                as_of=as_of,
                evidence_store=evidence_store,
                section_id=section.section_id,
                stats=stats,
            )
            all_sets.extend(sets)
    run.result_sets = all_sets

    # Run derive/engine steps via the existing runner on a plan that only has those
    # steps, with result sets already populated. Simplest path: rebuild flat plan
    # and call run_plan, replacing its query phase.
    items = {rs.step_id: rs for rs in run.result_sets}
    from seleric_swarm.pipeline._run_plan import _derive

    for section in report_plan.sections:
        for step in section.plan.steps:
            if isinstance(step, DeriveStep):
                d = _derive(step, items, read_model)
                items[step.step_id] = d
                run.derivations.append(d)
            elif isinstance(step, EngineStep):
                # Engine steps still go through run_plan for one section.
                sub = AnalysisPlan(template_id=section.template_id, steps=(step,))
                sub_spec = spec
                eng = await run_plan(
                    sub,
                    sub_spec,
                    port=port,
                    read_model=read_model,
                    as_of=as_of,
                    evidence_store=evidence_store,
                    graph_gcm=graph_gcm,
                    deadline_s=deadline_s,
                )
                run.investigations.update(eng.investigations)
                run.explorations.update(eng.explorations)
                run.derivations.extend(eng.derivations)
                run.result_sets.extend(eng.result_sets)
                run.unsupported.extend(eng.unsupported)
                run.deadline_hit.extend(eng.deadline_hit)

    coverage = build_report_coverage(
        report_plan, run, stats=stats, read_model=read_model, as_of=as_of
    )
    # Attach query_calls onto the run summary via a dynamic attribute.
    run.query_calls = stats.query_calls  # type: ignore[attr-defined]
    run.cache_hits = stats.cache_hits  # type: ignore[attr-defined]
    return run, coverage, stats
