"""The v2 stages in shadow (understand → spec → plan) beside the legacy mission path.

``start_shadow`` launches the v2 stages as a background task right after the catalogue and
value reads, so they run concurrently with the legacy understand call and the agent loop and
add no latency. ``collect_shadow`` waits briefly at the end of the mission, compares the v2
outcome with what the legacy path decided (``LegacyDecision``) and returns a compact record
for ``trace["v2_shadow"]``. Nothing the user sees changes: shadow never raises and never
feeds the legacy path.

Each stage runs inside ``stage_span`` (M0.3) so disagreements are inspectable in Langfuse.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from datetime import time as dtime
from typing import Any, Literal

from seleric_swarm.config.llm_roles import load_prompt
from seleric_swarm.contracts.pipeline import (
    AnalysisPlan,
    Clarification,
    MeasurePhrase,
    QueryStep,
    QuestionDraft,
    QuestionSpec,
)
from seleric_swarm.observability import stage_span
from seleric_swarm.pipeline._memory import apply_brand_defaults, with_assumptions
from seleric_swarm.pipeline._run_plan import PlanRun, run_plan
from seleric_swarm.plan import plan as build_plan
from seleric_swarm.plan import plan_notes
from seleric_swarm.semantic import SemanticPort, SemanticReadModel
from seleric_swarm.spec import candidates_from_resolution, compile_question_spec
from seleric_swarm.understand import DraftLLM, understand

_log = logging.getLogger("seleric.pipeline.shadow")

Mode = Literal["off", "shadow", "enforce"]


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


@dataclass(frozen=True)
class ShadowModes:
    understand: Mode = "off"
    planner: Mode = "off"
    clarify: Mode = "off"
    # Answer shadow runs the plan (real reads through ``port``) and drafts a v2 answer.
    answer: Mode = "off"
    graph_gcm: Mode = "off"

    @property
    def active(self) -> bool:
        # The planner reads the v2 spec, so planner shadow implies understand + spec.
        return self.understand != "off" or self.planner != "off" or self.answer != "off"


@dataclass(frozen=True)
class ShadowInputs:
    question: str
    as_of: date
    read_model: SemanticReadModel
    resolution: Mapping[str, Any] | None = None
    prior_question: str = ""
    prior_offer: str = ""
    headline_metric_ids: tuple[str, ...] = ()
    port: SemanticPort | None = None
    as_of_time: datetime | None = None
    prior_spec: QuestionSpec | None = None
    evidence_store: Any = None  # plan.EvidenceStore: persists v2 rows as mission evidence
    brand_prefs: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class LegacyDecision:
    """What the legacy path decided, as plain data (the pipeline never imports ``agent``)."""

    kind: str | None = None
    shape: str | None = None
    metric_ids: tuple[str, ...] = ()
    windows: tuple[tuple[date, date], ...] = ()
    # Named filter values. Legacy filters span several candidate dimensions, so values are compared.
    filter_values: tuple[str, ...] = ()


@dataclass
class ShadowOutcome:
    status: str = "pending"
    draft: QuestionDraft | Clarification | None = None
    spec: QuestionSpec | Clarification | None = None
    plan: AnalysisPlan | None = None
    notes: tuple[str, ...] = ()
    run: PlanRun | None = None
    answer: dict[str, Any] = field(default_factory=dict)
    stats: dict[str, Any] = field(default_factory=dict)
    latency_ms: int = 0


def start_shadow(
    llm: DraftLLM | None,
    inputs: ShadowInputs,
    *,
    modes: ShadowModes,
    mission_id: str,
    answer_llm: AnswerLLM | None = None,
) -> asyncio.Task[ShadowOutcome] | None:
    """Launch the v2 stages in the background; None when every v2 flag is off."""
    if not modes.active:
        return None
    return asyncio.create_task(
        _run(llm, inputs, modes=modes, mission_id=mission_id, answer_llm=answer_llm), name=f"v2_shadow:{mission_id}"
    )


async def collect_shadow(
    task: asyncio.Task[ShadowOutcome] | None,
    legacy: LegacyDecision,
    *,
    timeout_s: float = 2.0,
) -> dict[str, Any] | None:
    """The shadow record for the mission trace; never raises. A task still running is cancelled."""
    if task is None:
        return None
    try:
        outcome = await asyncio.wait_for(asyncio.shield(task), timeout=timeout_s)
    except TimeoutError:
        task.cancel()
        with contextlib.suppress(BaseException):
            await task
        return {"status": "timeout"}
    except Exception as exc:  # the task itself catches; this guards the await
        return {"status": "failed", "error": type(exc).__name__}
    record = summarise(outcome, legacy)
    _log.info("pipeline_v2_shadow", extra={"v2_shadow": record})
    return record


async def _run(
    llm: DraftLLM | None,
    inputs: ShadowInputs,
    *,
    modes: ShadowModes,
    mission_id: str,
    answer_llm: AnswerLLM | None = None,
) -> ShadowOutcome:
    started = time.perf_counter()
    outcome = ShadowOutcome()
    try:
        with stage_span("understand", mission_id=mission_id) as span:
            draft, stats = await understand(
                llm,
                inputs.question,
                read_model=inputs.read_model,
                value_words=_value_words(inputs.resolution),
                prior_question=inputs.prior_question,
                prior_offer=inputs.prior_offer,
                instructions=load_prompt("understand")[0],
            )
            _apply_gen_ai(span, stats)
            span.set_output(draft)
        if not isinstance(draft, Clarification):
            draft = overview_as_exploration(
                draft, inputs.headline_metric_ids, read_model=inputs.read_model
            )
        outcome.draft, outcome.stats = draft, dict(stats)
        if isinstance(draft, Clarification):
            outcome.status = "clarification"
            return outcome
        draft, defaults = apply_brand_defaults(draft, inputs.brand_prefs)
        with stage_span("spec", mission_id=mission_id, inp=draft) as span:
            spec = compile_question_spec(
                draft,
                candidates_from_resolution(dict(inputs.resolution or {})),
                inputs.read_model,
                inputs.prior_spec,
                inputs.as_of,
                clarify_mode=modes.clarify,
            )
            if isinstance(spec, QuestionSpec):
                spec = with_assumptions(spec, defaults)
            span.set_output(spec)
        outcome.spec = spec
        if isinstance(spec, Clarification):
            outcome.status = "clarification"
            return outcome
        if modes.planner != "off" or modes.answer != "off":
            with stage_span("plan", mission_id=mission_id, inp=spec) as span:
                as_of, headline = inputs.as_of, inputs.headline_metric_ids
                outcome.plan = build_plan(spec, inputs.read_model, as_of=as_of, headline_metric_ids=headline)
                outcome.notes = plan_notes(spec, inputs.read_model, as_of=as_of, headline_metric_ids=headline)
                span.set_output(outcome.plan)
            if modes.answer != "off" and inputs.port is not None and not outcome.plan.needs_agent:
                await _answer_shadow(answer_llm, inputs, spec, outcome, mission_id=mission_id, modes=modes)
        outcome.status = "ok"
    except Exception as exc:
        _log.warning("pipeline_v2_shadow_failed", exc_info=True, extra={"mission_id": mission_id})
        outcome.status = "failed"
        outcome.stats["error"] = type(exc).__name__
    finally:
        outcome.latency_ms = round((time.perf_counter() - started) * 1000)
    return outcome


async def _answer_shadow(
    llm: "AnswerLLM" | None,
    inputs: ShadowInputs,
    spec: QuestionSpec,
    outcome: ShadowOutcome,
    *,
    mission_id: str,
    modes: ShadowModes | None = None,
) -> None:
    from seleric_swarm.answer import AnswerLLM, Evidence, answer, verify

    modes = modes or ShadowModes()
    assert outcome.plan is not None
    as_of = inputs.as_of_time or datetime.combine(inputs.as_of, dtime(23, 59))
    with stage_span("execute", mission_id=mission_id, inp=outcome.plan):
        outcome.run = await run_plan(
            outcome.plan,
            spec,
            port=inputs.port,
            read_model=inputs.read_model,
            as_of=as_of,
            graph_gcm=modes.graph_gcm != "off",
        )
    evidence = Evidence(outcome.run.evidence)
    with stage_span("answer", mission_id=mission_id, inp=spec) as span:
        doc, stats = await answer(
            llm,
            inputs.question,
            spec=spec,
            evidence=evidence,
            read_model=inputs.read_model,
            instructions=load_prompt("answer")[0],
        )
        _apply_gen_ai(span, stats)
        span.set_output(doc)
    outcome.answer = {
        "status": doc.draft.status,
        "issues": verify(doc.draft, spec, evidence, read_model=inputs.read_model).codes(),
        "attempts": stats.get("attempts"),
        "chars": len(doc.rendered_markdown),
        "evidence_ids": len(doc.evidence_ids),
        "input_tokens": stats.get("input_tokens"),
        "output_tokens": stats.get("output_tokens"),
    }


def overview_as_exploration(
    draft: QuestionDraft,
    headline_metric_ids: tuple[str, ...],
    *,
    read_model: SemanticReadModel | None = None,
    scope_axes: tuple[tuple[str, str], ...] = (),
) -> QuestionDraft:
    """An overview that names a period is explored (the headline measures over that period);
    without a period it stays an overview (the snapshot fast path answers it). An overview
    scoped to an entity, breakdown or catalogue axis is not the business headline: left as is.

    Headline ids that the live catalogue does not carry are dropped so one missing
    registry→catalogue mapping cannot fail the whole mission as ``invalid_spec``.
    An empty read model (tests / warm-up) keeps the ids as written — same rule as the planner.
    """
    if draft.kind != "overview" or not draft.names_period:
        return draft
    if not draft.measures and (names_scope(draft) or scope_axes):
        return draft
    ids = _known_headlines(headline_metric_ids, read_model)
    measures = draft.measures or tuple(MeasurePhrase(words=m, metric_id=m) for m in ids)
    return draft.model_copy(update={"kind": "exploration", "measures": measures})


def underspecified_as_exploration(
    draft: QuestionDraft,
    headline_metric_ids: tuple[str, ...],
    *,
    read_model: SemanticReadModel | None = None,
    scope_axes: tuple[tuple[str, str], ...] = (),
) -> QuestionDraft:
    """Analysis/composition with no resolved measures cannot match a template.

    Fall back to exploration over the configured headline set (same recovery the
    overview path uses) so ``V2_NO_FALLBACK`` does not die as ``no_template``.
    Objectives that already name measures are left alone, and so is any question that is not
    open-ended: one scoped to an entity or breakdown, one whose words bound a catalogue axis
    (``scope=product``), or one the model said it could not read without asking.
    """
    if draft.kind not in ("analysis", "exploration"):
        return draft
    if draft.measures or any(obj.measures for obj in draft.objectives):
        return draft
    # The headline set would answer a different question; leave the draft for the fidelity gate
    # to refuse (→ legacy fallback).
    if names_scope(draft) or scope_axes or draft.needs_clarification:
        return draft
    ids = _known_headlines(headline_metric_ids, read_model)
    if not ids:
        return draft
    measures = tuple(MeasurePhrase(words=m, metric_id=m) for m in ids)
    # Composition/why without a parent metric cannot plan; exploration can.
    shape = draft.shape if draft.shape not in ("composition", "why_single_metric") else "lookup"
    return draft.model_copy(update={"kind": "exploration", "shape": shape, "measures": measures})


def names_scope(draft: QuestionDraft) -> bool:
    """The draft narrows the question to an entity, a breakdown, or named values."""
    if draft.entity_dimension.strip() or draft.entity_phrases or draft.exclusion_phrases:
        return True
    if any(d.strip() for d in draft.breakdown_dimensions):
        return True
    return any(
        obj.entity_dimension.strip() or obj.entity_phrases or any(d.strip() for d in obj.breakdown_dimensions)
        for obj in draft.objectives
    )


def _known_headlines(
    headline_metric_ids: tuple[str, ...],
    read_model: SemanticReadModel | None,
) -> tuple[str, ...]:
    ids = tuple(headline_metric_ids)
    if read_model is not None:
        known = read_model.metric_ids()
        if known:
            ids = tuple(mid for mid in headline_metric_ids if mid in known)
    return ids


def summarise(outcome: ShadowOutcome, legacy: LegacyDecision) -> dict[str, Any]:
    """Compact record: what v2 decided and, per field, whether it agrees with the legacy path."""
    record: dict[str, Any] = {
        "status": outcome.status,
        "latency_ms": outcome.latency_ms,
        "prompts": {r: load_prompt(r)[1] for r in ("understand", "answer")},
    }
    if outcome.stats:
        record["understand"] = {k: v for k, v in outcome.stats.items() if k != "latency_ms"}
    if isinstance(outcome.draft, Clarification) or isinstance(outcome.spec, Clarification):
        clar = outcome.draft if isinstance(outcome.draft, Clarification) else outcome.spec
        assert isinstance(clar, Clarification)
        record["clarification"] = {"reason": clar.reason, "question": clar.question_to_user}
    disagree: list[str] = []
    if isinstance(outcome.draft, QuestionDraft):
        record["kind"], record["shape"] = outcome.draft.kind, outcome.draft.shape
        if legacy.kind is not None and legacy.kind != outcome.draft.kind:
            disagree.append("kind")
        if legacy.shape is not None and legacy.shape != outcome.draft.shape:
            disagree.append("shape")
    spec = outcome.spec if isinstance(outcome.spec, QuestionSpec) else None
    if spec is not None:
        record["spec"] = spec_json(spec)
        windows = tuple(sorted((w.start, w.end) for w in spec.windows))
        record["windows"] = [[s.isoformat(), e.isoformat()] for s, e in windows]
        if legacy.windows and tuple(sorted(legacy.windows)) != windows:
            disagree.append("windows")
        named = [f for f in spec.filters if f.operator == "in"]
        record["filters"] = sorted(f"{f.dimension}={','.join(f.values)}" for f in named)
        excluded = [f for f in spec.filters if f.operator == "not_in"]
        if excluded:
            record["exclusions"] = sorted(f"{f.dimension}!={','.join(f.values)}" for f in excluded)
        if _lower(legacy.filter_values) != _lower(v for f in named for v in f.values):
            disagree.append("filters")
        record["assumptions"] = list(spec.assumptions)
    if outcome.plan is not None:
        metrics = _plan_metrics(outcome.plan)
        record["plan"] = {
            "template_id": outcome.plan.template_id,
            "needs_agent": outcome.plan.needs_agent,
            "steps": len(outcome.plan.steps),
            "metrics": sorted(metrics),
        }
        if outcome.notes:
            record["plan"]["notes"] = list(outcome.notes)
        if legacy.metric_ids and set(legacy.metric_ids) != metrics:
            disagree.append("metrics")
    if outcome.run is not None:
        record["run"] = outcome.run.summary()
    if outcome.answer:
        record["answer"] = outcome.answer
    record["disagree"] = disagree
    return record


_ROLE_ORDER = {"event": 0, "baseline": 1, "context": 2}


def spec_json(spec: QuestionSpec) -> dict[str, Any]:
    """The spec as JSON with windows in canonical role order (what the eval scorer compares)."""
    data = spec.model_dump(mode="json")
    data["windows"] = sorted(data.get("windows") or [], key=lambda w: _ROLE_ORDER.get(w.get("role"), 9))
    return data


def _plan_metrics(plan: AnalysisPlan) -> set[str]:
    metrics = {s.metric_id for s in plan.steps if isinstance(s, QueryStep)}
    for step in plan.steps:
        params = getattr(step, "params", {}) or {}
        if isinstance(params.get("metric_id"), str):
            metrics.add(params["metric_id"])
        for key in ("drivers", "metric_ids"):
            if isinstance(params.get(key), list):
                metrics.update(m for m in params[key] if isinstance(m, str))
    return metrics


def _lower(values: Iterable[str]) -> frozenset[str]:
    return frozenset(v.strip().lower() for v in values)


def _value_words(resolution: Mapping[str, Any] | None) -> list[str]:
    terms = (resolution or {}).get("terms")
    if not isinstance(terms, list):
        return []
    return [str(t.get("term")) for t in terms if isinstance(t, dict) and t.get("term")]
