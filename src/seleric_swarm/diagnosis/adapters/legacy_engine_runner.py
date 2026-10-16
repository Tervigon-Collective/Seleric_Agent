"""The legacy-order engine as a replayable runner (plan P2.3, closes the replay half of Gate 1).

The request carries the engine input (``input_to_dict``: the fetched series and the plan's choices); the runner runs the
four registered operators in the order the engine always ran them, assembles the report and writes the trace. The
series travel in the request, so a record of a run is self-contained and a replay needs no network. The estimates and
the clock readings it uses are recorded by ``record_run`` and served back by ``replay``, so a replayed run is
byte-identical in events and report.

The request carries either the engine input itself or the ``GatherPlan`` the input was gathered by (plan P2.6): with a
plan the runner gathers from its series port, so a record of a live tool call re-derives the input from the recorded
series. The live tool hands the runner the input it already gathered (``prepared``) and the series it fetched go into
the record from its own recording port, so nothing is fetched twice."""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar

from seleric_swarm.diagnosis.adapters.catalogue_graph import VendoredGraphAdapter
from seleric_swarm.diagnosis.adapters.service_formatting import ServiceFormatting
from seleric_swarm.diagnosis.domain.analysis import EventResult, WhatResult
from seleric_swarm.diagnosis.domain.canonical import fingerprint
from seleric_swarm.diagnosis.domain.errors import InvalidRequest
from seleric_swarm.diagnosis.domain.gather import GatherPlan
from seleric_swarm.diagnosis.domain.inputs import DiagnosisInput, input_from_dict, input_to_dict
from seleric_swarm.diagnosis.domain.investigation import (
    EvidenceBasis,
    Finding,
    Focus,
    InvestigationContext,
)
from seleric_swarm.diagnosis.domain.types import Scope, Window
from seleric_swarm.diagnosis.operators.base import Ports
from seleric_swarm.diagnosis.operators.engine_ops import ENGINE_INPUT
from seleric_swarm.diagnosis.operators.gather import gather
from seleric_swarm.diagnosis.operators.ledger_view import FindingsLedger
from seleric_swarm.diagnosis.operators.load import register_engine_operators
from seleric_swarm.diagnosis.operators.pipeline import STAGES, StageResults
from seleric_swarm.diagnosis.operators.summary import summarise
from seleric_swarm.diagnosis.report.assemble import assemble_report
from seleric_swarm.diagnosis.trace import events as kinds
from seleric_swarm.diagnosis.trace.active import activate_emitter
from seleric_swarm.diagnosis.trace.emitter import clean_payload
from seleric_swarm.diagnosis.trace.report_events import emit_report_events
from seleric_swarm.diagnosis.trace.run import RunInputs, register_runner


def request_for(inp: DiagnosisInput, scope: dict[str, Any] | None = None) -> dict[str, Any]:
    """The run request for an engine input (what ``record_run`` is given)."""
    return {"engine_input": input_to_dict(inp), "scope": dict(scope or {})}


def request_for_plan(plan: GatherPlan) -> dict[str, Any]:
    """The run request for a gather plan: the input is re-derived from the series port when the run is replayed."""
    return {"plan": plan.to_dict(), "scope": {k: v for k, v in plan.scope.items()}}


