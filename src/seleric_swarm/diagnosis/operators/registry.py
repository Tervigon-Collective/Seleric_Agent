"""The operator registry and the one question the scheduler asks of it."""

from __future__ import annotations

from seleric_swarm.diagnosis.domain.investigation import Applicability, Focus, InvestigationContext
from seleric_swarm.diagnosis.domain.registry import Registry
from seleric_swarm.diagnosis.operators.base import LedgerView, Operator

OPERATORS: Registry[Operator] = Registry("operator")
register_operator = OPERATORS.register


def applicable_operators(
    ctx: InvestigationContext,
    focus: Focus,
    ledger: LedgerView,
    registry: Registry[Operator] = OPERATORS,
) -> list[tuple[Operator, Applicability]]:
    """Every registered operator that applies to ``focus``, in kind order, each with its stated reason and cost.

    The refusals are not returned here; the scheduler asks ``all_verdicts`` when it must explain why not."""
    return [(op, verdict) for op, verdict in all_verdicts(ctx, focus, ledger, registry) if verdict.applicable]


def all_verdicts(
    ctx: InvestigationContext,
    focus: Focus,
    ledger: LedgerView,
    registry: Registry[Operator] = OPERATORS,
) -> list[tuple[Operator, Applicability]]:
    return [(op, op.applicable(ctx, focus, ledger)) for op in registry.create_all()]
