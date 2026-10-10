"""Typed derivation ops (M2.3): all arithmetic as recorded, refusable operations.

``derive(op, inputs, params, read_model=...)`` is the one public entry point.
Every result is a ``Derivation`` citing its input refs; an op that is not
defined for the catalogue returns a ``Derivation`` with ``error`` set, never an
exception.
"""

from seleric_swarm.derive._engine import derive

__all__ = ["derive"]
