"""Postgres implementations of the conversation persistence ports."""

from __future__ import annotations

import base64
import builtins
import json
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

from sqlalchemy import create_engine, text

from seleric_swarm.conversations.contracts import (
    ActivityEvent,
    Artifact,
    Attachment,
    MemoryItem,
    MemoryPreference,
    Message,
    Run,
    RunAttempt,
    RunAttemptStatus,
    RunStatus,
    Thread,
    ThreadSummary,
)
from seleric_swarm.conversations.memory import build_in_memory_repositories
from seleric_swarm.conversations.phase7 import (
    PostgresApprovalRepository,
    PostgresSearchRepository,
    QueryEmbeddingHook,
)
from seleric_swarm.conversations.repositories import ConversationRepositories


def _json(value: Any) -> str:
    return json.dumps(value, default=str)


def _dict(row: Any, key: str) -> dict[str, Any]:
    value = row.get(key) or {}
    return json.loads(value) if isinstance(value, str) else dict(value)


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


class _PostgresRepository:
    def __init__(self, database_url: str) -> None:
        self.engine = create_engine(database_url)


class PostgresThreadRepository(_PostgresRepository):
    def create(self, thread: Thread) -> Thread:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """INSERT INTO threads
                    (id, workspace_id, owner_user_id, project_id, title, status, metadata,
                     created_at, updated_at, deleted_at)
                    VALUES (:id, :workspace_id, :owner_user_id, :project_id, :title, :status,
                            CAST(:metadata AS JSONB), :created_at, :updated_at, :deleted_at)
                    ON CONFLICT (id) DO NOTHING"""
                ),
                {**thread.model_dump(), "status": thread.status.value, "metadata": _json(thread.metadata)},
            )
        return self.get(thread.id) or thread

    def get(self, thread_id: str) -> Thread | None:
        with self.engine.begin() as conn:
            row = conn.execute(
                text("SELECT * FROM threads WHERE id = :id"), {"id": thread_id}
            ).mappings().first()
        if not row:
            return None
        return Thread.model_validate({**row, "metadata": _dict(row, "metadata")})

    def list_for_owner(
        self, workspace_id: str, user_id: str, *, limit: int = 50
    ) -> list[Thread]:
        with self.engine.begin() as conn:
            rows = conn.execute(
                text(
                    """SELECT * FROM threads
                    WHERE workspace_id = :workspace_id AND owner_user_id = :user_id
                    ORDER BY updated_at DESC LIMIT :limit"""
                ),
                {"workspace_id": workspace_id, "user_id": user_id, "limit": max(1, limit)},
            ).mappings().all()
        return [Thread.model_validate({**row, "metadata": _dict(row, "metadata")}) for row in rows]

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
        with self.engine.begin() as conn:
            rows = conn.execute(
                text(
                    """SELECT * FROM threads
                    WHERE workspace_id=:workspace_id AND owner_user_id=:user_id
                    AND (:include_deleted OR status <> 'DELETED')
                    AND (:cursor_time IS NULL OR (updated_at, id) < (:cursor_time, :cursor_id))
                    ORDER BY updated_at DESC, id DESC LIMIT :fetch_limit"""
                ),
                {
                    "workspace_id": workspace_id,
                    "user_id": user_id,
                    "include_deleted": include_deleted,
                    "cursor_time": boundary[0] if boundary else None,
                    "cursor_id": boundary[1] if boundary else None,
                    "fetch_limit": max(1, limit) + 1,
                },
            ).mappings().all()
        items = [Thread.model_validate({**row, "metadata": _dict(row, "metadata")}) for row in rows]
        has_more = len(items) > max(1, limit)
        items = items[: max(1, limit)]
        next_cursor = _cursor(items[-1].updated_at, items[-1].id) if has_more and items else None
        return items, next_cursor

    def update(self, thread: Thread) -> Thread:
        with self.engine.begin() as conn:
            result = conn.execute(
                text(
                    """UPDATE threads SET project_id=:project_id, title=:title, status=:status,
                    metadata=CAST(:metadata AS JSONB), updated_at=:updated_at,
                    deleted_at=:deleted_at WHERE id=:id"""
                ),
                {**thread.model_dump(), "status": thread.status.value, "metadata": _json(thread.metadata)},
            )
        if not result.rowcount:
            raise KeyError(thread.id)
        return thread


