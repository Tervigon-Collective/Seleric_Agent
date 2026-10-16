"""The legacy-order engine as a replayable runner (plan P2.3, closes the replay half of Gate 1).

The request carries the engine input (``input_to_dict``: the fetched series and the plan's choices); the runner runs the
four registered operators in the order the engine always ran them, assembles the report and writes the trace. The
series travel in the request, so a record of a run is self-contained and a replay needs no network. The estimates and
the clock readings it uses are recorded by ``record_run`` and served back by ``replay``, so a replayed run is
byte-identical in events and report.

Not yet in the live tool's path: the tool still fetches outside the ports (plan §12, session 4); it runs the same stage
functions through ``operators/pipeline.run_stages``."""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar

from seleric_swarm.diagnosis.adapters.catalogue_graph import VendoredGraphAdapter
from seleric_swarm.diagnosis.adapters.service_formatting import ServiceFormatting
from seleric_swarm.diagnosis.domain.analysis import EventResult, WhatResult
from seleric_swarm.diagnosis.domain.canonical import fingerprint
from seleric_swarm.diagnosis.domain.errors import InvalidRequest
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
from seleric_swarm.diagnosis.operators.ledger_view import FindingsLedger
from seleric_swarm.diagnosis.operators.load import register_engine_operators
from seleric_swarm.diagnosis.operators.pipeline import STAGES, StageResults
from seleric_swarm.diagnosis.operators.summary import summarise
from seleric_swarm.diagnosis.report.assemble import assemble_report
from seleric_swarm.diagnosis.trace import events as kinds
from seleric_swarm.diagnosis.trace.emitter import clean_payload
from seleric_swarm.diagnosis.trace.report_events import emit_report_events
from seleric_swarm.diagnosis.trace.run import RunInputs, register_runner


def request_for(inp: DiagnosisInput, scope: dict[str, Any] | None = None) -> dict[str, Any]:
    """The run request for an engine input (what ``record_run`` is given)."""
    return {"engine_input": input_to_dict(inp), "scope": dict(scope or {})}


@register_runner
class LegacyEngineRunner:
    kind: ClassVar[str] = "legacy_engine"

    async def run(self, inputs: RunInputs) -> dict[str, Any]:
        inp = input_from_dict(inputs.request["engine_input"])
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

