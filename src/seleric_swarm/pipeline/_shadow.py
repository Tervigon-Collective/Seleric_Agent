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
from datetime import date
from typing import Any, Literal

from seleric_swarm.contracts.pipeline import (
    AnalysisPlan,
    Clarification,
    QueryStep,
    QuestionDraft,
    QuestionSpec,
)
from seleric_swarm.observability import stage_span
from seleric_swarm.plan import plan as build_plan
from seleric_swarm.plan import plan_notes
from seleric_swarm.semantic import SemanticReadModel
from seleric_swarm.spec import candidates_from_resolution, compile_question_spec
from seleric_swarm.understand import DraftLLM, understand

_log = logging.getLogger("seleric.pipeline.shadow")

Mode = Literal["off", "shadow", "enforce"]


@dataclass(frozen=True)
class ShadowModes:
    understand: Mode = "off"
    planner: Mode = "off"
    clarify: Mode = "off"

    @property
    def active(self) -> bool:
        # The planner reads the v2 spec, so planner shadow implies understand + spec.
        return self.understand != "off" or self.planner != "off"


@dataclass(frozen=True)
class ShadowInputs:
    question: str
    as_of: date
    read_model: SemanticReadModel
    resolution: Mapping[str, Any] | None = None
    prior_question: str = ""
    prior_offer: str = ""
    headline_metric_ids: tuple[str, ...] = ()


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
    stats: dict[str, Any] = field(default_factory=dict)
    latency_ms: int = 0


def start_shadow(
    llm: DraftLLM | None,
    inputs: ShadowInputs,
    *,
    modes: ShadowModes,
    mission_id: str,
) -> asyncio.Task[ShadowOutcome] | None:
    """Launch the v2 stages in the background; None when every v2 flag is off."""
    if not modes.active:
        return None
    return asyncio.create_task(_run(llm, inputs, modes=modes, mission_id=mission_id), name=f"v2_shadow:{mission_id}")


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


async def _run(llm: DraftLLM | None, inputs: ShadowInputs, *, modes: ShadowModes, mission_id: str) -> ShadowOutcome:
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
            )
            span.set_output(draft)
        outcome.draft, outcome.stats = draft, dict(stats)
        if isinstance(draft, Clarification):
            outcome.status = "clarification"
            return outcome
        with stage_span("spec", mission_id=mission_id, inp=draft) as span:
            spec = compile_question_spec(
                draft,
                candidates_from_resolution(dict(inputs.resolution or {})),
                inputs.read_model,
                None,
                inputs.as_of,
                clarify_mode=modes.clarify,
            )
            span.set_output(spec)
        outcome.spec = spec
        if isinstance(spec, Clarification):
            outcome.status = "clarification"
            return outcome
        if modes.planner != "off":
            with stage_span("plan", mission_id=mission_id, inp=spec) as span:
                as_of, headline = inputs.as_of, inputs.headline_metric_ids
                outcome.plan = build_plan(spec, inputs.read_model, as_of=as_of, headline_metric_ids=headline)
                outcome.notes = plan_notes(spec, inputs.read_model, as_of=as_of, headline_metric_ids=headline)
                span.set_output(outcome.plan)
        outcome.status = "ok"
    except Exception as exc:
        _log.warning("pipeline_v2_shadow_failed", exc_info=True, extra={"mission_id": mission_id})
        outcome.status = "failed"
        outcome.stats["error"] = type(exc).__name__
    finally:
        outcome.latency_ms = round((time.perf_counter() - started) * 1000)
    return outcome


def summarise(outcome: ShadowOutcome, legacy: LegacyDecision) -> dict[str, Any]:
    """Compact record: what v2 decided and, per field, whether it agrees with the legacy path."""
    record: dict[str, Any] = {"status": outcome.status, "latency_ms": outcome.latency_ms}
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
    record["disagree"] = disagree
    return record


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
