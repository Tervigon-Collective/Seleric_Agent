"""Persistence ports for durable conversation state."""

from __future__ import annotations

import builtins
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from seleric_swarm.conversations.contracts import (
    ActivityEvent,
    ApprovalDecisionEvent,
    ApprovalRequest,
    Artifact,
    Attachment,
    MemoryItem,
    MemoryPreference,
    Message,
    RollbackRecord,
    Run,
    RunAttempt,
    RunAttemptStatus,
    RunStatus,
    SearchResult,
    Thread,
    ThreadSummary,
)


class ThreadRepository(Protocol):
    def create(self, thread: Thread) -> Thread: ...
    def get(
        self,
        thread_id: str,
        workspace_id: str | None = None,
        owner_user_id: str | None = None,
    ) -> Thread | None: ...
    def list_for_owner(
        self,
        workspace_id: str,
        user_id: str,
        *,
        limit: int = 50,
        status: str | None = None,
    ) -> list[Thread]: ...
    def list_page(
        self,
        workspace_id: str,
        user_id: str,
        *,
        limit: int = 50,
        cursor: str | None = None,
        include_deleted: bool = False,
    ) -> tuple[list[Thread], str | None]: ...
    def update(self, thread: Thread) -> Thread: ...


class MessageRepository(Protocol):
    def create(self, message: Message) -> Message: ...
    def get(
        self, message_id: str, workspace_id: str | None = None
    ) -> Message | None: ...
    def list_for_thread(self, thread_id: str, *, limit: int = 100) -> list[Message]: ...
    def list_page(
        self, thread_id: str, *, limit: int = 100, cursor: str | None = None
    ) -> tuple[list[Message], str | None]: ...
    def update(self, message: Message) -> Message: ...