class PostgresMessageRepository(_PostgresRepository):
    def create(self, message: Message) -> Message:
        with self.engine.begin() as conn:
            result = conn.execute(
                text(
                    """INSERT INTO messages
                    (id, thread_id, workspace_id, user_id, role, run_id, parent_message_id,
                     created_at, updated_at)
                    VALUES (:id, :thread_id, :workspace_id, :user_id, :role, :run_id,
                            :parent_message_id, :created_at, :updated_at)
                    ON CONFLICT (id) DO NOTHING"""
                ),
                {**message.model_dump(exclude={"parts"}), "role": message.role.value},
            )
            if result.rowcount:
                for position, part in enumerate(message.parts):
                    conn.execute(
                        text(
                            """INSERT INTO message_parts
                            (message_id, position, part_type, content, metadata)
                            VALUES (:message_id, :position, :part_type, CAST(:content AS JSONB),
                                    CAST(:metadata AS JSONB))
                            ON CONFLICT (message_id, position) DO NOTHING"""
                        ),
                        {
                            "message_id": message.id,
                            "position": position,
                            "part_type": part.type.value,
                            "content": _json(part.content),
                            "metadata": _json(part.metadata),
                        },
                    )
        return self.get(message.id) or message

    def get(self, message_id: str) -> Message | None:
        with self.engine.begin() as conn:
            row = conn.execute(
                text("SELECT * FROM messages WHERE id=:id"), {"id": message_id}
            ).mappings().first()
            if not row:
                return None
            parts = conn.execute(
                text(
                    """SELECT part_type AS type, content, metadata FROM message_parts
                    WHERE message_id=:id ORDER BY position"""
                ),
                {"id": message_id},
            ).mappings().all()
        return Message.model_validate({**row, "parts": [dict(part) for part in parts]})

    def list_for_thread(self, thread_id: str, *, limit: int = 100) -> list[Message]:
        with self.engine.begin() as conn:
            ids = conn.execute(
                text(
                    """SELECT id FROM (
                        SELECT id, created_at FROM messages WHERE thread_id=:thread_id
                        ORDER BY created_at DESC, id DESC LIMIT :limit
                    ) recent ORDER BY created_at ASC, id ASC"""
                ),
                {"thread_id": thread_id, "limit": max(1, limit)},
            ).scalars().all()
        return [message for item_id in ids if (message := self.get(item_id)) is not None]

    def list_page(
        self, thread_id: str, *, limit: int = 100, cursor: str | None = None
    ) -> tuple[list[Message], str | None]:
        boundary = _decode_cursor(cursor)
        with self.engine.begin() as conn:
            ids = conn.execute(
                text(
                    """SELECT id FROM messages WHERE thread_id=:thread_id
                    AND (:cursor_time IS NULL OR (created_at, id) < (:cursor_time, :cursor_id))
                    ORDER BY created_at DESC, id DESC LIMIT :fetch_limit"""
                ),
                {
                    "thread_id": thread_id,
                    "cursor_time": boundary[0] if boundary else None,
                    "cursor_id": boundary[1] if boundary else None,
                    "fetch_limit": max(1, limit) + 1,
                },
            ).scalars().all()
        has_more = len(ids) > max(1, limit)
        ids = ids[: max(1, limit)]
        items = [message for item_id in ids if (message := self.get(item_id)) is not None]
        next_cursor = _cursor(items[-1].created_at, items[-1].id) if has_more and items else None
        return items, next_cursor

    def update(self, message: Message) -> Message:
        with self.engine.begin() as conn:
            result = conn.execute(
                text(
                    """UPDATE messages SET user_id=:user_id, role=:role, run_id=:run_id,
                    parent_message_id=:parent_message_id, updated_at=:updated_at WHERE id=:id"""
                ),
                {**message.model_dump(exclude={"parts"}), "role": message.role.value},
            )
            if result.rowcount:
                conn.execute(text("DELETE FROM message_parts WHERE message_id=:id"), {"id": message.id})
                for position, part in enumerate(message.parts):
                    conn.execute(
                        text(
                            """INSERT INTO message_parts
                            (message_id, position, part_type, content, metadata)
                            VALUES (:message_id, :position, :part_type, CAST(:content AS JSONB),
                                    CAST(:metadata AS JSONB))"""
                        ),
                        {
                            "message_id": message.id,
                            "position": position,
                            "part_type": part.type.value,
                            "content": _json(part.content),
                            "metadata": _json(part.metadata),
                        },
                    )
        if not result.rowcount:
            raise KeyError(message.id)
        return message


