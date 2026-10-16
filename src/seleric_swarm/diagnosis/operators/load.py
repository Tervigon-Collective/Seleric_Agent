"""The one explicit place that makes the shipped operators available (plan P-3). Importing the package registers
nothing; a caller that wants the engine's operators calls this."""

from __future__ import annotations

from seleric_swarm.diagnosis.domain.registry import Registry
from seleric_swarm.diagnosis.operators.base import Operator
from seleric_swarm.diagnosis.operators.registry import OPERATORS


def register_engine_operators() -> Registry[Operator]:
    """Import the operator modules (which register themselves) and return the registry. Safe to call repeatedly."""
    from seleric_swarm.diagnosis.operators import (
        engine_ops,  # noqa: F401  (imported for its registrations)
    )

    return OPERATORS
