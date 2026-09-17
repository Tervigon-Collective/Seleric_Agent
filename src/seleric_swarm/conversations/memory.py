"""Thread-safe in-memory conversation repositories."""

from __future__ import annotations

import base64
import builtins
import json
from datetime import UTC, datetime, timedelta
from threading import RLock

from seleric_swarm.conversations.contracts import (
    ActivityEvent,
    Artifact,
    Attachment,
    AttachmentScanStatus,
    AttachmentStatus,
    MemoryItem,
    MemoryPreference,
    MemoryStatus,
    Message,
    Run,
    RunAttempt,
    RunAttemptStatus,
    RunStatus,
    Thread,
    ThreadSummary,
)
from seleric_swarm.conversations.phase7 import (
    InMemoryApprovalRepository,
    InMemorySearchRepository,
    QueryEmbeddingHook,
)
from seleric_swarm.conversations.repositories import ConversationRepositories


def _cursor(moment: datetime, item_id: str) -> str:
    raw = json.dumps([moment.isoformat(), item_id], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(value: str | None) -> tuple[datetime, str] | None:
    if not value:
        return None
    try:
        padded = value + "=" * (-len(value) % 4)
        moment, item_id = json.loads(base64.urlsafe_b64decode(padded).decode())
        return datetime.fromisoformat(moment), str(item_id)
    except (ValueError, TypeError, json.JSONDecodeError):
        raise ValueError("invalid cursor") from None


class InMemoryThreadRepository:
    def __init__(self) -> None:
        self._items: dict[str, Thread] = {}
        self._lock = RLock()

    def create(self, thread: Thread) -> Thread:
        with self._lock:
            self._items.setdefault(thread.id, thread)
            return self._items[thread.id]

    def get(self, thread_id: str) -> Thread | None:
        with self._lock:
            return self._items.get(thread_id)

    def list_for_owner(
        self, workspace_id: str, user_id: str, *, limit: int = 50
    ) -> list[Thread]:
        with self._lock:
            items = [
                item
                for item in self._items.values()
                if item.workspace_id == workspace_id and item.owner_user_id == user_id
            ]
        return sorted(items, key=lambda item: item.updated_at, reverse=True)[: max(1, limit)]

    def list_page(
        self,
        workspace_id: str,
        user_id: str,
        *,
        limit: int = 50,
        cursor: str | None = None,
        include_deleted: bool = False,
    ) -> tuple[list[Thread], str | None]:
        boundary = _decode_cursor(cursor)
        with self._lock:
            items = [
                item
                for item in self._items.values()
                if item.workspace_id == workspace_id
                and item.owner_user_id == user_id
                and (include_deleted or item.status.value != "DELETED")
                and (
                    boundary is None
                    or (item.updated_at, item.id) < boundary
                )
            ]
        ordered = sorted(items, key=lambda item: (item.updated_at, item.id), reverse=True)
        page = ordered[: max(1, limit) + 1]
        has_more = len(page) > max(1, limit)
        page = page[: max(1, limit)]
        next_cursor = _cursor(page[-1].updated_at, page[-1].id) if has_more and page else None
        return page, next_cursor

    def update(self, thread: Thread) -> Thread:
        with self._lock:
            if thread.id not in self._items:
                raise KeyError(thread.id)
            self._items[thread.id] = thread
            return thread


class InMemoryMessageRepository:
    def __init__(self) -> None:
        self._items: dict[str, Message] = {}
        self._lock = RLock()

    def create(self, message: Message) -> Message:
        with self._lock:
            self._items.setdefault(message.id, message)
            return self._items[message.id]

    def get(self, message_id: str) -> Message | None:
        with self._lock:
            return self._items.get(message_id)

    def list_for_thread(self, thread_id: str, *, limit: int = 100) -> list[Message]:
        with self._lock:
            items = [item for item in self._items.values() if item.thread_id == thread_id]
        newest = sorted(items, key=lambda item: (item.created_at, item.id), reverse=True)[
            : max(1, limit)
        ]
        return list(reversed(newest))

    def list_page(
        self, thread_id: str, *, limit: int = 100, cursor: str | None = None
    ) -> tuple[list[Message], str | None]:
        boundary = _decode_cursor(cursor)
        with self._lock:
            items = [
                item
                for item in self._items.values()
                if item.thread_id == thread_id
                and (boundary is None or (item.created_at, item.id) < boundary)
            ]
        ordered = sorted(items, key=lambda item: (item.created_at, item.id), reverse=True)
        page = ordered[: max(1, limit) + 1]
        has_more = len(page) > max(1, limit)
        page = page[: max(1, limit)]
        next_cursor = _cursor(page[-1].created_at, page[-1].id) if has_more and page else None
        return page, next_cursor

    def update(self, message: Message) -> Message:
        with self._lock:
            if message.id not in self._items:
                raise KeyError(message.id)
            self._items[message.id] = message
            return message


class InMemoryRunRepository:
    def __init__(self) -> None:
        self._items: dict[str, Run] = {}
        self._attempts: dict[str, RunAttempt] = {}
        self._events: dict[str, list[ActivityEvent]] = {}
        self._lock = RLock()

    def create(self, run: Run) -> Run:
        with self._lock:
            self._items.setdefault(run.id, run)
            return self._items[run.id]

    def get(self, run_id: str) -> Run | None:
        with self._lock:
            return self._items.get(run_id)

    def update(self, run: Run) -> Run:
        with self._lock:
            if run.id not in self._items:
                raise KeyError(run.id)
            self._items[run.id] = run
            return run

    def compare_and_set_status(
        self,
        run_id: str,
        expected_statuses: set[RunStatus],
        new_status: RunStatus,
        *,
        now: datetime | None = None,
    ) -> Run | None:
        moment = now or datetime.now(UTC)
        with self._lock:
            run = self._items.get(run_id)
            if run is None or run.status not in expected_statuses:
                return None
            terminal = new_status in {
                RunStatus.COMPLETED,
                RunStatus.FAILED,
                RunStatus.CANCELLED,
            }
            updated = run.model_copy(
                update={
                    "status": new_status,
                    "started_at": (
                        run.started_at or moment
                        if new_status is RunStatus.RUNNING
                        else run.started_at
                    ),
                    "completed_at": moment if terminal else None,
                    "next_retry_at": None if terminal else run.next_retry_at,
                }
            )
            self._items[run_id] = updated
            return updated

    def list_for_owner(
        self, workspace_id: str, user_id: str, *, limit: int = 100
    ) -> list[Run]:
        with self._lock:
            items = [
                item for item in self._items.values()
                if item.workspace_id == workspace_id and item.requested_by_user_id == user_id
            ]
        return sorted(items, key=lambda item: item.created_at, reverse=True)[: max(1, limit)]

    def add_attempt(self, attempt: RunAttempt) -> RunAttempt:
        with self._lock:
            self._attempts.setdefault(attempt.id, attempt)
            return self._attempts[attempt.id]

    def update_attempt(self, attempt: RunAttempt) -> RunAttempt:
        with self._lock:
            if attempt.id not in self._attempts:
                raise KeyError(attempt.id)
            self._attempts[attempt.id] = attempt
            return attempt

    def claim(
        self, run_id: str, worker_id: str, lease_seconds: float, *, now: datetime | None = None
    ) -> RunAttempt | None:
        moment = now or datetime.now(UTC)
        with self._lock:
            attempts = sorted(
                (item for item in self._attempts.values() if item.run_id == run_id),
                key=lambda item: item.attempt_number,
                reverse=True,
            )
            if not attempts:
                return None
            attempt = attempts[0]
            claimable = attempt.status is RunAttemptStatus.RETRYABLE or (
                attempt.status is RunAttemptStatus.RUNNING
                and (attempt.worker_id is None or not attempt.lease_expires_at or attempt.lease_expires_at <= moment)
            )
            if not claimable:
                return None
            claimed = attempt.model_copy(
                update={
                    "status": RunAttemptStatus.RUNNING,
                    "worker_id": worker_id,
                    "heartbeat_at": moment,
                    "lease_expires_at": moment + timedelta(seconds=lease_seconds),
                    "version": attempt.version + 1,
                }
            )
            self._attempts[attempt.id] = claimed
            return claimed

    def heartbeat(
        self,
        attempt_id: str,
        worker_id: str,
        lease_seconds: float,
        *,
        expected_version: int | None = None,
        now: datetime | None = None,
    ) -> bool:
        moment = now or datetime.now(UTC)
        with self._lock:
            attempt = self._attempts.get(attempt_id)
            if (
                attempt is None
                or attempt.status is not RunAttemptStatus.RUNNING
                or attempt.worker_id != worker_id
                or (expected_version is not None and attempt.version != expected_version)
                or (attempt.lease_expires_at is not None and attempt.lease_expires_at <= moment)
            ):
                return False
            self._attempts[attempt_id] = attempt.model_copy(
                update={
                    "heartbeat_at": moment,
                    "lease_expires_at": moment + timedelta(seconds=lease_seconds),
                }
            )
            return True

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
    ) -> RunAttempt | None:
        moment = now or datetime.now(UTC)
        with self._lock:
            attempt = self._attempts.get(attempt_id)
            if attempt is None or attempt.status is not expected_status:
                return None
            if worker_id is not None and attempt.worker_id != worker_id:
                return None
            if expected_version is not None and attempt.version != expected_version:
                return None
            if (
                lease_expired_before is not None
                and (
                    attempt.lease_expires_at is None
                    or attempt.lease_expires_at > lease_expired_before
                )
            ):
                return None
            terminal = new_status in {
                RunAttemptStatus.COMPLETED,
                RunAttemptStatus.FAILED,
                RunAttemptStatus.CANCELLED,
            }
            updated = attempt.model_copy(
                update={
                    "status": new_status,
                    "error_code": error_code,
                    "error_message": error_message,
                    "completed_at": moment if terminal else None,
                    "lease_expires_at": None,
                    "version": attempt.version + 1,
                }
            )
            self._attempts[attempt_id] = updated
            return updated

    def cancel(self, run_id: str, *, now: datetime | None = None) -> bool:
        moment = now or datetime.now(UTC)
        with self._lock:
            run = self._items.get(run_id)
            if run is None or run.status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}:
                return False
            self._items[run_id] = run.model_copy(
                update={
                    "status": RunStatus.CANCELLED,
                    "cancel_requested_at": moment,
                    "completed_at": moment,
                }
            )
            for attempt_id, attempt in self._attempts.items():
                if attempt.run_id == run_id and attempt.status in {
                    RunAttemptStatus.RUNNING,
                    RunAttemptStatus.RETRYABLE,
                }:
                    self._attempts[attempt_id] = attempt.model_copy(
                        update={
                            "status": RunAttemptStatus.CANCELLED,
                            "completed_at": moment,
                            "lease_expires_at": None,
                            "version": attempt.version + 1,
                        }
                    )
            return True

    def list_recoverable(
        self, *, now: datetime | None = None, limit: int = 100
    ) -> list[RunAttempt]:
        moment = now or datetime.now(UTC)
        with self._lock:
            items = [
                attempt
                for attempt in self._attempts.values()
                if attempt.status is RunAttemptStatus.RETRYABLE
                or (
                    attempt.status is RunAttemptStatus.RUNNING
                    and attempt.lease_expires_at is not None
                    and attempt.lease_expires_at <= moment
                )
            ]
        return sorted(items, key=lambda item: item.started_at)[: max(1, limit)]

    def append_event(self, event: ActivityEvent) -> ActivityEvent:
        with self._lock:
            events = self._events.setdefault(event.run_id or "", [])
            if any(existing.id == event.id for existing in events):
                return next(existing for existing in events if existing.id == event.id)
            last_sequence = events[-1].sequence if events else 0
            if event.sequence == 0:
                event = event.model_copy(update={"sequence": last_sequence + 1})
            elif event.sequence <= last_sequence:
                raise ValueError("event sequence must be strictly monotonic")
            thread_sequence = max(
                (
                    existing.thread_sequence
                    for run_events in self._events.values()
                    for existing in run_events
                    if existing.thread_id == event.thread_id
                ),
                default=0,
            )
            event = event.model_copy(update={"thread_sequence": thread_sequence + 1})
            events.append(event)
            return event

    def list_events(self, run_id: str, *, after_sequence: int = 0) -> list[ActivityEvent]:
        with self._lock:
            return [
                event
                for event in self._events.get(run_id, [])
                if event.sequence > after_sequence
            ]

    def list_thread_events(
        self, thread_id: str, *, after_sequence: int = 0
    ) -> list[ActivityEvent]:
        with self._lock:
            events = [
                event
                for run_events in self._events.values()
                for event in run_events
                if event.thread_id == thread_id and event.thread_sequence > after_sequence
            ]
        return sorted(events, key=lambda event: event.thread_sequence)


