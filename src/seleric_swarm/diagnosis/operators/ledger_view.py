"""The simplest ledger that satisfies ``LedgerView``: the findings so far, in order (plan P2.1).

The real ledger (hypotheses, evidence, multiple-testing control) arrives in Phase 5; operators already read through the
``LedgerView`` protocol, so replacing this class changes no operator."""

from __future__ import annotations

from collections.abc import Iterable

from seleric_swarm.diagnosis.domain.investigation import Finding


class FindingsLedger:
    def __init__(self, findings: Iterable[Finding] = ()) -> None:
        self._findings: list[Finding] = list(findings)

    def add(self, findings: Iterable[Finding]) -> None:
        self._findings.extend(findings)

    def findings(self, kind: str) -> tuple[Finding, ...]:
        return tuple(f for f in self._findings if f.kind == kind)

    def unexplained_share(self) -> float:
        return 1.0

    def open_hypotheses(self) -> tuple[str, ...]:
        return ()
