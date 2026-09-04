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
    status_reason: str | None
    query_class: str
    mission_lead: str
    active_specialist: str | None
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
    # Coordinator control-plane annotations (deterministic, audit-only unless the
    # execution cycle consumes them).
    complexity: int
    complexity_label: str
    decomposed_questions: list[dict[str, Any]]
    plan_dispatchable: bool
    plan_blocked_reasons: list[str]
    lead_selection: dict[str, Any]
    coordinator_iterations: int
    agent_calls: int
    completion_score: float
    completion_decision: str
    completion_components: dict[str, float]
    unresolved_questions: list[str]
    contradictions: list[dict[str, Any]]
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
    # Coordinator V1 / swarm_v2 extensions (serialize dicts only — no live clients).
    execution_mode: str
    synthetic: bool
    normalized_query: dict[str, Any]
    decomposition_refs: list[str]
    current_decomposition_ref: str | None
    decompositions: list[dict[str, Any]]
    objectives: list[dict[str, Any]]
    claim_refs: list[str]
    validated_claim_refs: list[str]
    challenged_claim_refs: list[str]
    rejected_claim_refs: list[str]
    managed_claims: list[dict[str, Any]]
    evidence_gaps: list[dict[str, Any]]
    conflicts: list[dict[str, Any]]
    remediation_round: int
    remediation_tasks: list[dict[str, Any]]
    budgets: dict[str, Any]
    usage: dict[str, Any]
    budget_exhausted: bool
    events: list[dict[str, Any]]
    skeptic_refs: list[str]
    prediction_refs: list[str]
    team: list[dict[str, Any]]
    completion_detail: dict[str, Any]
    specialists_activated: int
    langsmith_tracing: bool
    trace_base: dict[str, Any]