class InMemoryArtifactRepository:
    def __init__(self) -> None:
        self._items: dict[str, Artifact] = {}
        self._lock = RLock()

    def put(self, artifact: Artifact) -> Artifact:
        artifact = artifact.require_provenance()
        with self._lock:
            self._items[artifact.id] = artifact
            return artifact

    def get(self, artifact_id: str) -> Artifact | None:
        with self._lock:
            return self._items.get(artifact_id)

    def list_for_mission(self, mission_id: str) -> list[Artifact]:
        with self._lock:
            return [item for item in self._items.values() if item.mission_id == mission_id]

    def list_for_context(self, workspace_id: str, thread_id: str) -> list[Artifact]:
        with self._lock:
            return sorted(
                (
                    item
                    for item in self._items.values()
                    if item.workspace_id == workspace_id and item.thread_id == thread_id
                ),
                key=lambda item: item.created_at,
                reverse=True,
            )


class InMemoryAttachmentRepository:
    def __init__(self) -> None:
        self._items: dict[str, Attachment] = {}
        self._lock = RLock()

    def create(self, attachment: Attachment) -> Attachment:
        attachment = Attachment.model_validate(attachment.model_dump())
        with self._lock:
            self._items.setdefault(attachment.id, attachment)
            return self._items[attachment.id]

    def get(self, attachment_id: str) -> Attachment | None:
        with self._lock:
            return self._items.get(attachment_id)

    def update(self, attachment: Attachment) -> Attachment:
        attachment = Attachment.model_validate(attachment.model_dump())
        with self._lock:
            if attachment.id not in self._items:
                raise KeyError(attachment.id)
            self._items[attachment.id] = attachment
            return attachment

    def associate_many(
        self,
        attachment_ids: list[str],
        message_id: str,
        *,
        thread_id: str,
        workspace_id: str,
        owner_user_id: str,
    ) -> bool:
        unique_ids = list(dict.fromkeys(attachment_ids))
        with self._lock:
            attachments = [self._items.get(item_id) for item_id in unique_ids]
            if any(
                item is None
                or item.thread_id != thread_id
                or item.workspace_id != workspace_id
                or item.owner_user_id != owner_user_id
                or item.status is not AttachmentStatus.READY
                or item.scan_status is not AttachmentScanStatus.CLEAN
                or item.message_id is not None
                for item in attachments
            ):
                return False
            for item in attachments:
                assert item is not None
                self._items[item.id] = item.model_copy(update={"message_id": message_id})
            return True

    def delete(self, attachment_id: str) -> bool:
        with self._lock:
            item = self._items.get(attachment_id)
            if item is None:
                return False
            self._items[attachment_id] = item.model_copy(update={"status": "DELETED"})
            return True

    def list_for_thread(self, thread_id: str) -> list[Attachment]:
        with self._lock:
            return [item for item in self._items.values() if item.thread_id == thread_id]


