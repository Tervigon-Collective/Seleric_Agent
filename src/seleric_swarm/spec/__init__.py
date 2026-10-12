"""Spec compiler (L2). Public: ``compile_question_spec``, ``scope_from_spec``, ``compile_report_spec``."""

from __future__ import annotations

from seleric_swarm.spec._compile import compile_question_spec, scope_from_spec
from seleric_swarm.spec._filters import SpecCandidates, ValueHit, candidates_from_resolution
from seleric_swarm.spec._report import as_report_spec, compile_report_spec

__all__ = [
    "SpecCandidates",
    "ValueHit",
    "as_report_spec",
    "candidates_from_resolution",
    "compile_question_spec",
    "compile_report_spec",
    "scope_from_spec",
]
