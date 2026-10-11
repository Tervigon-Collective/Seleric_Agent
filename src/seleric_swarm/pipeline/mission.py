"""The v2 mission path (enforce): understand → spec → plan → run_plan → answer → turn summary.

``run_mission`` either answers, asks one clarification, or returns ``fallback`` with a reason so
the caller runs the legacy agent loop. It never raises.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from datetime import time as dtime
from typing import Any, Literal

from seleric_swarm.answer import AnswerLLM, Evidence, TurnSummary, answer, render, turn_summary
from seleric_swarm.config.llm_roles import load_prompt
from seleric_swarm.contracts.pipeline import AnswerDocument, Clarification, QuestionSpec
from seleric_swarm.observability import stage_span
from seleric_swarm.pipeline._run_plan import PlanRun, run_plan
from seleric_swarm.pipeline._shadow import (
    Mode,
    ShadowInputs,
    _value_words,
    overview_as_exploration,
    spec_json,
)
from seleric_swarm.plan import plan as build_plan
from seleric_swarm.spec import candidates_from_resolution, compile_question_spec
from seleric_swarm.toolsets import policy_config as P
from seleric_swarm.understand import DraftLLM, understand

_log = logging.getLogger("seleric.pipeline.mission")


@dataclass
class MissionV2:
    status: Literal["answered", "clarify", "fallback"]
    reason: str = ""
    doc: AnswerDocument | None = None
    clarification: Clarification | None = None
    spec: QuestionSpec | None = None
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
        _log.warning("pipeline_v2_mission_failed", exc_info=True, extra={"mission_id": mission_id})
        out = MissionV2(status="fallback", reason=f"error:{type(exc).__name__}")
    out.trace.update(status=out.status, reason=out.reason, latency_ms=round((time.perf_counter() - started) * 1000))
    return out


async def _run(
    llm: DraftLLM | None, answer_llm: AnswerLLM | None, inputs: ShadowInputs, *, mission_id: str, clarify_mode: Mode,
    graph_gcm: bool = False,
) -> MissionV2:
    if inputs.port is None or answer_llm is None or llm is None:
        return MissionV2(status="fallback", reason="v2_unavailable")
    with stage_span("understand", mission_id=mission_id) as span:
        draft, stats = await understand(llm, inputs.question, read_model=inputs.read_model,
                                        value_words=_value_words(inputs.resolution),
                                        prior_question=inputs.prior_question, prior_offer=inputs.prior_offer,
                                        instructions=load_prompt("understand")[0])
        span.set_output(draft)
    trace: dict[str, Any] = {
        "understand": {k: v for k, v in stats.items() if k != "latency_ms"},
        "prompts": {r: load_prompt(r)[1] for r in ("understand", "answer")},
    }
    if isinstance(draft, Clarification):
        return _clarify_or_fallback(draft, clarify_mode, trace)
    draft = overview_as_exploration(draft, inputs.headline_metric_ids)
    if draft.kind != "analysis" and draft.kind != "exploration":
        return MissionV2(status="fallback", reason=f"kind:{draft.kind}", trace=trace)
    with stage_span("spec", mission_id=mission_id, inp=draft) as span:
        spec = compile_question_spec(draft, candidates_from_resolution(dict(inputs.resolution or {})), inputs.read_model,
                                     None, inputs.as_of, clarify_mode=clarify_mode)
        span.set_output(spec)
    if isinstance(spec, Clarification):
        return _clarify_or_fallback(spec, clarify_mode, trace)
    trace["spec"] = spec_json(spec)
    with stage_span("plan", mission_id=mission_id, inp=spec) as span:
        plan = build_plan(spec, inputs.read_model, as_of=inputs.as_of, headline_metric_ids=inputs.headline_metric_ids)
        span.set_output(plan)
    trace["plan"] = {"template_id": plan.template_id, "steps": len(plan.steps)}
    if plan.needs_agent:
        return MissionV2(status="fallback", reason="no_template", spec=spec, trace=trace)
    as_of = inputs.as_of_time or datetime.combine(inputs.as_of, dtime(23, 59))
    with stage_span("execute", mission_id=mission_id, inp=plan):
        run = await run_plan(plan, spec, port=inputs.port, read_model=inputs.read_model, as_of=as_of, graph_gcm=graph_gcm,
                             deadline_s=P.PLAN_DEADLINE_S.get(plan.template_id, P.PLAN_DEADLINE_DEFAULT_S))
    trace["run"] = run.summary()
    if run.unsupported:
        return MissionV2(status="fallback", reason="unsupported_engine", spec=spec, run=run, trace=trace)
    if run.result_sets and all(rs.error is not None for rs in run.result_sets):
        return MissionV2(status="fallback", reason="no_data", spec=spec, run=run, trace=trace)
    evidence = Evidence(run.evidence)
    with stage_span("answer", mission_id=mission_id, inp=spec) as span:
        doc, astats = await answer(answer_llm, inputs.question, spec=spec, evidence=evidence, read_model=inputs.read_model,
                                    instructions=load_prompt("answer")[0])
        span.set_output(doc)
    trace["answer"] = {k: v for k, v in astats.items() if k in ("attempts", "issues", "status")}
    if run.deadline_hit and doc.draft.status != "failed":
        # M5.3: the answer was written from a run the latency tier cut short; say so.
        cut = doc.draft.model_copy(update={"status": "partial", "limitations": (
            *doc.draft.limitations, f"time_limit:{','.join(run.deadline_hit)}")})
        doc = render(cut, evidence, inputs.read_model)
    if doc.draft.status == "failed":
        return MissionV2(status="fallback", reason="answer_failed", spec=spec, run=run, trace=trace)
    turn = turn_summary(spec, doc.draft, evidence, inputs.read_model)
    return MissionV2(status="answered", doc=doc, spec=spec, run=run, turn=turn, trace=trace)


def _clarify_or_fallback(c: Clarification, clarify_mode: Mode, trace: dict[str, Any]) -> MissionV2:
    trace["clarification"] = c.reason
    if clarify_mode == "enforce" and c.reason != "understanding_unavailable":
        return MissionV2(status="clarify", clarification=c, trace=trace)
    return MissionV2(status="fallback", reason=f"clarification:{c.reason}", trace=trace)
