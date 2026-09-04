"""LangGraph state for the Diagnostic workflow."""

from __future__ import annotations

from typing import Any, TypedDict


class DiagnosticState(TypedDict, total=False):
    mission_id: str
    diagnostic_run_id: str
    request: dict[str, Any]

    outcome_metric: str
    degradation_started_at: str
    anomaly_count: int
    evidence_count: int

    hypotheses: list[dict[str, Any]]
    primary_hypothesis_id: str
    causal_confidence: str
    causal_ref: str

    finding: dict[str, Any]
    claims: list[dict[str, Any]]
    limitations: list[str]
    status: str  # running | done | error
    error: str

    # non-serialized carriers
    _context: Any
    _result: Any
    _t0: float
