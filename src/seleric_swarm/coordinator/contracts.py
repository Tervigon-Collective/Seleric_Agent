"""Coordinator V1 contracts — progressive decomposition, tasks, claims, completion."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

MissionStatus = Literal[
    "received",
    "normalizing",
    "decomposing",
    "planning",
    "assembled",
    "running",
    "waiting",
    "remediating",
    "validating",
    "blocked",
    "partial",
    "completed",
    "prototype_completed",
    "failed",
    "cancelled",
]

SubQuestionStatus = Literal[
    "pending",
    "ready",
    "running",
    "answered",
    "blocked",
    "irrelevant",
    "superseded",
]

ClaimState = Literal[
    "PROPOSED",
    "SUPPORTED",
    "CHALLENGED",
    "VALIDATED",
    "REJECTED",
    "SUPERSEDED",
]

ComplexityBand = Literal["L0", "L1", "L2", "L3", "L4", "L5"]


class EntityRef(BaseModel):
    entity_type: str
    entity_id: str | None = None
    raw: str
    resolved: bool = False
    resolution_reason: str | None = None


class TimeRange(BaseModel):
    start: str
    end: str
    timezone: str = "Asia/Kolkata"
    label: str | None = None


class EvidenceRequirement(BaseModel):
    requirement_id: str
    description: str
    artifact_types: list[str] = Field(default_factory=list)
    metric_ids: list[str] = Field(default_factory=list)
    blocking: bool = False


class MissionObjective(BaseModel):
    objective_id: str
    description: str
    priority: int = 5
    status: Literal["pending", "satisfied", "unresolved", "blocked"] = "pending"
    required_claim_states: list[ClaimState] = Field(default_factory=lambda: ["VALIDATED"])


class MissionScope(BaseModel):
    domains: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    timezone: str = "Asia/Kolkata"
    as_of: str | None = None


class MissionBudget(BaseModel):
    max_agent_calls: int = 30
    max_a2a_calls: int = 40
    max_mcp_calls: int = 20
    max_llm_calls: int = 6
    max_remediation_rounds: int = 3
    max_leadership_transfers: int = 6
    max_runtime_s: float = 120.0
    max_parallel_tasks: int = 4
    token_budget: int | None = None


class MissionRequest(BaseModel):
    query: str
    user_id: str | None = None
    session_id: str | None = None
    scope: MissionScope = Field(default_factory=MissionScope)
    execution_mode: Literal["production", "staging", "fixture"] = "production"
    requested_outputs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class NormalizedQuery(BaseModel):
    original_query: str
    intents: list[str] = Field(default_factory=list)
    primary_metric: str | None = None
    secondary_metrics: list[str] = Field(default_factory=list)
    entities: list[EntityRef] = Field(default_factory=list)
    time_range: TimeRange | None = None
    comparison_range: TimeRange | None = None
    requested_outputs: list[str] = Field(default_factory=list)
    candidate_domains: list[str] = Field(default_factory=list)
    unresolved_semantics: list[str] = Field(default_factory=list)
    metric_resolution_reason: str | None = None


class SubQuestion(BaseModel):
    question_id: str
    mission_id: str
    decomposition_id: str
    question: str
    purpose: str
    parent_question_id: str | None = None
    required_capabilities: list[str] = Field(default_factory=list)
    required_artifact_types: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    priority: int = 5
    expected_information_gain: float | None = None
    status: SubQuestionStatus = "pending"
    answer_refs: list[str] = Field(default_factory=list)
    branch: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProblemDecomposition(BaseModel):
    decomposition_id: str
    mission_id: str
    root_question: str
    parent_decomposition_id: str | None = None
    version: int = 1
    objectives: list[MissionObjective] = Field(default_factory=list)
    subquestions: list[SubQuestion] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    required_evidence: list[EvidenceRequirement] = Field(default_factory=list)
    candidate_domains: list[str] = Field(default_factory=list)
    created_from_evidence_refs: list[str] = Field(default_factory=list)
    reason_for_revision: str | None = None
    status: Literal["active", "superseded", "complete"] = "active"
    questions_added: list[str] = Field(default_factory=list)
    questions_retired: list[str] = Field(default_factory=list)
    template: str | None = None


class TaskSpec(BaseModel):
    task_id: str
    mission_id: str
    objective_id: str | None = None
    subquestion_id: str | None = None
    task_type: str
    objective: str
    requested_capabilities: list[str] = Field(default_factory=list)
    preferred_domain: str | None = None
    requested_artifacts: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    input_refs: list[str] = Field(default_factory=list)
    priority: int = 5
    blocking: bool = False
    idempotency_key: str
    status: str = "pending"
    assigned_agent: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MissionPlan(BaseModel):
    mission_id: str
    normalized_query: NormalizedQuery
    complexity: ComplexityBand
    decomposition_ref: str
    objectives: list[MissionObjective] = Field(default_factory=list)
    tasks: list[TaskSpec] = Field(default_factory=list)
    initial_domain_lead: str
    required_specialists: list[str] = Field(default_factory=list)
    optional_specialists: list[str] = Field(default_factory=list)
    expected_artifact_types: list[str] = Field(default_factory=list)
    budgets: MissionBudget = Field(default_factory=MissionBudget)
    plan_version: int = 1


class LeadershipTransferRequest(BaseModel):
    mission_id: str
    from_agent: str
    requested_target: str
    reason: str
    evidence_refs: list[str] = Field(default_factory=list)
    unresolved_question: str
    requested_output: str | None = None
    current_frontier: str | None = None
    proposed_frontier: str | None = None


class ManagedClaim(BaseModel):
    claim_id: str
    mission_id: str
    claim_type: str
    statement: str
    state: ClaimState = "PROPOSED"
    support_refs: list[str] = Field(default_factory=list)
    origin_agent: str | None = None
    causal_strength: str | None = None
    synthetic: bool = False
    superseded_by: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CompletionDecision(BaseModel):
    complete: bool
    status: MissionStatus
    objective_coverage: float = 0.0
    blocking_tasks: list[str] = Field(default_factory=list)
    blocking_subquestions: list[str] = Field(default_factory=list)
    blocking_gaps: list[str] = Field(default_factory=list)
    unresolved_conflicts: list[str] = Field(default_factory=list)
    validated_claims: list[str] = Field(default_factory=list)
    challenged_claims: list[str] = Field(default_factory=list)
    rejected_claims: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    satisfied_objectives: list[str] = Field(default_factory=list)
    unresolved_objectives: list[str] = Field(default_factory=list)


class AgentExecutionResult(BaseModel):
    agent_id: str
    task_id: str
    status: Literal["success", "retryable_failure", "blocking_failure", "nonblocking_failure"]
    artifact_refs: list[str] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentContext(BaseModel):
    mission_id: str
    task_id: str
    question: str
    mission_lead: str | None = None
    active_specialist: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    anomaly_refs: list[str] = Field(default_factory=list)
    input_refs: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