@register_runner
class LegacyEngineRunner:
    kind: ClassVar[str] = "legacy_engine"

    def __init__(self, prepared: DiagnosisInput | None = None) -> None:
        self._prepared = prepared
        self.report_object: Any = None  # the report as an object, for a caller that goes on to use it (the live tool)

    async def _input(self, inputs: RunInputs) -> DiagnosisInput:
        if self._prepared is not None:
            return self._prepared
        if "plan" in inputs.request:
            gathered = await gather(GatherPlan.from_dict(inputs.request["plan"]), inputs.series, cfg=inputs.config)
            if gathered.input is None:
                raise InvalidRequest(f"the recorded plan gathers no engine input ({gathered.failure})")
            return gathered.input
        return input_from_dict(inputs.request["engine_input"])

    async def run(self, inputs: RunInputs) -> dict[str, Any]:
        inp = await self._input(inputs)
        if not inp.event_days:
            raise InvalidRequest("the request names no event days")
        operators = register_engine_operators()
        graph = inputs.graph if inputs.graph is not None else VendoredGraphAdapter().load().graph
        window = Window(min(inp.event_days), max(inp.event_days))
        ctx = InvestigationContext(inp.outcome, window, Scope.of(inputs.request.get("scope") or None), graph, inputs.config, inputs.seed)
        focus = Focus(inp.outcome, ctx.scope, window)
        ports = Ports(
            graph=VendoredGraphAdapter(), series=inputs.series, runtime=inputs.runtime,
            estimator=inputs.estimator, formatting=ServiceFormatting(),
        )
        emitter = inputs.emitter
        emitter.emit(kinds.INVESTIGATION_STARTED, {"outcome": inp.outcome, "scope": dict(inputs.request.get("scope") or {})})
        emitter.emit(kinds.WINDOW_RESOLVED, {"start": window.start.isoformat(), "end": window.end.isoformat(), "baseline_days": len(inp.baseline_days)})

        ledger = FindingsLedger([Finding(ENGINE_INPUT, EvidenceBasis.PRIOR, inp.outcome, {"input": inp})])
        results = StageResults()
        # A step that fails and falls back to a weaker one reports it on the active emitter (trace/active.py): here it
        # is this run's, so the failure is in the record and a replay reports it again.
        with activate_emitter(emitter):
            for stage in STAGES:
                operator = operators.get(stage)()
                verdict = operator.applicable(ctx, focus, ledger)
                if not verdict.applicable:
                    if stage != "is_real" and results.event is not None and results.event.analysis is None:
                        break  # insufficient data: the first stage already said so
                    raise InvalidRequest(f"{stage} is not applicable: {verdict.reason_code}")
                began = self._now(inputs)
                started = emitter.emit(kinds.OPERATOR_STARTED, {"operator": stage, "inputs": {}})
                result = await operator.run(ctx, focus, ports, ledger)
                ledger.add(result.findings)
                self._store(results, stage, result.findings[0].data["result"])
                emitter.emit(
                    kinds.OPERATOR_FINISHED,
                    {
                        "operator": stage, "query_count": result.cost.queries,
                        "duration_ms": round((self._now(inputs) - began).total_seconds() * 1000),
                        "result_digest": fingerprint(clean_payload(summarise(stage, results))),
                    },
                    parent=started.seq,
                )
                if stage == "is_real" and results.event is not None and results.event.analysis is None:
                    break
        report = self._report(results, inputs, ports)
        self.report_object = report
        emit_report_events(lambda kind, payload, parent: emitter.emit(kind, payload, parent=parent).seq, report)
        emitter.emit(
            kinds.INVESTIGATION_FINISHED,
            {"termination_reason": report.verdict, "events": emitter.last_seq, "sink_failures": len(emitter.sink_failures), "instrumentation_errors": 0},
        )
        return clean_payload(report.as_dict())

    @staticmethod
    def _now(inputs: RunInputs) -> datetime:
        return inputs.runtime.now()

    @staticmethod
    def _store(results: StageResults, stage: str, value: Any) -> None:
        if stage == "is_real":
            results.event = value
        elif stage == "what":
            results.what = value
        elif stage == "where":
            results.dims = value
        else:
            results.why = value

    @staticmethod
    def _report(results: StageResults, inputs: RunInputs, ports: Ports) -> Any:
        event: EventResult | None = results.event
        assert event is not None and ports.formatting is not None
        if event.analysis is None:
            assert event.early is not None
            return event.early
        what: WhatResult | None = results.what
        assert what is not None and results.dims is not None and results.why is not None
        return assemble_report(event.analysis, what, results.dims, results.why, cfg=inputs.config, fmt=ports.formatting)

