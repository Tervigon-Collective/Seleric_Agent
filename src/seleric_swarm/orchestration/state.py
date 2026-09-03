from __future__ import annotations

from typing import Any, TypedDict


class MissionState(TypedDict, total=False):
    mission_id: str
    request_id: str
    session_id: str
    user_query: str
    timezone: str
    as_of: str | None
    mode: str
    workflow_name: str
    workflow_version: str
    status: str
    query_class: str
    mission_lead: str
    active_specialist: str
    leadership_epoch: int
    task_id: str
    entities: list[str]
    time_range: dict[str, Any]
    metric_hints: list[str]
    metric_id: str | None
    allowed_metrics: list[str]
    unsupported_reason: str | None
    task_graph: dict[str, Any]
    tasks: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    evidence_refs: list[str]
    claims: list[dict[str, Any]]
    limitations: list[str]
    anomaly_refs: list[str]
    hypothesis_refs: list[str]
    causal_refs: list[str]
    forecast_refs: list[str]
    strategy_refs: list[str]
    skeptic_findings: list[dict[str, Any]]
    handoff_history: list[dict[str, Any]]
    handoff_needed_metrics: list[str]
    pending_transfer: dict[str, Any] | None
    initial_mission_lead: str
    final_response: str
    error_code: str | None
    error_message: str | None
    llm_calls: int
    tool_calls: int
    mcp_called: bool
    langsmith_run_id: str | None
    started_at: str
    synthesis_fallback: bool