class PostgresRunRepository(_PostgresRepository):
    def create(self, run: Run) -> Run:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """INSERT INTO runs
                    (id, thread_id, workspace_id, requested_by_user_id, mission_id, status,
                     current_attempt, max_attempts, retry_count, next_retry_at,
                     cancel_requested_at, metadata, created_at, started_at, completed_at)
                    VALUES (:id, :thread_id, :workspace_id, :requested_by_user_id, :mission_id,
                            :status, :current_attempt, :max_attempts, :retry_count,
                            :next_retry_at, :cancel_requested_at, CAST(:metadata AS JSONB),
                            :created_at, :started_at, :completed_at)
                    ON CONFLICT (id) DO NOTHING"""
                ),
                {**run.model_dump(), "status": run.status.value, "metadata": _json(run.metadata)},
            )
        return self.get(run.id) or run

    def get(self, run_id: str) -> Run | None:
        with self.engine.begin() as conn:
            row = conn.execute(
                text("SELECT * FROM runs WHERE id=:id"), {"id": run_id}
            ).mappings().first()
        return Run.model_validate({**row, "metadata": _dict(row, "metadata")}) if row else None

    def update(self, run: Run) -> Run:
        with self.engine.begin() as conn:
            result = conn.execute(
                text(
                    """UPDATE runs SET mission_id=:mission_id, status=:status,
                    current_attempt=:current_attempt, max_attempts=:max_attempts,
                    retry_count=:retry_count, next_retry_at=:next_retry_at,
                    cancel_requested_at=:cancel_requested_at, metadata=CAST(:metadata AS JSONB),
                    started_at=:started_at, completed_at=:completed_at WHERE id=:id"""
                ),
                {**run.model_dump(), "status": run.status.value, "metadata": _json(run.metadata)},
            )
        if not result.rowcount:
            raise KeyError(run.id)
        return run

    def compare_and_set_status(
        self,
        run_id: str,
        expected_statuses: set[RunStatus],
        new_status: RunStatus,
        *,
        now: datetime | None = None,
    ) -> Run | None:
        if not expected_statuses:
            return None
        moment = now or datetime.now(UTC)
        terminal = new_status in {
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }
        with self.engine.begin() as conn:
            row = conn.execute(
                text(
                    """UPDATE runs SET status=:new_status,
                    started_at=CASE
                        WHEN :new_status='RUNNING' THEN COALESCE(started_at, :now)
                        ELSE started_at
                    END,
                    completed_at=CASE WHEN :terminal THEN :now ELSE completed_at END,
                    next_retry_at=CASE WHEN :terminal THEN NULL ELSE next_retry_at END
                    WHERE id=:id AND status = ANY(:expected_statuses)
                    RETURNING *"""
                ),
                {
                    "id": run_id,
                    "expected_statuses": [
                        status.value for status in expected_statuses
                    ],
                    "new_status": new_status.value,
                    "terminal": terminal,
                    "now": moment,
                },
            ).mappings().first()
        return (
            Run.model_validate({**row, "metadata": _dict(row, "metadata")})
            if row
            else None
        )

    def list_for_owner(
        self, workspace_id: str, user_id: str, *, limit: int = 100
    ) -> list[Run]:
        with self.engine.begin() as conn:
            rows = conn.execute(
                text(
                    """SELECT * FROM runs WHERE workspace_id=:workspace_id
                    AND requested_by_user_id=:user_id ORDER BY created_at DESC LIMIT :limit"""
                ),
                {"workspace_id": workspace_id, "user_id": user_id, "limit": max(1, limit)},
            ).mappings().all()
        return [Run.model_validate({**row, "metadata": _dict(row, "metadata")}) for row in rows]

    def add_attempt(self, attempt: RunAttempt) -> RunAttempt:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """INSERT INTO run_attempts
                    (id, run_id, attempt_number, status, error_code, error_message,
                     worker_id, lease_expires_at, heartbeat_at, retryable, version,
                     started_at, completed_at)
                    VALUES (:id, :run_id, :attempt_number, :status, :error_code, :error_message,
                            :worker_id, :lease_expires_at, :heartbeat_at, :retryable, :version,
                            :started_at, :completed_at) ON CONFLICT (id) DO NOTHING"""
                ),
                {**attempt.model_dump(), "status": attempt.status.value},
            )
        return attempt

    def update_attempt(self, attempt: RunAttempt) -> RunAttempt:
        with self.engine.begin() as conn:
            result = conn.execute(
                text(
                    """UPDATE run_attempts SET status=:status, error_code=:error_code,
                    error_message=:error_message, worker_id=:worker_id,
                    lease_expires_at=:lease_expires_at, heartbeat_at=:heartbeat_at,
                    retryable=:retryable, version=:version,
                    completed_at=:completed_at WHERE id=:id"""
                ),
                {**attempt.model_dump(), "status": attempt.status.value},
            )
        if not result.rowcount:
            raise KeyError(attempt.id)
        return attempt

    def claim(
        self, run_id: str, worker_id: str, lease_seconds: float, *, now: datetime | None = None
    ) -> RunAttempt | None:
        moment = now or datetime.now(UTC)
        with self.engine.begin() as conn:
            row = conn.execute(
                text(
                    """WITH candidate AS (
                        SELECT id FROM run_attempts
                        WHERE run_id=:run_id
                          AND (status='RETRYABLE' OR (
                            status='RUNNING'
                            AND (worker_id IS NULL OR lease_expires_at IS NULL
                                 OR lease_expires_at <= :now)
                          ))
                        ORDER BY attempt_number DESC
                        FOR UPDATE SKIP LOCKED LIMIT 1
                    )
                    UPDATE run_attempts AS attempt
                    SET status='RUNNING', worker_id=:worker_id, heartbeat_at=:now,
                        lease_expires_at=:lease_expires_at, version=attempt.version + 1
                    FROM candidate WHERE attempt.id=candidate.id
                    RETURNING attempt.*"""
                ),
                {
                    "run_id": run_id,
                    "worker_id": worker_id,
                    "now": moment,
                    "lease_expires_at": moment + timedelta(seconds=lease_seconds),
                },
            ).mappings().first()
        return RunAttempt.model_validate(row) if row else None

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
        with self.engine.begin() as conn:
            result = conn.execute(
                text(
                    """UPDATE run_attempts SET heartbeat_at=:now,
                    lease_expires_at=:lease_expires_at
                    WHERE id=:id AND worker_id=:worker_id AND status='RUNNING'
                    AND lease_expires_at > :now
                    AND (:expected_version IS NULL OR version=:expected_version)"""
                ),
                {
                    "id": attempt_id,
                    "worker_id": worker_id,
                    "now": moment,
                    "lease_expires_at": moment + timedelta(seconds=lease_seconds),
                    "expected_version": expected_version,
                },
            )
        return bool(result.rowcount)

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
        terminal = new_status in {
            RunAttemptStatus.COMPLETED,
            RunAttemptStatus.FAILED,
            RunAttemptStatus.CANCELLED,
        }
        with self.engine.begin() as conn:
            row = conn.execute(
                text(
                    """UPDATE run_attempts SET status=:new_status, error_code=:error_code,
                    error_message=:error_message, completed_at=:completed_at,
                    lease_expires_at=NULL, version=version + 1
                    WHERE id=:id AND status=:expected_status
                    AND (:worker_id IS NULL OR worker_id=:worker_id)
                    AND (:expected_version IS NULL OR version=:expected_version)
                    AND (:lease_expired_before IS NULL OR (
                        lease_expires_at IS NOT NULL
                        AND lease_expires_at <= :lease_expired_before
                    ))
                    RETURNING *"""
                ),
                {
                    "id": attempt_id,
                    "expected_status": expected_status.value,
                    "new_status": new_status.value,
                    "worker_id": worker_id,
                    "expected_version": expected_version,
                    "lease_expired_before": lease_expired_before,
                    "error_code": error_code,
                    "error_message": error_message,
                    "completed_at": moment if terminal else None,
                },
            ).mappings().first()
        return RunAttempt.model_validate(row) if row else None

    def cancel(self, run_id: str, *, now: datetime | None = None) -> bool:
        moment = now or datetime.now(UTC)
        with self.engine.begin() as conn:
            result = conn.execute(
                text(
                    """UPDATE runs SET status='CANCELLED', cancel_requested_at=:now,
                    completed_at=:now WHERE id=:id
                    AND status NOT IN ('COMPLETED', 'FAILED', 'CANCELLED')"""
                ),
                {"id": run_id, "now": moment},
            )
            if result.rowcount:
                conn.execute(
                    text(
                        """UPDATE run_attempts SET status='CANCELLED', completed_at=:now,
                        lease_expires_at=NULL, version=version + 1
                        WHERE run_id=:id AND status IN ('RUNNING', 'RETRYABLE')"""
                    ),
                    {"id": run_id, "now": moment},
                )
        return bool(result.rowcount)

    def list_recoverable(
        self, *, now: datetime | None = None, limit: int = 100
    ) -> list[RunAttempt]:
        moment = now or datetime.now(UTC)
        with self.engine.begin() as conn:
            rows = conn.execute(
                text(
                    """SELECT * FROM run_attempts
                    WHERE status='RETRYABLE'
                       OR (status='RUNNING' AND lease_expires_at IS NOT NULL
                           AND lease_expires_at <= :now)
                    ORDER BY started_at LIMIT :limit"""
                ),
                {"now": moment, "limit": max(1, limit)},
            ).mappings().all()
        return [RunAttempt.model_validate(row) for row in rows]

    def append_event(self, event: ActivityEvent) -> ActivityEvent:
        with self.engine.begin() as conn:
            if event.run_id:
                conn.execute(
                    text("SELECT pg_advisory_xact_lock(hashtext(:run_id))"),
                    {"run_id": event.run_id},
                )
            conn.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:thread_id))"),
                {"thread_id": event.thread_id},
            )
            row = conn.execute(
                text(
                    """INSERT INTO run_events
                    (id, thread_id, workspace_id, run_id, owner_user_id, actor_user_id, sequence,
                     thread_sequence, event_type, actor_type, actor_id, title, summary,
                     parent_event_id, visibility, evidence_ids, payload, metadata,
                     started_at, completed_at, duration_ms, event_json, created_at)
                    VALUES (:id, :thread_id, :workspace_id, :run_id, :owner_user_id, :actor_user_id,
                            (SELECT COALESCE(MAX(sequence), 0) + 1 FROM run_events
                             WHERE run_id=:run_id),
                            (SELECT COALESCE(MAX(thread_sequence), 0) + 1 FROM run_events
                             WHERE thread_id=:thread_id),
                            :event_type, :actor_type, :actor_id, :title, :summary,
                            :parent_event_id, :visibility, CAST(:evidence_ids AS JSONB),
                            CAST(:payload AS JSONB), CAST(:metadata AS JSONB),
                            :started_at, :completed_at, :duration_ms,
                            CAST(:event_json AS JSONB), :created_at)
                    ON CONFLICT (id) DO NOTHING
                    RETURNING sequence, thread_sequence"""
                ),
                {
                    **event.model_dump(),
                    "visibility": event.visibility.value,
                    "evidence_ids": _json(event.evidence_ids),
                    "payload": _json(event.payload),
                    "metadata": _json(event.metadata),
                    "event_json": _json(event.model_dump(mode="json")),
                },
            ).first()
            if row is None:
                row = conn.execute(
                    text(
                        """SELECT sequence, thread_sequence FROM run_events
                        WHERE id=:id"""
                    ),
                    {"id": event.id},
                ).first()
        return (
            event.model_copy(update={"sequence": int(row[0]), "thread_sequence": int(row[1])})
            if row
            else event
        )

    def list_events(self, run_id: str, *, after_sequence: int = 0) -> list[ActivityEvent]:
        with self.engine.begin() as conn:
            rows = conn.execute(
                text(
                    """SELECT event_json || jsonb_build_object(
                        'sequence', sequence, 'thread_sequence', thread_sequence
                    ) FROM run_events
                    WHERE run_id=:run_id AND sequence>:after ORDER BY sequence"""
                ),
                {"run_id": run_id, "after": after_sequence},
            ).scalars().all()
        return [ActivityEvent.model_validate(json.loads(row) if isinstance(row, str) else row) for row in rows]

    def list_thread_events(
        self, thread_id: str, *, after_sequence: int = 0
    ) -> list[ActivityEvent]:
        with self.engine.begin() as conn:
            rows = conn.execute(
                text(
                    """SELECT event_json || jsonb_build_object(
                        'sequence', sequence, 'thread_sequence', thread_sequence
                    )
                    FROM run_events
                    WHERE thread_id=:thread_id AND thread_sequence>:after
                    ORDER BY thread_sequence"""
                ),
                {"thread_id": thread_id, "after": after_sequence},
            ).scalars().all()
        return [
            ActivityEvent.model_validate(json.loads(row) if isinstance(row, str) else row)
            for row in rows
        ]


