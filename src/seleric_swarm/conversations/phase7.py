"""Scoped search, reciprocal-rank fusion, and human approval persistence."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from threading import RLock
from typing import Any

from sqlalchemy import create_engine, text

from seleric_swarm.conversations.contracts import (
    ApprovalDecisionEvent,
    ApprovalRequest,
    ApprovalStatus,
    Principal,
    RollbackRecord,
    SearchResult,
)

VectorSearchHook = Callable[[str, str, str, int], Iterable[SearchResult]]


def reciprocal_rank_fusion(
    lexical: Iterable[SearchResult],
    vector: Iterable[SearchResult] = (),
    recency: Iterable[SearchResult] = (),
    *,
    k: int = 60,
    lexical_weight: float = 1.0,
    vector_weight: float = 0.8,
    recency_weight: float = 0.25,
) -> list[SearchResult]:
    """Fuse ranked lists without mixing authorization domains."""
    fused: dict[tuple[str, str], SearchResult] = {}
    scores: dict[tuple[str, str], float] = {}
    ranked = (
        ("lexical_rank", lexical, lexical_weight),
        ("vector_rank", vector, vector_weight),
        ("recency_rank", recency, recency_weight),
    )
    for rank_field, items, weight in ranked:
        for rank, item in enumerate(items, 1):
            key = (item.kind, item.id)
            fused.setdefault(key, item)
            scores[key] = scores.get(key, 0.0) + weight / (k + rank)
            fused[key] = fused[key].model_copy(update={rank_field: rank})
    return sorted(
        (item.model_copy(update={"score": scores[key]}) for key, item in fused.items()),
        key=lambda item: (item.score, item.created_at),
        reverse=True,
    )


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, default=str, ensure_ascii=False)


class InMemorySearchRepository:
    def __init__(
        self,
        threads: Any,
        messages: Any,
        runs: Any,
        artifacts: Any,
        memories: Any,
        *,
        vector_hook: VectorSearchHook | None = None,
    ) -> None:
        self.threads = threads
        self.messages = messages
        self.runs = runs
        self.artifacts = artifacts
        self.memories = memories
        self.vector_hook = vector_hook

    def search(
        self,
        query: str,
        workspace_id: str,
        owner_user_id: str,
        *,
        kinds: set[str] | None = None,
        limit: int = 20,
        vector: list[float] | None = None,
    ) -> list[SearchResult]:
        del vector
        terms = [term.casefold() for term in query.split() if term.strip()]
        if not terms:
            return []
        allowed = kinds or {"thread", "message", "memory", "artifact", "run"}
        found: list[SearchResult] = []
        threads = self.threads.list_for_owner(workspace_id, owner_user_id, limit=1000)
        thread_ids = {thread.id for thread in threads}
        for thread in threads:
            if "thread" in allowed:
                found.append(SearchResult(
                    id=thread.id, kind="thread", title=thread.title or "Untitled thread",
                    snippet=_text(thread.metadata), thread_id=thread.id,
                    created_at=thread.updated_at,
                ))
            if "message" in allowed:
                for message in self.messages.list_for_thread(thread.id, limit=1000):
                    content = " ".join(_text(part.content) for part in message.parts)
                    found.append(SearchResult(
                        id=message.id, kind="message", title=f"{message.role.value.title()} message",
                        snippet=content, thread_id=thread.id, run_id=message.run_id,
                        created_at=message.created_at,
                    ))
            if "artifact" in allowed:
                for artifact in self.artifacts.list_for_context(workspace_id, thread.id):
                    found.append(SearchResult(
                        id=artifact.id, kind="artifact",
                        title=str(artifact.payload.get("title") or artifact.artifact_type),
                        snippet=_text(artifact.payload), thread_id=thread.id, run_id=artifact.run_id,
                        created_at=artifact.created_at,
                        metadata={"artifact_type": artifact.artifact_type},
                    ))
        if "memory" in allowed:
            for memory in self.memories.list(
                workspace_id, owner_user_id, include_inactive=True, limit=1000
            ):
                found.append(SearchResult(
                    id=memory.id, kind="memory", title=memory.type.value.title(),
                    snippet=memory.normalized_content or _text(memory.content),
                    thread_id=memory.thread_id, run_id=memory.source_run_id,
                    created_at=memory.updated_at,
                    metadata={"status": memory.status.value, "provenance": memory.provenance},
                ))
        if "run" in allowed:
            for run in self.runs.list_for_owner(workspace_id, owner_user_id, limit=1000):
                if run.thread_id not in thread_ids:
                    continue
                found.append(SearchResult(
                    id=run.id, kind="run", title=f"Run {run.status.value.title()}",
                    snippet=_text(run.metadata), thread_id=run.thread_id, run_id=run.id,
                    created_at=run.created_at, metadata={"status": run.status.value},
                ))
        lexical = [
            item for item in found
            if all(term in f"{item.title} {item.snippet}".casefold() for term in terms)
        ]
        lexical.sort(
            key=lambda item: sum(
                f"{item.title} {item.snippet}".casefold().count(term) for term in terms
            ),
            reverse=True,
        )
        recency = sorted(lexical, key=lambda item: item.created_at, reverse=True)
        vectors = list(self.vector_hook(query, workspace_id, owner_user_id, limit * 3)) if self.vector_hook else []
        return reciprocal_rank_fusion(lexical, vectors, recency)[: max(1, limit)]


class PostgresSearchRepository:
    def __init__(self, database_url: str, *, vector_hook: VectorSearchHook | None = None) -> None:
        self.engine = create_engine(database_url)
        self.vector_hook = vector_hook

    def search(
        self,
        query: str,
        workspace_id: str,
        owner_user_id: str,
        *,
        kinds: set[str] | None = None,
        limit: int = 20,
        vector: list[float] | None = None,
    ) -> list[SearchResult]:
        del vector
        allowed = sorted(kinds or {"thread", "message", "memory", "artifact", "run"})
        with self.engine.begin() as conn:
            rows = conn.execute(
                text(
                    """SELECT id, kind, title, snippet, thread_id, run_id, created_at, metadata,
                       ts_rank(search_vector, websearch_to_tsquery('simple', :query)) AS rank
                    FROM search_documents
                    WHERE workspace_id=:workspace_id AND owner_user_id=:owner_user_id
                      AND kind = ANY(:kinds)
                      AND search_vector @@ websearch_to_tsquery('simple', :query)
                    ORDER BY rank DESC, created_at DESC LIMIT :candidate_limit"""
                ),
                {
                    "query": query, "workspace_id": workspace_id,
                    "owner_user_id": owner_user_id, "kinds": allowed,
                    "candidate_limit": max(1, limit) * 4,
                },
            ).mappings().all()
        lexical = [SearchResult.model_validate(row) for row in rows]
        recency = sorted(lexical, key=lambda item: item.created_at, reverse=True)
        vectors = list(self.vector_hook(query, workspace_id, owner_user_id, limit * 3)) if self.vector_hook else []
        return reciprocal_rank_fusion(lexical, vectors, recency)[: max(1, limit)]


class InMemoryApprovalRepository:
    def __init__(self) -> None:
        self._items: dict[str, ApprovalRequest] = {}
        self._events: dict[str, list[ApprovalDecisionEvent]] = {}
        self._rollbacks: dict[str, RollbackRecord] = {}
        self._lock = RLock()

    def create(self, approval: ApprovalRequest) -> ApprovalRequest:
        with self._lock:
            existing = self.get_by_idempotency(
                approval.workspace_id, approval.owner_user_id, approval.idempotency_key
            )
            if existing:
                return existing
            self._items[approval.id] = approval
            self._events[approval.id] = []
            return approval

    def get(self, approval_id: str, workspace_id: str, owner_user_id: str) -> ApprovalRequest | None:
        item = self._items.get(approval_id)
        if not item or item.workspace_id != workspace_id or item.owner_user_id != owner_user_id:
            return None
        return item

    def get_by_idempotency(
        self, workspace_id: str, owner_user_id: str, idempotency_key: str
    ) -> ApprovalRequest | None:
        return next((item for item in self._items.values() if item.workspace_id == workspace_id
                     and item.owner_user_id == owner_user_id
                     and item.idempotency_key == idempotency_key), None)

    def transition(
        self, approval_id: str, expected_status: str, approval: ApprovalRequest,
        event: ApprovalDecisionEvent,
    ) -> ApprovalRequest | None:
        with self._lock:
            current = self._items.get(approval_id)
            if current is None or current.status.value != expected_status:
                return None
            self._items[approval_id] = approval
            self._events.setdefault(approval_id, []).append(event)
            return approval

    def list_events(self, approval_id: str) -> list[ApprovalDecisionEvent]:
        return list(self._events.get(approval_id, []))

    def add_rollback(self, record: RollbackRecord) -> RollbackRecord:
        self._rollbacks.setdefault(record.id, record)
        return self._rollbacks[record.id]


class PostgresApprovalRepository:
    def __init__(self, database_url: str) -> None:
        self.engine = create_engine(database_url)

    @staticmethod
    def _params(item: ApprovalRequest) -> dict[str, Any]:
        values = item.model_dump()
        values["status"] = item.status.value
        values["action_preview"] = json.dumps(item.action_preview, default=str)
        return values

    def create(self, approval: ApprovalRequest) -> ApprovalRequest:
        with self.engine.begin() as conn:
            row = conn.execute(text(
                """INSERT INTO approval_requests
                (id,workspace_id,owner_user_id,run_id,action_type,action_preview,required_role,
                 status,idempotency_key,dry_run,checkpoint_resume_token,expires_at,created_at,updated_at)
                VALUES (:id,:workspace_id,:owner_user_id,:run_id,:action_type,
                 CAST(:action_preview AS JSONB),:required_role,:status,:idempotency_key,:dry_run,
                 :checkpoint_resume_token,:expires_at,:created_at,:updated_at)
                ON CONFLICT (workspace_id,owner_user_id,idempotency_key) DO UPDATE
                SET idempotency_key=EXCLUDED.idempotency_key RETURNING *"""
            ), self._params(approval)).mappings().one()
        return ApprovalRequest.model_validate(row)

    def get(self, approval_id: str, workspace_id: str, owner_user_id: str) -> ApprovalRequest | None:
        with self.engine.begin() as conn:
            row = conn.execute(text(
                """SELECT * FROM approval_requests WHERE id=:id AND workspace_id=:workspace_id
                   AND owner_user_id=:owner_user_id"""
            ), {"id": approval_id, "workspace_id": workspace_id,
                "owner_user_id": owner_user_id}).mappings().first()
        return ApprovalRequest.model_validate(row) if row else None

    def get_by_idempotency(
        self, workspace_id: str, owner_user_id: str, idempotency_key: str
    ) -> ApprovalRequest | None:
        with self.engine.begin() as conn:
            row = conn.execute(text(
                """SELECT * FROM approval_requests WHERE workspace_id=:workspace_id
                   AND owner_user_id=:owner_user_id AND idempotency_key=:key"""
            ), {"workspace_id": workspace_id, "owner_user_id": owner_user_id,
                "key": idempotency_key}).mappings().first()
        return ApprovalRequest.model_validate(row) if row else None

    def transition(
        self, approval_id: str, expected_status: str, approval: ApprovalRequest,
        event: ApprovalDecisionEvent,
    ) -> ApprovalRequest | None:
        with self.engine.begin() as conn:
            row = conn.execute(text(
                """UPDATE approval_requests SET status=:status, dry_run=:dry_run,
                   checkpoint_resume_token=:checkpoint_resume_token, updated_at=:updated_at
                   WHERE id=:id AND status=:expected RETURNING *"""
            ), {**self._params(approval), "expected": expected_status}).mappings().first()
            if row:
                conn.execute(text(
                    """INSERT INTO approval_decision_events
                    (id,approval_id,from_status,to_status,actor_principal_id,reason,metadata,created_at)
                    VALUES (:id,:approval_id,:from_status,:to_status,:actor_principal_id,:reason,
                     CAST(:metadata AS JSONB),:created_at)"""
                ), {**event.model_dump(), "from_status": event.from_status.value if event.from_status else None,
                    "to_status": event.to_status.value,
                    "metadata": json.dumps(event.metadata, default=str)})
        return ApprovalRequest.model_validate(row) if row else None

    def list_events(self, approval_id: str) -> list[ApprovalDecisionEvent]:
        with self.engine.begin() as conn:
            rows = conn.execute(text(
                "SELECT * FROM approval_decision_events WHERE approval_id=:id ORDER BY created_at"
            ), {"id": approval_id}).mappings().all()
        return [ApprovalDecisionEvent.model_validate(row) for row in rows]

    def add_rollback(self, record: RollbackRecord) -> RollbackRecord:
        with self.engine.begin() as conn:
            conn.execute(text(
                """INSERT INTO approval_rollbacks
                (id,approval_id,actor_principal_id,action,outcome,created_at)
                VALUES (:id,:approval_id,:actor_principal_id,CAST(:action AS JSONB),
                 CAST(:outcome AS JSONB),:created_at) ON CONFLICT (id) DO NOTHING"""
            ), {**record.model_dump(), "action": json.dumps(record.action),
                "outcome": json.dumps(record.outcome)})
        return record


_TRANSITIONS: dict[ApprovalStatus, set[ApprovalStatus]] = {
    ApprovalStatus.REQUESTED: {
        ApprovalStatus.APPROVED, ApprovalStatus.REJECTED,
        ApprovalStatus.EXPIRED, ApprovalStatus.CANCELLED,
    },
    ApprovalStatus.APPROVED: {
        ApprovalStatus.EXECUTED, ApprovalStatus.EXPIRED, ApprovalStatus.CANCELLED,
    },
    ApprovalStatus.EXECUTED: {ApprovalStatus.ROLLED_BACK},
}


def transition_approval(
    repository: Any,
    approval: ApprovalRequest,
    target: ApprovalStatus,
    principal: Principal,
    *,
    reason: str | None = None,
    allow_write_actions: bool = False,
    now: datetime | None = None,
) -> ApprovalRequest:
    moment = now or datetime.now(UTC)
    if approval.expires_at and approval.expires_at <= moment and target not in {
        ApprovalStatus.EXPIRED, ApprovalStatus.CANCELLED
    }:
        target = ApprovalStatus.EXPIRED
    if target not in _TRANSITIONS.get(approval.status, set()):
        raise ValueError(f"invalid approval transition {approval.status.value}->{target.value}")
    if target in {ApprovalStatus.APPROVED, ApprovalStatus.REJECTED} and (
        approval.required_role.lower() not in {role.lower() for role in principal.roles}
        and not principal.is_admin
    ):
        raise PermissionError(f"role {approval.required_role!r} is required")
    if target is ApprovalStatus.EXECUTED:
        if approval.status is not ApprovalStatus.APPROVED:
            raise PermissionError("unapproved actions cannot execute")
        if not allow_write_actions:
            raise PermissionError("write actions are disabled")
        if approval.dry_run:
            raise PermissionError("dry-run approvals cannot execute")
    updated = approval.model_copy(update={"status": target, "updated_at": moment})
    event = ApprovalDecisionEvent(
        approval_id=approval.id, from_status=approval.status, to_status=target,
        actor_principal_id=principal.principal_id, reason=reason,
    )
    persisted = repository.transition(approval.id, approval.status.value, updated, event)
    if persisted is None:
        raise RuntimeError("approval changed concurrently")
    return persisted
