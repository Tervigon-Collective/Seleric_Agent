"""The operator contract (plan §5.7). An operator is pure with respect to its ports: everything it reads comes
through them, everything random through ``ports.runtime.rng(stream)``, so a replay reproduces it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Protocol

from seleric_swarm.diagnosis.domain.investigation import (
    Applicability,
    Focus,
    InvestigationContext,
    OperatorResult,
)
from seleric_swarm.diagnosis.ports.evidence import EvidencePort
from seleric_swarm.diagnosis.ports.graph import GraphPort
from seleric_swarm.diagnosis.ports.runtime import Runtime
from seleric_swarm.diagnosis.ports.series import SeriesPort


class LedgerView(Protocol):
    """What an operator may read of the hypothesis ledger (it changes the ledger only by proposing updates)."""

    def unexplained_share(self) -> float: ...

    def open_hypotheses(self) -> tuple[str, ...]: ...


@dataclass(frozen=True)
class Ports:
    graph: GraphPort
    series: SeriesPort
    runtime: Runtime
    evidence: tuple[EvidencePort, ...] = ()


class Operator(Protocol):
    kind: ClassVar[str]
    version: ClassVar[str]

    def applicable(self, ctx: InvestigationContext, focus: Focus, ledger: LedgerView) -> Applicability: ...

    async def run(self, ctx: InvestigationContext, focus: Focus, ports: Ports) -> OperatorResult: ...
