"""The v2 mission path (enforce): understand → spec → plan → run_plan → answer → turn summary.

``run_mission`` either answers, asks one clarification, or returns ``fallback`` with a reason so
the caller runs the legacy agent loop. It never raises.

AG-08: multi-objective ReportPlan path with coverage-aware acquisition and grounded blocks.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from datetime import time as dtime
from typing import TYPE_CHECKING, Any, Literal

from seleric_swarm.config.llm_roles import load_prompt
from seleric_swarm.contracts.pipeline import (
    AnswerDocument,
    Clarification,
    QuestionDraft,
    QuestionSpec,
)
from seleric_swarm.contracts.pipeline._report import ReportCoverage, ReportPlan, ReportSpec
from seleric_swarm.observability import stage_span
from seleric_swarm.pipeline._acquire import run_report_plan
from seleric_swarm.pipeline._fidelity import (
    asked_dimensions,
    explicit_metric_ids,
    intent_lost,
    scope_axes,
    scope_uncovered,
)
from seleric_swarm.pipeline._memory import apply_brand_defaults, with_assumptions
from seleric_swarm.pipeline._run_plan import PlanRun, run_plan
from seleric_swarm.pipeline._shadow import (
    Mode,
    ShadowInputs,
    _value_words,
    overview_as_exploration,
    spec_json,
    underspecified_as_exploration,
)
from seleric_swarm.plan import plan as build_plan
from seleric_swarm.plan._report import plan_report, report_as_flat_plan, single_section_report_from_spec
from seleric_swarm.spec import candidates_from_resolution, compile_question_spec
from seleric_swarm.spec._names import resolve_draft_names
from seleric_swarm.spec._report import compile_report_spec
from seleric_swarm.toolsets import policy_config as P
from seleric_swarm.understand import DraftLLM, understand

if TYPE_CHECKING:
    from seleric_swarm.answer import AnswerLLM, TurnSummary

_log = logging.getLogger("seleric.pipeline.mission")


def _apply_gen_ai(span: Any, stats: dict[str, Any]) -> None:
    """Push an LLM stage's model + token usage onto its span (M0.3/M5.1)."""
    model = stats.get("model")
    if not model:
        return
    span.set_gen_ai(
        model=str(model),
        input_tokens=stats.get("input_tokens"),
        output_tokens=stats.get("output_tokens"),
    )


@dataclass
class MissionV2:
    status: Literal["answered", "clarify", "fallback"]
    reason: str = ""
    doc: AnswerDocument | None = None
    clarification: Clarification | None = None
    spec: QuestionSpec | None = None
    report_spec: ReportSpec | None = None
    report_plan: ReportPlan | None = None
    coverage: ReportCoverage | None = None
    run: PlanRun | None = None
    turn: TurnSummary | None = None
    trace: dict[str, Any] = field(default_factory=dict)


async def run_mission(
    llm: DraftLLM | None,
    answer_llm: AnswerLLM | None,
    inputs: ShadowInputs,
    *,
    mission_id: str,
    clarify_mode: Mode = "off",
    graph_gcm: bool = False,
) -> MissionV2:
    started = time.perf_counter()
    out = MissionV2(status="fallback")
    try:
        out = await _run(llm, answer_llm, inputs, mission_id=mission_id, clarify_mode=clarify_mode, graph_gcm=graph_gcm)
    except Exception as exc:
        # Lazy answer imports avoid answer↔pipeline circular init; still log the message.
        _log.warning(
            "pipeline_v2_mission_failed",
            exc_info=True,
            extra={"mission_id": mission_id, "error": f"{type(exc).__name__}: {exc}"},
        )
        out = MissionV2(status="fallback", reason=f"error:{type(exc).__name__}:{exc}")
    out.trace.update(status=out.status, reason=out.reason, latency_ms=round((time.perf_counter() - started) * 1000))
    return out


