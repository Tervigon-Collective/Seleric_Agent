"""The operator contract (plan §5.7, P2.1): ``run(ctx, focus, ports, ledger) -> OperatorResult``.

An operator is pure with respect to what it is handed: what it reads of the world comes through ``ports``, what the
operators before it found comes through the read-only ``ledger`` view, everything random through
``ports.runtime.rng(stream)``, so a replay reproduces it. It never changes the ledger: it returns the findings, ledger
updates, follow-up foci and trace events it proposes, and the caller applies them."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Protocol

from seleric_swarm.diagnosis.domain.investigation import (
    Applicability,
    Finding,
    Focus,
    InvestigationContext,
    OperatorResult,
)
from seleric_swarm.diagnosis.ports.estimator import EffectEstimator
from seleric_swarm.diagnosis.ports.evidence import EvidencePort
from seleric_swarm.diagnosis.ports.formatting import ValueFormatting
from seleric_swarm.diagnosis.ports.graph import GraphPort
from seleric_swarm.diagnosis.ports.runtime import Runtime
from seleric_swarm.diagnosis.ports.series import SeriesPort


class LedgerView(Protocol):
    """What an operator may read of the hypothesis ledger (it changes the ledger only by proposing updates)."""

    def unexplained_share(self) -> float: ...

    def open_hypotheses(self) -> tuple[str, ...]: ...

    def findings(self, kind: str) -> tuple[Finding, ...]:
        """What the operators that already ran found, of one kind, in the order they found it."""
        ...


@dataclass(frozen=True)
class Ports:
    graph: GraphPort
    series: SeriesPort
    runtime: Runtime
    evidence: tuple[EvidencePort, ...] = ()
    estimator: EffectEstimator | None = None  # the driver estimates (WHY_EVENT)
    formatting: ValueFormatting | None = None  # how the report writes a value


class Operator(Protocol):
    kind: ClassVar[str]
    version: ClassVar[str]

    def applicable(self, ctx: InvestigationContext, focus: Focus, ledger: LedgerView) -> Applicability: ...

    async def run(self, ctx: InvestigationContext, focus: Focus, ports: Ports, ledger: LedgerView) -> OperatorResult: ...
