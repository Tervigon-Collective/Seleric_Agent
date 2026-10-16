"""The four analysis stages as registered operators (plan P2.2, P-3).

Each wraps the stage function the legacy order already ran, takes the engine input from the ledger's ``engine_input``
finding and the stages before it from their findings, and returns its own. Importing this module registers them; the
package never does so on its own (``operators/load.py`` is the one explicit place).

| operator   | answers                         | evidence basis | needs                          |
| ---------- | ------------------------------- | -------------- | ------------------------------ |
| is_real    | is the change real, how big     | statistical    | engine_input                   |
| what       | what changed (exact accounting) | identity       | event                          |
| where      | where it sat (segments)         | statistical    | event                          |
| why_event  | which driver explains it        | causal         | event, what (+ an estimator)   |
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, ClassVar

from seleric_swarm.diagnosis.domain.analysis import EventAnalysis, EventResult, WhatResult
from seleric_swarm.diagnosis.domain.errors import InvalidRequest
from seleric_swarm.diagnosis.domain.inputs import DiagnosisInput
from seleric_swarm.diagnosis.domain.investigation import (
    Applicability,
    EvidenceBasis,
    Finding,
    Focus,
    InvestigationContext,
    OperatorResult,
)
from seleric_swarm.diagnosis.operators.base import LedgerView, Ports
from seleric_swarm.diagnosis.operators.is_real import run_is_real
from seleric_swarm.diagnosis.operators.registry import register_operator
from seleric_swarm.diagnosis.operators.what import run_what
from seleric_swarm.diagnosis.operators.where import run_where
from seleric_swarm.diagnosis.operators.why_event import run_why_event

ENGINE_INPUT = "engine_input"  # the fetched series and plan an engine run starts from: {"input": DiagnosisInput}
EVENT, WHAT, WHERE, WHY = "event", "what", "where", "why_event"


def _one(ledger: LedgerView, kind: str) -> dict[str, Any] | None:
    found = ledger.findings(kind)
    return dict(found[-1].data) if found else None


def _analysis(ledger: LedgerView) -> EventAnalysis | None:
    data = _one(ledger, EVENT)
    result: EventResult | None = data["result"] if data else None
    return result.analysis if result is not None else None


def _clock(ports: Ports) -> Callable[[], float]:
    return lambda: ports.runtime.now().timestamp()


def _needs(ok: bool, yes: str, no: str) -> Applicability:
    return Applicability(ok, yes if ok else no)


@register_operator
class IsRealOperator:
    kind: ClassVar[str] = "is_real"
    version: ClassVar[str] = "1"

    def applicable(self, ctx: InvestigationContext, focus: Focus, ledger: LedgerView) -> Applicability:
        if ledger.findings(EVENT):
            return Applicability(False, "already_done")
        return _needs(bool(ledger.findings(ENGINE_INPUT)), "input_ready", "no_engine_input")

    async def run(self, ctx: InvestigationContext, focus: Focus, ports: Ports, ledger: LedgerView) -> OperatorResult:
        inp: DiagnosisInput = _required(ledger, ENGINE_INPUT)["input"]
        result = await asyncio.to_thread(run_is_real, inp, cfg=ctx.config)
        return OperatorResult(findings=(Finding(EVENT, EvidenceBasis.STATISTICAL, ctx.outcome, {"result": result}),))


@register_operator
class WhatOperator:
    kind: ClassVar[str] = "what"
    version: ClassVar[str] = "1"

    def applicable(self, ctx: InvestigationContext, focus: Focus, ledger: LedgerView) -> Applicability:
        if ledger.findings(WHAT):
            return Applicability(False, "already_done")
        return _needs(_analysis(ledger) is not None, "event_known", "no_event_analysis")

    async def run(self, ctx: InvestigationContext, focus: Focus, ports: Ports, ledger: LedgerView) -> OperatorResult:
        ana = _require_analysis(ledger)
        result = await asyncio.to_thread(run_what, ana, cfg=ctx.config, clock=_clock(ports))
        return OperatorResult(findings=(Finding(WHAT, EvidenceBasis.IDENTITY, ctx.outcome, {"result": result}),))


@register_operator
class WhereOperator:
    kind: ClassVar[str] = "where"
    version: ClassVar[str] = "1"

    def applicable(self, ctx: InvestigationContext, focus: Focus, ledger: LedgerView) -> Applicability:
        if ledger.findings(WHERE):
            return Applicability(False, "already_done")
        return _needs(_analysis(ledger) is not None, "event_known", "no_event_analysis")

    async def run(self, ctx: InvestigationContext, focus: Focus, ports: Ports, ledger: LedgerView) -> OperatorResult:
        ana = _require_analysis(ledger)
        dims = await asyncio.to_thread(run_where, ana, cfg=ctx.config)
        return OperatorResult(findings=(Finding(WHERE, EvidenceBasis.STATISTICAL, ctx.outcome, {"result": dims}),))


@register_operator
class WhyEventOperator:
    kind: ClassVar[str] = "why_event"
    version: ClassVar[str] = "1"

    def applicable(self, ctx: InvestigationContext, focus: Focus, ledger: LedgerView) -> Applicability:
        if ledger.findings(WHY):
            return Applicability(False, "already_done")
        ready = _analysis(ledger) is not None and bool(ledger.findings(WHAT))
        return _needs(ready, "event_and_accounting_known", "needs_event_and_what")

    async def run(self, ctx: InvestigationContext, focus: Focus, ports: Ports, ledger: LedgerView) -> OperatorResult:
        if ports.estimator is None:
            raise InvalidRequest("why_event needs an estimator in ports")
        ana = _require_analysis(ledger)
        what: WhatResult = _required(ledger, WHAT)["result"]
        result = await asyncio.to_thread(run_why_event, ana, what, cfg=ctx.config, estimator=ports.estimator)
        return OperatorResult(findings=(Finding(WHY, EvidenceBasis.CAUSAL, ctx.outcome, {"result": result}),))


def _required(ledger: LedgerView, kind: str) -> dict[str, Any]:
    data = _one(ledger, kind)
    if data is None:
        raise InvalidRequest(f"no {kind!r} finding yet: the operator ran before the one that produces it")
    return data


def _require_analysis(ledger: LedgerView) -> EventAnalysis:
    result: EventResult = _required(ledger, EVENT)["result"]
    if result.analysis is None:
        raise InvalidRequest("the event could not be analysed (insufficient data); there is nothing to build on")
    return result.analysis