class PostgresArtifactRepository(_PostgresRepository):
    def put(self, artifact: Artifact) -> Artifact:
        artifact = artifact.require_provenance()
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """INSERT INTO artifacts
                    (id, workspace_id, artifact_type, payload, classification, evidence_ids,
                     provenance, mission_id, thread_id, run_id, message_id, created_at)
                    VALUES (:id, :workspace_id, :artifact_type, CAST(:payload AS JSONB),
                            :classification, CAST(:evidence_ids AS JSONB),
                            CAST(:provenance AS JSONB), :mission_id, :thread_id, :run_id,
                            :message_id, :created_at)
                    ON CONFLICT (id) DO UPDATE SET payload=EXCLUDED.payload,
                    classification=EXCLUDED.classification, evidence_ids=EXCLUDED.evidence_ids,
                    provenance=EXCLUDED.provenance"""
                ),
                {
                    **artifact.model_dump(exclude={"provenance", "evidence_ids"}),
                    "payload": _json(artifact.payload),
                    "evidence_ids": _json(artifact.evidence_ids),
                    "provenance": _json(artifact.provenance.model_dump()),
                },
            )
        return artifact

    def get(self, artifact_id: str) -> Artifact | None:
        with self.engine.begin() as conn:
            row = conn.execute(
                text("SELECT * FROM artifacts WHERE id=:id"), {"id": artifact_id}
            ).mappings().first()
        return Artifact.model_validate(row) if row else None

    def list_for_mission(self, mission_id: str) -> list[Artifact]:
        with self.engine.begin() as conn:
            rows = conn.execute(
                text("SELECT * FROM artifacts WHERE mission_id=:id ORDER BY created_at"),
                {"id": mission_id},
            ).mappings().all()
        return [Artifact.model_validate(row) for row in rows]

    def list_for_context(self, workspace_id: str, thread_id: str) -> list[Artifact]:
        with self.engine.begin() as conn:
            rows = conn.execute(
                text(
                    """SELECT * FROM artifacts WHERE workspace_id=:workspace_id
                    AND thread_id=:thread_id ORDER BY created_at DESC"""
                ),
                {"workspace_id": workspace_id, "thread_id": thread_id},
            ).mappings().all()
        return [Artifact.model_validate(row) for row in rows]


