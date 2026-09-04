"""LangGraph state for the Prediction workflow."""

from __future__ import annotations

from typing import Any, TypedDict


class PredictionState(TypedDict, total=False):
    mission_id: str
    prediction_run_id: str
    request: dict[str, Any]

    target_metric: str
    horizon: str
    history_points: int
    drift_status: str

    source: str
    applicability: str
    confidence: str
    forecast_ref: str
    scenarios: list[dict[str, Any]]
    claims: list[dict[str, Any]]
    limitations: list[str]
    status: str  # running | done
    error: str

    _context: Any
    _result: Any
    _t0: float
