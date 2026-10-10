"""Spec compiler (L2). Public: ``compile_question_spec``, ``scope_from_spec``."""

from __future__ import annotations

from seleric_swarm.spec._compile import compile_question_spec, scope_from_spec
from seleric_swarm.spec._filters import SpecCandidates, ValueHit, candidates_from_resolution

__all__ = [
    "SpecCandidates",
    "ValueHit",
    "candidates_from_resolution",
    "compile_question_spec",
    "scope_from_spec",
]