class InMemoryMemoryRepository:
    def __init__(self) -> None:
        self._items: dict[str, MemoryItem] = {}
        self._usage: dict[str, list[str]] = {}
        self._preferences: dict[tuple[str, str], MemoryPreference] = {}
        self._lock = RLock()

    def create(self, memory: MemoryItem) -> MemoryItem:
        with self._lock:
            self._items.setdefault(memory.id, memory)
            return self._items[memory.id]

    def get(
        self, memory_id: str, workspace_id: str, owner_user_id: str
    ) -> MemoryItem | None:
        with self._lock:
            item = self._items.get(memory_id)
            if (
                item is None
                or item.workspace_id != workspace_id
                or item.owner_user_id != owner_user_id
                or item.deleted_at is not None
                or item.status is MemoryStatus.DELETED
            ):
                return None
            return item

    def list(
        self,
        workspace_id: str,
        owner_user_id: str,
        *,
        project_id: str | None = None,
        thread_id: str | None = None,
        include_inactive: bool = False,
        limit: int = 100,
    ) -> list[MemoryItem]:
        now = datetime.now(UTC)
        with self._lock:
            items = [
                item
                for item in self._items.values()
                if item.workspace_id == workspace_id
                and item.owner_user_id == owner_user_id
                and item.deleted_at is None
                and (project_id is None or item.project_id in {None, project_id})
                and (thread_id is None or item.thread_id in {None, thread_id})
                and (
                    include_inactive
                    or (
                        item.status is MemoryStatus.ACTIVE
                        and (item.valid_from is None or item.valid_from <= now)
                        and (item.valid_to is None or item.valid_to > now)
                        and (item.expires_at is None or item.expires_at > now)
                    )
                )
            ]
        return sorted(items, key=lambda item: (item.pinned, item.updated_at), reverse=True)[
            : max(1, limit)
        ]

    def update(self, memory: MemoryItem) -> MemoryItem:
        with self._lock:
            existing = self._items.get(memory.id)
            if (
                existing is None
                or existing.workspace_id != memory.workspace_id
                or existing.owner_user_id != memory.owner_user_id
            ):
                raise KeyError(memory.id)
            self._items[memory.id] = memory
            return memory

    def delete(self, memory_id: str, workspace_id: str, owner_user_id: str) -> bool:
        item = self.get(memory_id, workspace_id, owner_user_id)
        if item is None:
            return False
        now = datetime.now(UTC)
        self.update(
            item.model_copy(
                update={"status": MemoryStatus.DELETED, "deleted_at": now, "updated_at": now}
            )
        )
        return True

    def record_usage(
        self, run_id: str, memories: builtins.list[MemoryItem], provenance: dict[str, object]
    ) -> None:
        del provenance
        now = datetime.now(UTC)
        with self._lock:
            ids = self._usage.setdefault(run_id, [])
            for memory in memories:
                if memory.id not in ids:
                    ids.append(memory.id)
                self._items[memory.id] = memory.model_copy(update={"last_used_at": now})

    def list_used_by_run(
        self, run_id: str, workspace_id: str, owner_user_id: str
    ) -> builtins.list[MemoryItem]:
        return [
            item
            for memory_id in self._usage.get(run_id, [])
            if (item := self.get(memory_id, workspace_id, owner_user_id)) is not None
        ]

    def get_preference(self, workspace_id: str, owner_user_id: str) -> MemoryPreference:
        with self._lock:
            return self._preferences.get(
                (workspace_id, owner_user_id),
                MemoryPreference(workspace_id=workspace_id, owner_user_id=owner_user_id),
            )

    def set_preference(self, preference: MemoryPreference) -> MemoryPreference:
        with self._lock:
            self._preferences[(preference.workspace_id, preference.owner_user_id)] = preference
            return preference


