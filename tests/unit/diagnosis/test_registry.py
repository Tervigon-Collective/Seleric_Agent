"""Registries (plan P1.12, D-29): a new extension is a new class, not a core edit."""

from __future__ import annotations

from datetime import date
from typing import ClassVar

import pytest

from seleric_swarm.diagnosis.adapters.catalogue_graph import VendoredGraphAdapter
from seleric_swarm.diagnosis.domain.config import DiagnosisConfig
from seleric_swarm.diagnosis.domain.graph import Edge, EdgeKind, Graph
from seleric_swarm.diagnosis.domain.investigation import (
    Applicability,
    Cost,
    EvidenceBasis,
    Finding,
    Focus,
    InvestigationContext,
    OperatorResult,
)
from seleric_swarm.diagnosis.domain.registry import Registry, RegistryError
from seleric_swarm.diagnosis.domain.types import Scope, Window
from seleric_swarm.diagnosis.graph.edge_policy import EDGE_POLICIES, EdgePolicy, PolicyDecision
from seleric_swarm.diagnosis.operators.base import LedgerView, Operator, Ports
from seleric_swarm.diagnosis.operators.registry import all_verdicts, applicable_operators
from seleric_swarm.diagnosis.ports.evidence import DatedEvidence, EvidencePort

WINDOW = Window(date(2026, 9, 1), date(2026, 9, 3))


class Ledger:
    def unexplained_share(self) -> float:
        return 1.0

    def open_hypotheses(self) -> tuple[str, ...]:
        return ()

    def findings(self, kind: str) -> tuple[Finding, ...]:
        return ()


@pytest.fixture(scope="module")
def ctx() -> InvestigationContext:
    graph = VendoredGraphAdapter().load().graph
    return InvestigationContext("net_sales", WINDOW, Scope(), graph, DiagnosisConfig(), seed=1)


def _focus(node: str) -> Focus:
    return Focus(node, Scope(), WINDOW)


def _dummy(kind: str, applies_to: str):
    class Dummy:
        version: ClassVar[str] = "1"

        def applicable(self, ctx: InvestigationContext, focus: Focus, ledger: LedgerView) -> Applicability:
            ok = focus.node == applies_to
            return Applicability(ok, "matches" if ok else "other_node", Cost(queries=1))

        async def run(self, ctx: InvestigationContext, focus: Focus, ports: Ports, ledger: LedgerView) -> OperatorResult:
            return OperatorResult(findings=(Finding("seen", EvidenceBasis.IDENTITY, focus.node, {}),))

    Dummy.kind = kind
    return Dummy


def test_a_dummy_operator_is_found_and_selected_without_any_scheduler_code(ctx):
    registry: Registry[Operator] = Registry("operator")
    registry.register(_dummy("zeta", "orders"))
    registry.register(_dummy("alpha", "orders"))
    registry.register(_dummy("mid", "sessions"))
    assert registry.kinds() == ("alpha", "mid", "zeta")  # by kind, not by registration order
    chosen = applicable_operators(ctx, _focus("orders"), Ledger(), registry)
    assert [op.kind for op, _ in chosen] == ["alpha", "zeta"] and chosen[0][1].reason_code == "matches"
    refused = {op.kind: v.reason_code for op, v in all_verdicts(ctx, _focus("orders"), Ledger(), registry) if not v.applicable}
    assert refused == {"mid": "other_node"}


async def test_a_selected_operator_runs_through_the_contract(ctx):
    registry: Registry[Operator] = Registry("operator")
    registry.register(_dummy("alpha", "orders"))
    [(op, _)] = applicable_operators(ctx, _focus("orders"), Ledger(), registry)
    result = await op.run(ctx, _focus("orders"), None, Ledger())  # type: ignore[arg-type]
    assert result.findings[0].subject == "orders" and result.cost == Cost()


def test_duplicate_or_missing_kinds_are_refused():
    registry: Registry[Operator] = Registry("operator")
    registry.register(_dummy("alpha", "x"))
    with pytest.raises(RegistryError):
        registry.register(_dummy("alpha", "y"))

    class NoKind:
        pass

    with pytest.raises(RegistryError):
        registry.register(NoKind)  # type: ignore[arg-type]
    with pytest.raises(RegistryError):
        registry.get("nope")


def test_the_shipped_registries_start_empty_and_other_extension_points_use_the_same_type(ctx):
    from seleric_swarm.diagnosis.ports.evidence_registry import EVIDENCE

    assert EDGE_POLICIES.kinds() == () and EVIDENCE.kinds() == ()  # nothing registers on import
    # (the operator registry is checked in a fresh interpreter: another test may have called the explicit loader)
    import subprocess
    import sys

    code = "import seleric_swarm.diagnosis.operators.registry as r; print(r.OPERATORS.kinds())"
    assert subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.strip() == "()"

    class TwinPolicy:
        kind: ClassVar[str] = EdgeKind.TWIN.value

        def decide(self, edge: Edge, graph: Graph) -> PolicyDecision:
            return PolicyDecision("exclude", "same_quantity")

    class Prices:
        kind: ClassVar[str] = "price"

        async def events(self, scope: Scope, window: Window) -> list[DatedEvidence]:
            return []

    policies: Registry[EdgePolicy] = Registry("edge policy")
    policies.register(TwinPolicy)
    twin = next(e for e in ctx.graph.edges if e.kind is EdgeKind.TWIN)
    assert policies.get("twin")().decide(twin, ctx.graph).reason_code == "same_quantity"
    evidence: Registry[EvidencePort] = Registry("evidence port")
    evidence.register(Prices)
    assert evidence.kinds() == ("price",)