class PostgresAttachmentRepository(_PostgresRepository):
    def create(self, attachment: Attachment) -> Attachment:
        attachment = Attachment.model_validate(attachment.model_dump())
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """INSERT INTO attachments
                    (id, thread_id, workspace_id, owner_user_id, message_id, filename,
                     content_type, size_bytes, storage_uri, checksum_sha256, status,
                     scan_status, scan_detail, created_at)
                    VALUES (:id, :thread_id, :workspace_id, :owner_user_id, :message_id,
                            :filename, :content_type, :size_bytes, :storage_uri,
                            :checksum_sha256, :status, :scan_status, :scan_detail, :created_at)
                    ON CONFLICT (id) DO NOTHING"""
                ),
                {
                    **attachment.model_dump(),
                    "status": attachment.status.value,
                    "scan_status": attachment.scan_status.value,
                },
            )
        return self.get(attachment.id) or attachment

    def get(self, attachment_id: str) -> Attachment | None:
        with self.engine.begin() as conn:
            row = conn.execute(
                text("SELECT * FROM attachments WHERE id=:id"), {"id": attachment_id}
            ).mappings().first()
        return Attachment.model_validate(row) if row else None

    def update(self, attachment: Attachment) -> Attachment:
        attachment = Attachment.model_validate(attachment.model_dump())
        with self.engine.begin() as conn:
            result = conn.execute(
                text(
                    """UPDATE attachments SET message_id=:message_id, size_bytes=:size_bytes,
                    storage_uri=:storage_uri, checksum_sha256=:checksum_sha256, status=:status,
                    scan_status=:scan_status, scan_detail=:scan_detail
                    WHERE id=:id"""
                ),
                {
                    **attachment.model_dump(),
                    "status": attachment.status.value,
                    "scan_status": attachment.scan_status.value,
                },
            )
        if not result.rowcount:
            raise KeyError(attachment.id)
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
        with self.engine.begin() as conn:
            locked = []
            for attachment_id in unique_ids:
                row = conn.execute(
                    text("SELECT * FROM attachments WHERE id=:id FOR UPDATE"),
                    {"id": attachment_id},
                ).mappings().first()
                if row is None:
                    return False
                locked.append(row)
            if any(
                row["thread_id"] != thread_id
                or row["workspace_id"] != workspace_id
                or row["owner_user_id"] != owner_user_id
                or row["status"] != "READY"
                or row["scan_status"] != "CLEAN"
                or row["message_id"] is not None
                for row in locked
            ):
                return False
            for attachment_id in unique_ids:
                conn.execute(
                    text("UPDATE attachments SET message_id=:message_id WHERE id=:id"),
                    {"message_id": message_id, "id": attachment_id},
                )
        return True

    def delete(self, attachment_id: str) -> bool:
        with self.engine.begin() as conn:
            result = conn.execute(
                text("UPDATE attachments SET status='DELETED' WHERE id=:id"),
                {"id": attachment_id},
            )
        return bool(result.rowcount)

    def list_for_thread(self, thread_id: str) -> list[Attachment]:
        with self.engine.begin() as conn:
            rows = conn.execute(
                text("SELECT * FROM attachments WHERE thread_id=:id ORDER BY created_at"),
                {"id": thread_id},
            ).mappings().all()
        return [Attachment.model_validate(row) for row in rows]