class RunRepository(Protocol):
    def create(self, run: Run) -> Run: ...
    def get(
        self,
        run_id: str,
        workspace_id: str | None = None,
        owner_user_id: str | None = None,
    ) -> Run | None: ...
    def update(self, run: Run) -> Run: ...
    def compare_and_set_status(
        self,
        run_id: str,
        expected_statuses: set[RunStatus],
        new_status: RunStatus,
        *,
        now: datetime | None = None,
    ) -> Run | None: ...
    def list_for_owner(
        self, workspace_id: str, user_id: str, *, limit: int = 100
    ) -> list[Run]: ...
    def add_attempt(self, attempt: RunAttempt) -> RunAttempt: ...
    def update_attempt(self, attempt: RunAttempt) -> RunAttempt: ...
    def claim(
        self, run_id: str, worker_id: str, lease_seconds: float, *, now: datetime | None = None
    ) -> RunAttempt | None: ...
    def heartbeat(
        self,
        attempt_id: str,
        worker_id: str,
        lease_seconds: float,
        *,
        expected_version: int | None = None,
        now: datetime | None = None,
    ) -> bool: ...
    def compare_and_set_attempt(
        self,
        attempt_id: str,
        expected_status: RunAttemptStatus,
        new_status: RunAttemptStatus,
        *,
        worker_id: str | None = None,
        expected_version: int | None = None,
        lease_expired_before: datetime | None = None,
        now: datetime | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> RunAttempt | None: ...
    def finalize_attempt(
        self,
        attempt_id: str,
        *,
        worker_id: str,
        expected_version: int,
        requested_status: RunStatus,
        terminal_event: ActivityEvent,
        now: datetime | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> tuple[Run, RunAttempt, ActivityEvent] | None: ...
    def transition_failed_attempt(
        self,
        attempt_id: str,
        *,
        expected_version: int,
        retry_delay_seconds: float,
        now: datetime,
        error_code: str,
        error_message: str,
        worker_id: str | None = None,
        lease_expired_before: datetime | None = None,
    ) -> str: ...
    def cancel(
        self,
        run_id: str,
        *,
        now: datetime | None = None,
        terminal_event: ActivityEvent | None = None,
    ) -> ActivityEvent | None: ...
    def list_recoverable(
        self, *, now: datetime | None = None, limit: int = 100
    ) -> list[RunAttempt]: ...
    def append_event(self, event: ActivityEvent) -> ActivityEvent: ...
    def list_events(self, run_id: str, *, after_sequence: int = 0) -> list[ActivityEvent]: ...
    def list_thread_events(
        self, thread_id: str, *, after_sequence: int = 0
    ) -> list[ActivityEvent]: ...
    def add_outbox(self, run_id: str) -> None: ...
    def list_pending_outbox(self, *, limit: int = 100) -> list[str]: ...
    def mark_outbox_published(self, run_id: str) -> None: ...


class ArtifactRepository(Protocol):
    def put(self, artifact: Artifact) -> Artifact: ...
    def get(self, artifact_id: str, workspace_id: str | None = None) -> Artifact | None: ...
    def list_for_mission(self, mission_id: str) -> list[Artifact]: ...
    def list_for_context(self, workspace_id: str, thread_id: str) -> list[Artifact]: ...


class AttachmentRepository(Protocol):
    def create(self, attachment: Attachment) -> Attachment: ...
    def get(
        self,
        attachment_id: str,
        workspace_id: str | None = None,
        owner_user_id: str | None = None,
    ) -> Attachment | None: ...
    def update(self, attachment: Attachment) -> Attachment: ...
    def associate_many(
        self,
        attachment_ids: list[str],
        message_id: str,
        *,
        thread_id: str,
        workspace_id: str,
        owner_user_id: str,
    ) -> bool: ...
    def delete(self, attachment_id: str) -> bool: ...
    def list_for_thread(self, thread_id: str) -> list[Attachment]: ...


class MemoryRepository(Protocol):
    def create(self, memory: MemoryItem) -> MemoryItem: ...
    def get(
        self, memory_id: str, workspace_id: str, owner_user_id: str
    ) -> MemoryItem | None: ...
    def list(
        self,
        workspace_id: str,
        owner_user_id: str,
        *,
        project_id: str | None = None,
        thread_id: str | None = None,
        include_inactive: bool = False,
        limit: int = 100,
    ) -> list[MemoryItem]: ...
    def update(self, memory: MemoryItem) -> MemoryItem: ...
    def delete(self, memory_id: str, workspace_id: str, owner_user_id: str) -> bool: ...
    def record_usage(
        self, run_id: str, memories: builtins.list[MemoryItem], provenance: dict[str, object]
    ) -> None: ...
    def list_used_by_run(
        self, run_id: str, workspace_id: str, owner_user_id: str
    ) -> builtins.list[MemoryItem]: ...
    def get_preference(self, workspace_id: str, owner_user_id: str) -> MemoryPreference: ...
    def set_preference(self, preference: MemoryPreference) -> MemoryPreference: ...


class ThreadSummaryRepository(Protocol):
    def create(self, summary: ThreadSummary) -> ThreadSummary: ...
    def latest(
        self, thread_id: str, workspace_id: str, owner_user_id: str
    ) -> ThreadSummary | None: ...


class SearchRepository(Protocol):
    def search(
        self,
        query: str,
        workspace_id: str,
        owner_user_id: str,
        *,
        kinds: set[str] | None = None,
        limit: int = 20,
        vector: list[float] | None = None,
    ) -> list[SearchResult]: ...


class ApprovalRepository(Protocol):
    def create(self, approval: ApprovalRequest) -> ApprovalRequest: ...
    def get(
        self, approval_id: str, workspace_id: str, owner_user_id: str
    ) -> ApprovalRequest | None: ...
    def get_for_workspace(
        self, approval_id: str, workspace_id: str
    ) -> ApprovalRequest | None: ...
    def get_by_idempotency(
        self, workspace_id: str, owner_user_id: str, idempotency_key: str
    ) -> ApprovalRequest | None: ...
    def transition(
        self,
        approval_id: str,
        expected_status: str,
        approval: ApprovalRequest,
        event: ApprovalDecisionEvent,
    ) -> ApprovalRequest | None: ...
    def list_events(self, approval_id: str) -> list[ApprovalDecisionEvent]: ...
    def add_rollback(self, record: RollbackRecord) -> RollbackRecord: ...
    def get_rollback(self, approval_id: str) -> RollbackRecord | None: ...
    def list_due(self, now: datetime) -> list[ApprovalRequest]: ...


class UnitOfWorkFactory(Protocol):
    def __call__(self) -> AbstractContextManager[ConversationRepositories]: ...


@dataclass(frozen=True)
class ConversationRepositories:
    threads: ThreadRepository
    messages: MessageRepository
    runs: RunRepository
    artifacts: ArtifactRepository
    attachments: AttachmentRepository
    memories: MemoryRepository
    thread_summaries: ThreadSummaryRepository
    search: SearchRepository
    approvals: ApprovalRepository
    unit_of_work: UnitOfWorkFactory | None = None

    def transaction(self) -> AbstractContextManager[ConversationRepositories]:
        if self.unit_of_work is None:
            raise RuntimeError("unit of work is not configured")
        return self.unit_of_work()