async def _run(
    llm: DraftLLM | None,
    answer_llm: AnswerLLM | None,
    inputs: ShadowInputs,
    *,
    mission_id: str,
    clarify_mode: Mode,
    graph_gcm: bool = False,
) -> MissionV2:
    # Imported lazily: answer.__init__ → _assemble → pipeline → mission would
    # otherwise circular-import and surface as NameError under enforce.
    from seleric_swarm.answer import Evidence, answer, render, turn_summary
    from seleric_swarm.answer._assemble import assemble

    if inputs.port is None or answer_llm is None or llm is None:
        return MissionV2(status="fallback", reason="v2_unavailable")
    with stage_span("understand", mission_id=mission_id) as span:
        draft, stats = await understand(llm, inputs.question, read_model=inputs.read_model,
                                        value_words=_value_words(inputs.resolution),
                                        prior_question=inputs.prior_question, prior_offer=inputs.prior_offer,
                                        instructions=load_prompt("understand")[0])
        _apply_gen_ai(span, stats)
        span.set_output(draft)
    trace: dict[str, Any] = {
        "understand": {k: v for k, v in stats.items() if k != "latency_ms"},
        "prompts": {r: load_prompt(r)[1] for r in ("understand", "answer")},
    }
    if isinstance(draft, Clarification):
        return _clarify_or_fallback(draft, clarify_mode, trace)
    asked = resolve_draft_names(draft, inputs.read_model)
    axes = scope_axes(candidates_from_resolution(dict(inputs.resolution or {})).axes)
    draft = overview_as_exploration(
        draft, inputs.headline_metric_ids, read_model=inputs.read_model, scope_axes=axes
    )
    defaulted_measures = not asked.measures and bool(draft.measures)
    if draft.kind != "analysis" and draft.kind != "exploration":
        return MissionV2(status="fallback", reason=f"kind:{draft.kind}", trace=trace)
    draft = await resolve_measures(draft, inputs, clarify_mode=clarify_mode)
    resolved = draft
    draft = underspecified_as_exploration(
        draft, inputs.headline_metric_ids, read_model=inputs.read_model, scope_axes=axes
    )
    defaulted_measures = defaulted_measures or draft.measures != resolved.measures
    draft, defaults = apply_brand_defaults(draft, inputs.brand_prefs)

    # AG-08: multi-objective → ReportSpec; else legacy QuestionSpec wrapped as one section.
    report_spec: ReportSpec | None = None
    with stage_span("spec", mission_id=mission_id, inp=draft) as span:
        if draft.objectives:
            compiled = compile_report_spec(
                draft,
                candidates_from_resolution(dict(inputs.resolution or {})),
                inputs.read_model,
                inputs.prior_spec,
                inputs.as_of,
                clarify_mode=clarify_mode,
            )
            if isinstance(compiled, Clarification):
                span.set_output(compiled)
                return _clarify_or_fallback(compiled, clarify_mode, trace)
            if isinstance(compiled, ReportSpec):
                report_spec = compiled.model_copy(
                    update={"assumptions": (*compiled.assumptions, *defaults)}
                )
                # Bridge QuestionSpec for answer/turn_summary compatibility.
                spec = _question_spec_from_report(report_spec)
            else:
                spec = with_assumptions(compiled, defaults)
                report_spec = single_section_report_from_spec(spec)
        else:
            spec = compile_question_spec(
                draft,
                candidates_from_resolution(dict(inputs.resolution or {})),
                inputs.read_model,
                inputs.prior_spec,
                inputs.as_of,
                clarify_mode=clarify_mode,
            )
            if isinstance(spec, Clarification):
                span.set_output(spec)
                return _clarify_or_fallback(spec, clarify_mode, trace)
            spec = with_assumptions(spec, defaults)
            report_spec = single_section_report_from_spec(spec)
        span.set_output(report_spec or spec)

    if isinstance(spec, Clarification):
        return _clarify_or_fallback(spec, clarify_mode, trace)

    # AG-08: if the user named a calendar span (e.g. 1-11th October, month of
    # October) but the draft left windows empty, infer them from the question.
    if not spec.windows:
        from seleric_swarm.spec._windows import infer_absolute_window

        inferred = infer_absolute_window(inputs.question, inputs.as_of)
        if inferred is not None:
            spec = spec.model_copy(update={"windows": (inferred,)})
            if report_spec is not None:
                report_spec = report_spec.model_copy(update={"windows": (inferred,)})
            trace["window_inferred"] = f"{inferred.start}..{inferred.end}"

    trace["spec"] = spec_json(spec)
    lost = intent_lost(
        asked,
        spec,
        inputs.read_model,
        report_spec=report_spec,
        defaulted_measures=defaulted_measures,
        axes=axes,
    )
    if lost:
        trace["fidelity"] = lost
        _log.info("v2_intent_lost", extra={"mission_id": mission_id, "lost": lost})
        return MissionV2(
            status="fallback",
            reason="intent_lost:" + ";".join(lost),
            spec=spec,
            report_spec=report_spec,
            trace=trace,
        )
    if report_spec is not None:
        trace["report_spec"] = {
            "sections": [
                {
                    "id": s.section_id,
                    "objective": s.objective,
                    "shape": s.shape,
                    "metrics": list(s.metric_ids),
                    "intent": s.presentation_intent,
                }
                for s in report_spec.sections
            ],
            "windows": [f"{w.role} {w.start}..{w.end}" for w in report_spec.windows],
        }

    with stage_span("plan", mission_id=mission_id, inp=report_spec or spec) as span:
        report_plan = plan_report(
            report_spec,
            inputs.read_model,
            as_of=inputs.as_of,
            headline_metric_ids=inputs.headline_metric_ids,
            kind=spec.kind,
        )
        flat = report_as_flat_plan(report_plan)
        span.set_output(report_plan)
    trace["plan"] = {
        "template_id": flat.template_id,
        "steps": len(flat.steps),
        "sections": [
            {
                "id": s.section_id,
                "template_id": s.template_id,
                "needs_agent": s.needs_agent,
                "steps": len(s.plan.steps),
            }
            for s in report_plan.sections
        ],
    }
    if report_plan.needs_agent or flat.needs_agent:
        return MissionV2(
            status="fallback",
            reason="no_template",
            spec=spec,
            report_spec=report_spec,
            report_plan=report_plan,
            trace=trace,
        )

    as_of = inputs.as_of_time or datetime.combine(inputs.as_of, dtime(23, 59))
    deadline = P.PLAN_DEADLINE_S.get("report", P.PLAN_DEADLINE_DEFAULT_S)
    if len(report_plan.sections) == 1:
        deadline = P.PLAN_DEADLINE_S.get(
            report_plan.sections[0].template_id, P.PLAN_DEADLINE_DEFAULT_S
        )

    with stage_span("execute", mission_id=mission_id, inp=report_plan):
        if len(report_plan.sections) == 1 and not draft.objectives:
            # Fast path: existing single-template executor (lookup/trend regression).
            run = await run_plan(
                flat,
                spec,
                port=inputs.port,
                read_model=inputs.read_model,
                as_of=as_of,
                graph_gcm=graph_gcm,
                evidence_store=inputs.evidence_store,
                deadline_s=deadline,
            )
            from seleric_swarm.pipeline._acquire import AcquireStats, build_report_coverage

            stats = AcquireStats(
                query_calls=len([rs for rs in run.result_sets]),
            )
            run.query_calls = stats.query_calls
            coverage = build_report_coverage(
                report_plan, run, stats=stats, read_model=inputs.read_model, as_of=as_of
            )
        else:
            run, coverage, _stats = await run_report_plan(
                report_plan,
                spec,
                port=inputs.port,
                read_model=inputs.read_model,
                as_of=as_of,
                evidence_store=inputs.evidence_store,
                graph_gcm=graph_gcm,
                deadline_s=deadline,
            )

    trace["run"] = run.summary()
    trace["coverage"] = coverage.model_dump(mode="json")

    if run.unsupported and not any(rs.error is None for rs in run.result_sets):
        return MissionV2(
            status="fallback",
            reason="unsupported_engine",
            spec=spec,
            report_spec=report_spec,
            report_plan=report_plan,
            coverage=coverage,
            run=run,
            trace=trace,
        )

    uncovered = scope_uncovered(
        asked_dimensions(asked, inputs.read_model),
        explicit_metric_ids(inputs.question, resolved, inputs.read_model),
        run.result_sets,
        inputs.read_model,
    )
    if uncovered:
        trace["fidelity"] = uncovered
        _log.info("v2_scope_uncovered", extra={"mission_id": mission_id, "uncovered": uncovered})
        return MissionV2(
            status="fallback",
            reason="scope_uncovered:" + ";".join(uncovered),
            spec=spec,
            report_spec=report_spec,
            report_plan=report_plan,
            coverage=coverage,
            run=run,
            trace=trace,
        )

    # AG08-08: fall back only when every section is unusable.
    usable = any(
        s.status in ("complete", "partial", "provisional") for s in coverage.sections
    ) or any(rs.error is None and rs.rows for rs in run.result_sets)
    if not usable:
        return MissionV2(
            status="fallback",
            reason="no_data",
            spec=spec,
            report_spec=report_spec,
            report_plan=report_plan,
            coverage=coverage,
            run=run,
            trace=trace,
        )

    evidence = Evidence(run.evidence)
    skeleton = assemble(report_plan, run, coverage, inputs.read_model)

    with stage_span("answer", mission_id=mission_id, inp=spec) as span:
        doc, astats = await answer(
            answer_llm,
            inputs.question,
            spec=spec,
            evidence=evidence,
            read_model=inputs.read_model,
            instructions=load_prompt("answer")[0],
            skeleton=skeleton,
        )
        _apply_gen_ai(span, astats)
        span.set_output(doc)
    trace["answer"] = {
        k: v
        for k, v in astats.items()
        if k in ("attempts", "issues", "status", "input_tokens", "output_tokens", "model")
    }

    if run.deadline_hit and doc.draft.status != "failed":
        cut = doc.draft.model_copy(
            update={
                "status": "partial",
                "limitations": (
                    *doc.draft.limitations,
                    f"time_limit:{','.join(run.deadline_hit)}",
                ),
            }
        )
        doc = render(cut, evidence, inputs.read_model)

    # Align draft status with coverage when assembler already knew it was partial.
    if coverage.status != "complete" and doc.draft.status == "completed":
        cut = doc.draft.model_copy(
            update={
                "status": "partial",
                "limitations": (*doc.draft.limitations, *coverage.limitations[:6]),
            }
        )
        doc = render(cut, evidence, inputs.read_model)

    # Legacy single-section path: a failed answer still falls back to the agent.
    # Multi-objective reports keep any assembled skeleton as a partial answer.
    multi = bool(draft.objectives) or len(report_plan.sections) > 1
    if doc.draft.status == "failed" and (not usable or not multi):
        return MissionV2(
            status="fallback",
            reason="answer_failed",
            spec=spec,
            report_spec=report_spec,
            report_plan=report_plan,
            coverage=coverage,
            run=run,
            trace=trace,
        )

    turn = turn_summary(spec, doc.draft, evidence, inputs.read_model)
    return MissionV2(
        status="answered",
        doc=doc,
        spec=spec,
        report_spec=report_spec,
        report_plan=report_plan,
        coverage=coverage,
        run=run,
        turn=turn,
        trace=trace,
    )