class PostgresMemoryRepository(_PostgresRepository):
    _json_fields: ClassVar[set[str]] = {
        "content",
        "structured_data",
        "provenance",
        "source_message_ids",
        "source_evidence_ids",
    }

    @classmethod
    def _params(cls, memory: MemoryItem) -> dict[str, Any]:
        values = memory.model_dump()
        for key in cls._json_fields:
            values[key] = _json(values[key])
        values.update(
            scope=memory.scope.value,
            type=memory.type.value,
            status=memory.status.value,
        )
        return values

    def create(self, memory: MemoryItem) -> MemoryItem:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """INSERT INTO memories
                    (id, workspace_id, owner_user_id, project_id, thread_id, scope, type, status,
                     content, normalized_content, structured_data, provenance, confidence, salience,
                     source_run_id, source_message_id, source_message_ids, source_evidence_ids,
                     supersedes_id, superseded_by_id, consented_at, pinned, requires_confirmation,
                     valid_from, valid_to, expires_at, last_used_at, archived_at, deleted_at,
                     created_at, updated_at)
                    VALUES (:id, :workspace_id, :owner_user_id, :project_id, :thread_id, :scope,
                     :type, :status, CAST(:content AS JSONB), :normalized_content,
                     CAST(:structured_data AS JSONB), CAST(:provenance AS JSONB), :confidence,
                     :salience, :source_run_id, :source_message_id,
                     CAST(:source_message_ids AS JSONB), CAST(:source_evidence_ids AS JSONB),
                     :supersedes_id, :superseded_by_id, :consented_at, :pinned,
                     :requires_confirmation, :valid_from, :valid_to, :expires_at, :last_used_at,
                     :archived_at, :deleted_at, :created_at, :updated_at)
                    ON CONFLICT (id) DO NOTHING"""
                ),
                self._params(memory),
            )
        return self.get(memory.id, memory.workspace_id, memory.owner_user_id) or memory

    def get(
        self, memory_id: str, workspace_id: str, owner_user_id: str
    ) -> MemoryItem | None:
        with self.engine.begin() as conn:
            row = conn.execute(
                text(
                    """SELECT * FROM memories WHERE id=:id AND workspace_id=:workspace_id
                    AND owner_user_id=:owner_user_id AND deleted_at IS NULL"""
                ),
                {
                    "id": memory_id,
                    "workspace_id": workspace_id,
                    "owner_user_id": owner_user_id,
                },
            ).mappings().first()
        return MemoryItem.model_validate(row) if row else None

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
        active = "" if include_inactive else """AND status='ACTIVE'
            AND (valid_from IS NULL OR valid_from <= NOW())
            AND (valid_to IS NULL OR valid_to > NOW())
            AND (expires_at IS NULL OR expires_at > NOW())"""
        with self.engine.begin() as conn:
            rows = conn.execute(
                text(
                    f"""SELECT * FROM memories WHERE workspace_id=:workspace_id
                    AND owner_user_id=:owner_user_id AND deleted_at IS NULL
                    AND (:project_id IS NULL OR project_id IS NULL OR project_id=:project_id)
                    AND (:thread_id IS NULL OR thread_id IS NULL OR thread_id=:thread_id)
                    {active}
                    ORDER BY pinned DESC, updated_at DESC LIMIT :limit"""
                ),
                {
                    "workspace_id": workspace_id,
                    "owner_user_id": owner_user_id,
                    "project_id": project_id,
                    "thread_id": thread_id,
                    "limit": max(1, limit),
                },
            ).mappings().all()
        return [MemoryItem.model_validate(row) for row in rows]

    def update(self, memory: MemoryItem) -> MemoryItem:
        with self.engine.begin() as conn:
            result = conn.execute(
                text(
                    """UPDATE memories SET project_id=:project_id, thread_id=:thread_id,
                    scope=:scope, type=:type, status=:status, content=CAST(:content AS JSONB),
                    normalized_content=:normalized_content,
                    structured_data=CAST(:structured_data AS JSONB),
                    provenance=CAST(:provenance AS JSONB), confidence=:confidence,
                    salience=:salience, source_run_id=:source_run_id,
                    source_message_id=:source_message_id,
                    source_message_ids=CAST(:source_message_ids AS JSONB),
                    source_evidence_ids=CAST(:source_evidence_ids AS JSONB),
                    supersedes_id=:supersedes_id, superseded_by_id=:superseded_by_id,
                    consented_at=:consented_at, pinned=:pinned,
                    requires_confirmation=:requires_confirmation, valid_from=:valid_from,
                    valid_to=:valid_to, expires_at=:expires_at, last_used_at=:last_used_at,
                    archived_at=:archived_at, deleted_at=:deleted_at, updated_at=:updated_at
                    WHERE id=:id AND workspace_id=:workspace_id
                    AND owner_user_id=:owner_user_id"""
                ),
                self._params(memory),
            )
        if not result.rowcount:
            raise KeyError(memory.id)
        return memory

    def delete(self, memory_id: str, workspace_id: str, owner_user_id: str) -> bool:
        with self.engine.begin() as conn:
            result = conn.execute(
                text(
                    """UPDATE memories SET status='DELETED', deleted_at=NOW(), updated_at=NOW()
                    WHERE id=:id AND workspace_id=:workspace_id
                    AND owner_user_id=:owner_user_id AND deleted_at IS NULL"""
                ),
                {
                    "id": memory_id,
                    "workspace_id": workspace_id,
                    "owner_user_id": owner_user_id,
                },
            )
        return bool(result.rowcount)

    def record_usage(
        self, run_id: str, memories: builtins.list[MemoryItem], provenance: dict[str, object]
    ) -> None:
        with self.engine.begin() as conn:
            for memory in memories:
                conn.execute(
                    text(
                        """INSERT INTO run_memory_usage
                        (run_id, memory_id, workspace_id, owner_user_id, provenance)
                        VALUES (:run_id, :memory_id, :workspace_id, :owner_user_id,
                                CAST(:provenance AS JSONB))
                        ON CONFLICT (run_id, memory_id) DO NOTHING"""
                    ),
                    {
                        "run_id": run_id,
                        "memory_id": memory.id,
                        "workspace_id": memory.workspace_id,
                        "owner_user_id": memory.owner_user_id,
                        "provenance": _json(provenance),
                    },
                )
                conn.execute(
                    text(
                        """UPDATE memories SET last_used_at=NOW() WHERE id=:id
                        AND workspace_id=:workspace_id AND owner_user_id=:owner_user_id"""
                    ),
                    {
                        "id": memory.id,
                        "workspace_id": memory.workspace_id,
                        "owner_user_id": memory.owner_user_id,
                    },
                )

    def list_used_by_run(
        self, run_id: str, workspace_id: str, owner_user_id: str
    ) -> builtins.list[MemoryItem]:
        with self.engine.begin() as conn:
            rows = conn.execute(
                text(
                    """SELECT m.* FROM memories m JOIN run_memory_usage u ON u.memory_id=m.id
                    WHERE u.run_id=:run_id AND u.workspace_id=:workspace_id
                    AND u.owner_user_id=:owner_user_id AND m.deleted_at IS NULL
                    ORDER BY u.used_at"""
                ),
                {
                    "run_id": run_id,
                    "workspace_id": workspace_id,
                    "owner_user_id": owner_user_id,
                },
            ).mappings().all()
        return [MemoryItem.model_validate(row) for row in rows]

    def get_preference(self, workspace_id: str, owner_user_id: str) -> MemoryPreference:
        with self.engine.begin() as conn:
            row = conn.execute(
                text(
                    """SELECT * FROM memory_preferences WHERE workspace_id=:workspace_id
                    AND owner_user_id=:owner_user_id"""
                ),
                {"workspace_id": workspace_id, "owner_user_id": owner_user_id},
            ).mappings().first()
        return (
            MemoryPreference.model_validate(row)
            if row
            else MemoryPreference(workspace_id=workspace_id, owner_user_id=owner_user_id)
        )

    def set_preference(self, preference: MemoryPreference) -> MemoryPreference:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """INSERT INTO memory_preferences
                    (workspace_id, owner_user_id, opted_out, updated_at)
                    VALUES (:workspace_id, :owner_user_id, :opted_out, :updated_at)
                    ON CONFLICT (workspace_id, owner_user_id) DO UPDATE
                    SET opted_out=EXCLUDED.opted_out, updated_at=EXCLUDED.updated_at"""
                ),
                preference.model_dump(),
            )
        return preference


