"""The stages in the order the engine always ran them (plan P2.3). ``diagnose`` is what ``causal/diagnosis.py`` always
exposed: the same inputs give the same report, now assembled from named, separately testable stages.

``run_stages`` is the one place the order is written down; the tool, the operator wrappers and the fixture tests all
go through it, so they cannot disagree about what runs when."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from typing import Any

from seleric_swarm.diagnosis.domain.analysis import EventResult, WhatResult, WhyResult
from seleric_swarm.diagnosis.domain.config import DiagnosisConfig
from seleric_swarm.diagnosis.domain.errors import InvalidRequest
from seleric_swarm.diagnosis.domain.inputs import DiagnosisInput
from seleric_swarm.diagnosis.domain.report import DiagnosisReport, DimensionFinding
from seleric_swarm.diagnosis.operators.is_real import run_is_real
from seleric_swarm.diagnosis.operators.what import run_what
from seleric_swarm.diagnosis.operators.where import run_where
from seleric_swarm.diagnosis.operators.why_event import run_why_event
from seleric_swarm.diagnosis.ports.estimator import EffectEstimator
from seleric_swarm.diagnosis.ports.formatting import ValueFormatting
from seleric_swarm.diagnosis.report.assemble import assemble_report

STAGES = ("is_real", "what", "where", "why_event")


class StageScope:
    """What a stage reports about itself when it closes (shown in the trace)."""

    result: Mapping[str, Any] = {}


ScopeFactory = Callable[[str, dict[str, Any]], AbstractContextManager[Any]]


def _no_scope(name: str, inputs: dict[str, Any]) -> AbstractContextManager[Any]:
    return nullcontext(StageScope())


@dataclass(frozen=True)
class EngineEnv:
    """The collaborators a diagnosis needs besides its input; every one of them is injected."""

    config: DiagnosisConfig
    estimator: EffectEstimator
    formatting: ValueFormatting
    clock: Callable[[], float]  # seconds on a monotonic clock; only the bridge search's time budget reads it
    scope: ScopeFactory = field(default=_no_scope)


@dataclass
class StageResults:
    """What each stage produced; a stage that did not run is ``None``."""

    event: EventResult | None = None
    what: WhatResult | None = None
    dims: list[DimensionFinding] | None = None
    why: WhyResult | None = None
    report: DiagnosisReport | None = None


def run_stages(inp: DiagnosisInput, env: EngineEnv, *, until: str | None = None) -> StageResults:
    """Run the stages in order. ``until`` names the last stage to run (the report is assembled only when all ran)."""
    if until is not None and until not in STAGES:
        raise InvalidRequest(f"no stage {until!r}; the stages are {list(STAGES)}")
    cfg, out = env.config, StageResults()
    with env.scope("is_real", {"event_days": len(inp.event_days)}) as s:
        out.event = run_is_real(inp, cfg=cfg)
        s.result = {"early": out.event.early is not None}
    if out.event.analysis is None:
        out.report = out.event.early
        return out
    ana = out.event.analysis
    if until == "is_real":
        return out
    with env.scope("what", {"history_days": len(ana.history)}) as s:
        out.what = run_what(ana, cfg=cfg, clock=env.clock)
        s.result = {"decomposition": len(out.what.decomposition), "bridge": len(out.what.bridge), "chain": len(out.what.chain)}
    if until == "what":
        return out
    with env.scope("where", {"dimensions": len(inp.segments.get(ana.y) or {})}) as s:
        out.dims = run_where(ana, cfg=cfg)
        s.result = {"dimensions": len(out.dims)}
    if until == "where":
        return out
    with env.scope("why_event", {"candidate_drivers": len(inp.candidate_drivers)}) as s:
        out.why = run_why_event(ana, out.what, cfg=cfg, estimator=env.estimator)
        s.result = {"drivers": len(out.why.drivers), "implicated": len(out.why.implicated)}
    if until == "why_event":
        return out
    out.report = assemble_report(ana, out.what, out.dims, out.why, cfg=cfg, fmt=env.formatting)
    return out


def diagnose(inp: DiagnosisInput, env: EngineEnv) -> DiagnosisReport:
    report = run_stages(inp, env).report
    assert report is not None  # a full run always ends in a report
    return report
