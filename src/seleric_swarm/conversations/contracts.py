"""Canonical contracts for the Seleric conversation platform."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ContractModel(BaseModel):
    """Strict, assignment-safe base for persisted conversation records."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class PrincipalAuthMethod(StrEnum):
    ANONYMOUS = "ANONYMOUS"
    SHARED_API_KEY = "SHARED_API_KEY"
    SERVICE = "SERVICE"


class Principal(ContractModel):
    principal_id: str
    workspace_id: str
    user_id: str
    authenticated: bool = False
    auth_method: PrincipalAuthMethod = PrincipalAuthMethod.ANONYMOUS
    roles: set[str] = Field(default_factory=set)

    @field_validator("principal_id", "workspace_id", "user_id")
    @classmethod
    def identifiers_are_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("identifier must not be blank")
        return value

    @property
    def is_admin(self) -> bool:
        return self.authenticated and bool(
            {"admin", "internal"} & {role.lower() for role in self.roles}
        )

    @property
    def is_internal(self) -> bool:
        return self.authenticated and "internal" in {role.lower() for role in self.roles}

    def can_access_workspace(self, workspace_id: str) -> bool:
        return self.workspace_id == workspace_id or self.is_internal

    def owns(self, *, workspace_id: str, user_id: str) -> bool:
        return self.can_access_workspace(workspace_id) and (
            self.user_id == user_id or self.is_admin
        )


class ThreadStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"
    DELETED = "DELETED"


class MessageRole(StrEnum):
    USER = "USER"
    ASSISTANT = "ASSISTANT"
    SYSTEM = "SYSTEM"
    TOOL = "TOOL"


class MessagePartType(StrEnum):
    TEXT = "TEXT"
    TABLE = "TABLE"
    CODE = "CODE"
    SOURCE = "SOURCE"
    TOOL_CALL = "TOOL_CALL"
    ARTIFACT = "ARTIFACT"
    CHART = "CHART"
    AGENT_STATUS = "AGENT_STATUS"
    APPROVAL = "APPROVAL"
    WARNING = "WARNING"


class RunStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class RunAttemptStatus(StrEnum):
    RUNNING = "RUNNING"
    RETRYABLE = "RETRYABLE"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class EventVisibility(StrEnum):
    USER = "USER"
    ADMIN = "ADMIN"
    INTERNAL = "INTERNAL"


class AttachmentStatus(StrEnum):
    PENDING = "PENDING"
    READY = "READY"
    FAILED = "FAILED"
    DELETED = "DELETED"


class AttachmentScanStatus(StrEnum):
    PENDING = "PENDING"
    CLEAN = "CLEAN"
    QUARANTINED = "QUARANTINED"
    FAILED = "FAILED"
    UNAVAILABLE = "UNAVAILABLE"


class MemoryScope(StrEnum):
    THREAD = "THREAD"
    USER = "USER"
    PROJECT = "PROJECT"
    EPISODIC = "EPISODIC"


class MemoryType(StrEnum):
    FACT = "FACT"
    DECISION = "DECISION"
    PREFERENCE = "PREFERENCE"
    OUTCOME = "OUTCOME"
    CONSTRAINT = "CONSTRAINT"
    DEFINITION = "DEFINITION"
    SUMMARY = "SUMMARY"
    INSTRUCTION = "INSTRUCTION"


class MemoryStatus(StrEnum):
    CANDIDATE = "CANDIDATE"
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    ARCHIVED = "ARCHIVED"
    DELETED = "DELETED"
    PENDING_CONSENT = "PENDING_CONSENT"


class ApprovalStatus(StrEnum):
    REQUESTED = "REQUESTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    EXECUTED = "EXECUTED"
    ROLLED_BACK = "ROLLED_BACK"


class Thread(ContractModel):
    id: str = Field(default_factory=lambda: _id("thread"))
    workspace_id: str
    owner_user_id: str
    project_id: str | None = None
    title: str | None = None
    status: ThreadStatus = ThreadStatus.ACTIVE
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    deleted_at: datetime | None = None

    def is_owned_by(self, principal: Principal) -> bool:
        return principal.owns(workspace_id=self.workspace_id, user_id=self.owner_user_id)


class ThreadParticipant(ContractModel):
    thread_id: str
    workspace_id: str
    user_id: str
    role: str = "member"
    joined_at: datetime = Field(default_factory=_utc_now)


class SourcePartContent(ContractModel):
    evidence_id: str = Field(min_length=1)
    title: str | None = None
    url: str | None = None
    excerpt: str | None = None