class PostgresThreadSummaryRepository(_PostgresRepository):
    def create(self, summary: ThreadSummary) -> ThreadSummary:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """INSERT INTO thread_summaries
                    (id, thread_id, workspace_id, owner_user_id, through_message_id,
                     covered_message_ids, source_evidence_ids, mode, version, summary,
                     metadata, created_at)
                    VALUES (:id, :thread_id, :workspace_id, :owner_user_id,
                     :through_message_id, CAST(:covered_message_ids AS JSONB),
                     CAST(:source_evidence_ids AS JSONB), :mode, :version, :summary,
                     CAST(:metadata AS JSONB), :created_at)
                    ON CONFLICT (id) DO NOTHING"""
                ),
                {
                    **summary.model_dump(
                        exclude={"covered_message_ids", "source_evidence_ids", "metadata"}
                    ),
                    "covered_message_ids": _json(summary.covered_message_ids),
                    "source_evidence_ids": _json(summary.source_evidence_ids),
                    "metadata": _json(summary.metadata),
                },
            )
        return summary

    def latest(
        self, thread_id: str, workspace_id: str, owner_user_id: str
    ) -> ThreadSummary | None:
        with self.engine.begin() as conn:
            row = conn.execute(
                text(
                    """SELECT * FROM thread_summaries WHERE thread_id=:thread_id
                    AND workspace_id=:workspace_id AND owner_user_id=:owner_user_id
                    ORDER BY created_at DESC LIMIT 1"""
                ),
                {
                    "thread_id": thread_id,
                    "workspace_id": workspace_id,
                    "owner_user_id": owner_user_id,
                },
            ).mappings().first()
        return ThreadSummary.model_validate(row) if row else None


def build_conversation_repositories(
    backend: str,
    database_url: str,
    *,
    query_embedder: QueryEmbeddingHook | None = None,
) -> ConversationRepositories:
    if backend != "postgres":
        return build_in_memory_repositories(query_embedder=query_embedder)
    if not database_url.strip():
        raise ValueError("persistence_backend=postgres requires a non-empty database_url")
    return ConversationRepositories(
        threads=PostgresThreadRepository(database_url),
        messages=PostgresMessageRepository(database_url),
        runs=PostgresRunRepository(database_url),
        artifacts=PostgresArtifactRepository(database_url),
        attachments=PostgresAttachmentRepository(database_url),
        memories=PostgresMemoryRepository(database_url),
        thread_summaries=PostgresThreadSummaryRepository(database_url),
        search=PostgresSearchRepository(database_url, query_embedder=query_embedder),
        approvals=PostgresApprovalRepository(database_url),
    )