class InMemoryThreadSummaryRepository:
    def __init__(self) -> None:
        self._items: dict[str, ThreadSummary] = {}
        self._lock = RLock()

    def create(self, summary: ThreadSummary) -> ThreadSummary:
        with self._lock:
            self._items.setdefault(summary.id, summary)
            return self._items[summary.id]

    def latest(
        self, thread_id: str, workspace_id: str, owner_user_id: str
    ) -> ThreadSummary | None:
        with self._lock:
            matches = [
                item
                for item in self._items.values()
                if item.thread_id == thread_id
                and item.workspace_id == workspace_id
                and item.owner_user_id == owner_user_id
            ]
        return max(matches, key=lambda item: item.created_at) if matches else None


def build_in_memory_repositories(
    *, query_embedder: QueryEmbeddingHook | None = None
) -> ConversationRepositories:
    threads = InMemoryThreadRepository()
    messages = InMemoryMessageRepository()
    runs = InMemoryRunRepository()
    artifacts = InMemoryArtifactRepository()
    memories = InMemoryMemoryRepository()
    return ConversationRepositories(
        threads=threads,
        messages=messages,
        runs=runs,
        artifacts=artifacts,
        attachments=InMemoryAttachmentRepository(),
        memories=memories,
        thread_summaries=InMemoryThreadSummaryRepository(),
        search=InMemorySearchRepository(
            threads,
            messages,
            runs,
            artifacts,
            memories,
            query_embedder=query_embedder,
        ),
        approvals=InMemoryApprovalRepository(),
    )