class ChartPartContent(ContractModel):
    chart_type: str = Field(min_length=1)
    artifact_id: str | None = None
    data: dict[str, Any] | list[Any] | None = None

    @model_validator(mode="after")
    def require_chart_source(self) -> ChartPartContent:
        if self.artifact_id is None and self.data is None:
            raise ValueError("CHART parts require artifact_id or data")
        return self


class ArtifactPartContent(ContractModel):
    artifact_id: str = Field(min_length=1)
    label: str | None = None
    artifact_type: str | None = None


class ToolCallPartContent(ContractModel):
    tool: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    call_id: str | None = None


class MessagePart(ContractModel):
    type: MessagePartType
    content: str | dict[str, Any] | list[Any]
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_content(self) -> MessagePart:
        if self.type in {
            MessagePartType.TEXT,
            MessagePartType.CODE,
            MessagePartType.AGENT_STATUS,
            MessagePartType.WARNING,
        } and not isinstance(self.content, str):
            raise ValueError(f"{self.type.value} parts require string content")
        if self.type is MessagePartType.TABLE and not isinstance(self.content, (dict, list)):
            raise ValueError("TABLE parts require object or array content")
        if self.type in {
            MessagePartType.SOURCE,
            MessagePartType.TOOL_CALL,
            MessagePartType.ARTIFACT,
            MessagePartType.CHART,
            MessagePartType.APPROVAL,
        } and not isinstance(self.content, dict):
            raise ValueError(f"{self.type.value} parts require object content")
        if self.type is MessagePartType.SOURCE:
            object.__setattr__(
                self,
                "content",
                SourcePartContent.model_validate(self.content).model_dump(exclude_none=True),
            )
        if self.type is MessagePartType.TOOL_CALL:
            object.__setattr__(
                self,
                "content",
                ToolCallPartContent.model_validate(self.content).model_dump(exclude_none=True),
            )
        if self.type is MessagePartType.ARTIFACT:
            object.__setattr__(
                self,
                "content",
                ArtifactPartContent.model_validate(self.content).model_dump(exclude_none=True),
            )
        if self.type is MessagePartType.CHART:
            object.__setattr__(
                self,
                "content",
                ChartPartContent.model_validate(self.content).model_dump(exclude_none=True),
            )
        if (
            self.type is MessagePartType.APPROVAL
            and isinstance(self.content, dict)
            and not self.content.get("approval_id")
        ):
            raise ValueError("APPROVAL parts require approval_id")
        return self


class Message(ContractModel):
    id: str = Field(default_factory=lambda: _id("message"))
    thread_id: str
    workspace_id: str
    user_id: str | None = None
    role: MessageRole
    parts: list[MessagePart] = Field(min_length=1)
    run_id: str | None = None
    parent_message_id: str | None = None
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)


class Run(ContractModel):
    id: str = Field(default_factory=lambda: _id("run"))
    thread_id: str
    workspace_id: str
    requested_by_user_id: str
    mission_id: str | None = None
    status: RunStatus = RunStatus.QUEUED
    current_attempt: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=3, ge=1)
    retry_count: int = Field(default=0, ge=0)
    next_retry_at: datetime | None = None
    cancel_requested_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None


class RunAttempt(ContractModel):
    id: str = Field(default_factory=lambda: _id("attempt"))
    run_id: str
    attempt_number: int = Field(ge=1)
    status: RunAttemptStatus = RunAttemptStatus.RUNNING
    worker_id: str | None = None
    lease_expires_at: datetime | None = None
    heartbeat_at: datetime | None = None
    retryable: bool = True
    version: int = Field(default=0, ge=0)
    error_code: str | None = None
    error_message: str | None = None
    started_at: datetime = Field(default_factory=_utc_now)
    completed_at: datetime | None = None


class ActivityEvent(ContractModel):
    id: str = Field(default_factory=lambda: _id("event"))
    thread_id: str
    workspace_id: str
    run_id: str | None = None
    owner_user_id: str | None = None
    actor_user_id: str | None = None
    sequence: int = Field(default=0, ge=0)
    thread_sequence: int = Field(default=0, ge=0)
    event_type: str
    actor_type: str | None = None
    actor_id: str | None = None
    title: str | None = None
    summary: str | None = None
    parent_event_id: str | None = None
    visibility: EventVisibility = EventVisibility.USER
    evidence_ids: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    created_at: datetime = Field(default_factory=_utc_now)


class ThreadPage(ContractModel):
    items: list[Thread]
    next_cursor: str | None = None
    has_more: bool = False


class MessagePage(ContractModel):
    items: list[Message]
    next_cursor: str | None = None
    has_more: bool = False