def _question_spec_from_report(report: ReportSpec) -> QuestionSpec:
    """Flatten ReportSpec into a QuestionSpec for answer coverage checks."""
    from seleric_swarm.contracts.pipeline import MeasureSpec

    measures = []
    breakdowns: list[str] = []
    ranking = None
    shape = report.sections[0].shape if report.sections else "other"
    for section in report.sections:
        for mid in section.metric_ids:
            measures.append(MeasureSpec(phrase=mid, metric_id=mid))
        for d in section.dimensions:
            if d not in breakdowns:
                breakdowns.append(d)
        if section.ranking is not None and ranking is None:
            ranking = section.ranking
        if section.shape == "funnel":
            shape = "funnel"
    return QuestionSpec(
        kind="analysis",
        shape=shape,
        measures=tuple(measures),
        filters=report.filters,
        entities=tuple(report.entities),
        breakdowns=tuple(breakdowns),
        windows=report.windows,
        grain=report.sections[0].grain if report.sections else None,
        ranking=ranking,
        catalogue_version=report.catalogue_version,
        follow_up_of=report.follow_up_of,
        assumptions=report.assumptions,
    )


async def resolve_measures(draft: QuestionDraft, inputs: ShadowInputs, *, clarify_mode: Mode) -> QuestionDraft:
    """A measure the draft could not name exactly is resolved by the catalogue's concept search
    (Core owns synonyms). With clarification off, an ambiguous concept takes its first suggestion."""
    if inputs.port is None:
        return draft
    known = inputs.read_model.metric_ids()
    labels = {(m.label or "").casefold(): m.id for m in inputs.read_model.metrics}

    async def _one(m):
        if m.metric_id in known or (m.metric_id or "").casefold() in labels or not m.words:
            # Map label → id when the model put a display name in metric_id.
            if m.metric_id and m.metric_id not in known:
                mapped = labels.get(m.metric_id.casefold())
                if mapped:
                    return m.model_copy(update={"metric_id": mapped})
            return m
        try:
            res = await inputs.port.resolve_concept(m.words)
        except Exception:
            _log.warning("resolve_concept_failed", exc_info=True)
            return m
        pick = res.metric_id or (
            res.suggestions[0] if res.suggestions and clarify_mode != "enforce" else None
        )
        return m.model_copy(update={"metric_id": pick}) if pick and pick in known else m

    out = []
    for m in draft.measures:
        out.append(await _one(m))
    objectives = []
    for obj in draft.objectives:
        obj_measures = tuple([await _one(m) for m in obj.measures])
        rank_by = await _one(obj.rank_by) if obj.rank_by is not None else None
        objectives.append(obj.model_copy(update={"measures": obj_measures, "rank_by": rank_by}))
    return draft.model_copy(update={"measures": tuple(out), "objectives": tuple(objectives)})


def _clarify_or_fallback(c: Clarification, clarify_mode: Mode, trace: dict[str, Any]) -> MissionV2:
    trace["clarification"] = c.reason
    trace["clarification_text"] = c.question_to_user[:200]
    if clarify_mode == "enforce" and c.reason != "understanding_unavailable":
        return MissionV2(status="clarify", clarification=c, trace=trace)
    return MissionV2(status="fallback", reason=f"clarification:{c.reason}", trace=trace)
