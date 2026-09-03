from __future__ import annotations
from typing import Any, TypedDict


class MissionState(TypedDict, total=False):
    mission_id: str
    user_query: str
    normalized_query: str
    status: str
    mission_lead: str
    active_specialist: str
    leadership_epoch: int
    task_graph: dict[str, Any]
    tasks: list[dict[str, Any]]
    evidence_refs: list[str]
    anomaly_refs: list[str]
    hypothesis_refs: list[str]
    causal_refs: list[str]
    forecast_refs: list[str]
    strategy_refs: list[str]
    skeptic_findings: list[dict[str, Any]]
    claims: list[dict[str, Any]]
    handoff_history: list[dict[str, Any]]
    final_response: str