class ArtifactProvenance(ContractModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True, protected_namespaces=())

    evidence_ids: list[str] = Field(default_factory=list)
    calculation_version: str | None = None
    query_version: str | None = None
    prompt_version: str | None = None
    tool_version: str | None = None
    model_version: str | None = None
    source_metadata: dict[str, Any] = Field(default_factory=dict)


class Artifact(ContractModel):
    """Durable envelope preserving a complete typed artifact payload."""

    id: str = Field(default_factory=lambda: _id("artifact"))
    workspace_id: str
    artifact_type: str
    payload: dict[str, Any]
    classification: Literal["ui", "factual", "derived"]
    evidence_ids: list[str] = Field(default_factory=list)
    provenance: ArtifactProvenance = Field(default_factory=ArtifactProvenance)
    mission_id: str | None = None
    thread_id: str | None = None
    run_id: str | None = None
    message_id: str | None = None
    created_at: datetime = Field(default_factory=_utc_now)

    @model_validator(mode="after")
    def validate_classification_boundary(self) -> Artifact:
        evidence_ids = list(dict.fromkeys([*self.evidence_ids, *self.provenance.evidence_ids]))
        if self.classification == "ui":
            if evidence_ids or any(
                (
                    self.provenance.calculation_version,
                    self.provenance.query_version,
                    self.provenance.prompt_version,
                    self.provenance.tool_version,
                    self.provenance.model_version,
                    self.provenance.source_metadata,
                )
            ):
                raise ValueError("ui artifacts cannot carry evidence or derived provenance")
            return self
        if not evidence_ids:
            raise ValueError("factual/derived artifacts require evidence IDs")
        versions = (
            self.provenance.calculation_version,
            self.provenance.query_version,
            self.provenance.prompt_version,
            self.provenance.tool_version,
            self.provenance.model_version,
        )
        if self.classification == "derived" and not any(versions):
            raise ValueError(
                "derived artifacts require a calculation/query/prompt/tool/model version"
            )
        return self

    def require_provenance(self) -> Artifact:
        """Validate durable factual/derived output without constraining UI envelopes."""
        if self.classification == "ui":
            return self
        evidence_ids = list(dict.fromkeys([*self.evidence_ids, *self.provenance.evidence_ids]))
        if not evidence_ids:
            raise ValueError("factual/derived artifacts require evidence IDs")
        versions = (
            self.provenance.calculation_version,
            self.provenance.query_version,
            self.provenance.prompt_version,
            self.provenance.tool_version,
            self.provenance.model_version,
        )
        if self.classification == "derived" and not any(versions):
            raise ValueError(
                "derived artifacts require a calculation/query/prompt/tool/model version"
            )
        return self.model_copy(
            update={
                "evidence_ids": evidence_ids,
                "provenance": self.provenance.model_copy(update={"evidence_ids": evidence_ids}),
            }
        )


class Attachment(ContractModel):
    id: str = Field(default_factory=lambda: _id("attachment"))
    thread_id: str
    workspace_id: str
    owner_user_id: str
    message_id: str | None = None
    filename: str
    content_type: str
    size_bytes: int = Field(ge=0)
    storage_uri: str | None = None
    checksum_sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    status: AttachmentStatus = AttachmentStatus.PENDING
    scan_status: AttachmentScanStatus = AttachmentScanStatus.PENDING
    scan_detail: str = ""
    created_at: datetime = Field(default_factory=_utc_now)

    @model_validator(mode="after")
    def ready_requires_verified_clean_scan(self) -> Attachment:
        if self.status is AttachmentStatus.READY and (
            self.scan_status is not AttachmentScanStatus.CLEAN
            or not self.storage_uri
            or not self.checksum_sha256
        ):
            raise ValueError("READY attachments require storage, checksum, and a CLEAN scan")
        return self


class ThreadSummary(ContractModel):
    id: str = Field(default_factory=lambda: _id("summary"))
    thread_id: str
    workspace_id: str
    owner_user_id: str
    through_message_id: str | None = None
    covered_message_ids: list[str] = Field(default_factory=list)
    source_evidence_ids: list[str] = Field(default_factory=list)
    mode: Literal["extractive", "deterministic"] = "extractive"
    version: str = "phase6-v1"
    summary: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)


class TurnRecord(ContractModel):
    """Machine-readable record of one completed agent turn.

    Stored as a 'ui' Artifact with artifact_type='turn_record', this is
    the primary grounding source for follow-up resolution — not raw message text.

    Only fields with actual values are written; None/empty fields are omitted
    from prompt injection so the context block stays compact.

    Content policy — what MUST appear here:
      - metric_labels: human-readable metric names ("Net Sales", "MER")
      - entities: named brands, channels, segments, SKUs mentioned/returned
      - top_items: top-N ranked item names from the answer
      - period / grain: the time window and bucket size answered
    Content policy — what MUST NEVER appear here:
      - Internal metric IDs (e.g. "metric.shopify_net_sales_v2")
      - Raw artifact or mission IDs
      - Numbers without associated labels
      - Full Markdown prose or table text
    """

    # What was asked
    query: str
    intent: str | None = None
    period: str | None = None           # e.g. "last_30d", "today", "this_month"
    grain: str | None = None            # e.g. "day", "week", "month"

    # What was answered — machine-readable facts the next turn can cite
    metric_labels: list[str] = Field(default_factory=list)   # human-readable labels only
    entities: list[str] = Field(default_factory=list)         # brands, channels, segments
    top_items: list[str] = Field(default_factory=list)        # top-N SKUs/products/campaigns

    # Provenance
    evidence_ids: list[str] = Field(default_factory=list)
    mission_id: str | None = None
    as_of: str | None = None            # ISO date string e.g. "2026-09-26"


class MemoryItem(ContractModel):
    id: str = Field(default_factory=lambda: _id("memory"))
    workspace_id: str
    owner_user_id: str
    project_id: str | None = None
    scope: MemoryScope
    type: MemoryType
    status: MemoryStatus = MemoryStatus.PENDING_CONSENT
    content: str | dict[str, Any]
    normalized_content: str = ""
    structured_data: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    salience: float = Field(default=0.5, ge=0.0, le=1.0)
    thread_id: str | None = None
    source_message_id: str | None = None
    source_message_ids: list[str] = Field(default_factory=list)
    source_run_id: str | None = None
    source_evidence_ids: list[str] = Field(default_factory=list)
    supersedes_id: str | None = None
    superseded_by_id: str | None = None
    pinned: bool = False
    requires_confirmation: bool = True
    consented_at: datetime | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    last_used_at: datetime | None = None
    expires_at: datetime | None = None
    archived_at: datetime | None = None
    deleted_at: datetime | None = None

    @model_validator(mode="after")
    def validate_scope_and_consent(self) -> MemoryItem:
        if self.scope is MemoryScope.THREAD and not self.thread_id:
            raise ValueError("thread_id is required for THREAD-scoped memory")
        if self.scope in {MemoryScope.PROJECT, MemoryScope.EPISODIC} and not self.project_id:
            raise ValueError("project_id is required for PROJECT/EPISODIC memory")
        if self.status is MemoryStatus.ACTIVE and self.consented_at is None:
            raise ValueError("ACTIVE memory requires consented_at")
        if self.valid_from and self.valid_to and self.valid_to <= self.valid_from:
            raise ValueError("valid_to must be after valid_from")
        return self


class MemoryPreference(ContractModel):
    workspace_id: str
    owner_user_id: str
    opted_out: bool = False
    updated_at: datetime = Field(default_factory=_utc_now)


class ContextBundle(ContractModel):
    permissions: dict[str, bool] = Field(default_factory=dict)
    workspace_config: dict[str, Any] = Field(default_factory=dict)
    recent_messages: list[dict[str, Any]] = Field(default_factory=list)
    latest_summary: ThreadSummary | None = None
    memories: list[MemoryItem] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    memory_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    provenance: dict[str, dict[str, Any]] = Field(default_factory=dict)
    character_count: int = Field(default=0, ge=0)
    token_estimate: int = Field(default=0, ge=0)
    token_budget: int = Field(default=0, ge=0)


class SearchResult(ContractModel):
    id: str
    kind: Literal["thread", "message", "memory", "artifact", "report", "run"]
    title: str
    snippet: str = ""
    thread_id: str | None = None
    run_id: str | None = None
    lexical_rank: int | None = None
    vector_rank: int | None = None
    recency_rank: int | None = None
    score: float = 0.0
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class ApprovalRequest(ContractModel):
    id: str = Field(default_factory=lambda: _id("approval"))
    workspace_id: str
    owner_user_id: str
    run_id: str | None = None
    action_type: str
    action_preview: dict[str, Any]
    required_role: str
    status: ApprovalStatus = ApprovalStatus.REQUESTED
    idempotency_key: str
    dry_run: bool = True
    checkpoint_resume_token: str | None = None
    expires_at: datetime | None = None
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)


class ApprovalDecisionEvent(ContractModel):
    id: str = Field(default_factory=lambda: _id("approval_event"))
    approval_id: str
    from_status: ApprovalStatus | None = None
    to_status: ApprovalStatus
    actor_principal_id: str
    reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)


class RollbackRecord(ContractModel):
    id: str = Field(default_factory=lambda: _id("rollback"))
    approval_id: str
    actor_principal_id: str
    action: dict[str, Any] = Field(default_factory=dict)
    outcome: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)
