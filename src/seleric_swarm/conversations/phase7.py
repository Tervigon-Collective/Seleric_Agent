"""Scoped search, reciprocal-rank fusion, and human approval persistence."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from threading import RLock
from typing import Any, Literal, Protocol

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from seleric_swarm.conversations.contracts import (
    ApprovalDecisionEvent,
    ApprovalRequest,
    ApprovalStatus,
    Principal,
    RollbackRecord,
    SearchResult,
)
from seleric_swarm.observability.tracing import operation_span

VectorSearchHook = Callable[[str, str, str, int], Iterable[SearchResult]]
QueryEmbeddingHook = Callable[[str], list[float]]


def build_query_embedder(settings: Any) -> QueryEmbeddingHook | None:
    """Build a synchronous embedding hook only when fully configured."""
    model = str(getattr(settings, "search_embedding_model", "") or "").strip()
    endpoint = str(getattr(settings, "azure_openai_endpoint", "") or "").strip().rstrip("/")
    api_key = str(getattr(settings, "azure_openai_api_key", "") or "").strip()
    if not (model and endpoint and api_key):
        return None
    if getattr(settings, "azure_auth_style", "openai_compatible") == "azure":
        from openai import AzureOpenAI

        client: Any = AzureOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version=getattr(settings, "azure_openai_api_version", "2024-05-01-preview"),
            timeout=getattr(settings, "llm_timeout_s", 30.0),
        )
    else:
        from openai import OpenAI

        base_url = endpoint if endpoint.endswith(("/v1", "/models")) else f"{endpoint}/models"
        client = OpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=getattr(settings, "llm_timeout_s", 30.0),
            default_query={
                "api-version": getattr(
                    settings, "azure_openai_api_version", "2024-05-01-preview"
                )
            },
        )

    def embed(query: str) -> list[float]:
        response = client.embeddings.create(model=model, input=query)
        return [float(value) for value in response.data[0].embedding]

    return embed


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
        query_embedder: QueryEmbeddingHook | None = None,
    ) -> None:
        self.threads = threads
        self.messages = messages
        self.runs = runs
        self.artifacts = artifacts
        self.memories = memories
        self.vector_hook = vector_hook
        self.query_embedder = query_embedder

    def populate_embedding(self, kind: str, document_id: str, content: str) -> bool:
        """Invoke the configured population hook; in-memory indexes own storage."""
        del kind, document_id
        if not self.query_embedder:
            return False
        try:
            self.query_embedder(content)
            return True
        except Exception:
            return False

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
            if {"artifact", "report"} & allowed:
                for artifact in self.artifacts.list_for_context(workspace_id, thread.id):
                    result_kind: Literal["artifact", "report"] = (
                        "report" if "report" in allowed and artifact.artifact_type == "report"
                        else "artifact"
                    )
                    if result_kind not in allowed:
                        continue
                    found.append(SearchResult(
                        id=artifact.id, kind=result_kind,
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
        if vector is None and self.query_embedder:
            try:
                vector = self.query_embedder(query)
            except Exception:
                vector = None
        del vector  # In-memory hooks own their similarity implementation.
        vectors = (
            list(self.vector_hook(query, workspace_id, owner_user_id, limit * 3))
            if self.vector_hook
            else []
        )
        authorized = {(item.kind, item.id) for item in found}
        vectors = [item for item in vectors if (item.kind, item.id) in authorized]
        with operation_span(
            "retrieval",
            "hybrid_search",
            {"backend": "memory", "lexical": len(lexical), "vector": len(vectors)},
        ):
            return reciprocal_rank_fusion(lexical, vectors, recency)[: max(1, limit)]


class PostgresSearchRepository:
    def __init__(
        self,
        database_url: str | Engine,
        *,
        vector_hook: VectorSearchHook | None = None,
        query_embedder: QueryEmbeddingHook | None = None,
    ) -> None:
        self.engine = (
            create_engine(database_url) if isinstance(database_url, str) else database_url
        )
        self.vector_hook = vector_hook
        self.query_embedder = query_embedder

    def populate_embedding(self, kind: str, document_id: str, content: str) -> bool:
        if not self.query_embedder:
            return False
        table = {
            "thread": "threads",
            "message": "message_parts",
            "memory": "memories",
            "artifact": "artifacts",
            "report": "artifacts",
            "run": "runs",
        }.get(kind)
        if table is None:
            raise ValueError(f"unsupported search kind {kind!r}")
        try:
            embedding = self.query_embedder(content)
            id_column = "message_id" if table == "message_parts" else "id"
            with self.engine.begin() as conn:
                result = conn.execute(
                    text(
                        f"""UPDATE {table} SET embedding=CAST(:embedding AS vector)
                        WHERE {id_column}=:document_id"""
                    ),
                    {"embedding": json.dumps(embedding), "document_id": document_id},
                )
            return bool(result.rowcount)
        except Exception:
            return False

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
        requested = kinds or {"thread", "message", "memory", "artifact", "run"}
        allowed = sorted((requested - {"report"}) | ({"artifact"} if "report" in requested else set()))
        with self.engine.begin() as conn:
            rows = conn.execute(
                text(
                    """SELECT id, kind, title, snippet, thread_id, run_id, created_at, metadata,
                       ts_rank(search_vector, websearch_to_tsquery('simple', :query)) AS score
                    FROM search_documents
                    WHERE workspace_id=:workspace_id AND owner_user_id=:owner_user_id
                      AND kind = ANY(:kinds)
                      AND search_vector @@ websearch_to_tsquery('simple', :query)
                    ORDER BY score DESC, created_at DESC LIMIT :candidate_limit"""
                ),
                {
                    "query": query, "workspace_id": workspace_id,
                    "owner_user_id": owner_user_id, "kinds": allowed,
                    "candidate_limit": max(1, limit) * 4,
                },
            ).mappings().all()
        lexical = []
        for row in rows:
            item = SearchResult.model_validate(row)
            is_report = (
                item.kind == "artifact"
                and item.metadata.get("artifact_type") == "report"
            )
            if is_report:
                item = item.model_copy(update={"kind": "report"})
            if item.kind in requested:
                lexical.append(item)
        recency = sorted(lexical, key=lambda item: item.created_at, reverse=True)
        if vector is None and self.query_embedder:
            try:
                vector = self.query_embedder(query)
            except Exception:
                vector = None
        vectors: list[SearchResult] = []
        if vector:
            try:
                with self.engine.begin() as conn:
                    vector_rows = conn.execute(
                        text(
                            """SELECT sd.id, sd.kind, sd.title, sd.snippet,
                               sd.thread_id, sd.run_id, sd.created_at, sd.metadata,
                               1 - (se.embedding <=> CAST(:embedding AS vector)) AS score
                            FROM search_documents sd
                            JOIN search_document_embeddings se
                              ON se.id=sd.id AND se.kind=sd.kind
                            WHERE sd.workspace_id=:workspace_id
                              AND sd.owner_user_id=:owner_user_id
                              AND sd.kind = ANY(:kinds) AND se.embedding IS NOT NULL
                            ORDER BY se.embedding <=> CAST(:embedding AS vector)
                            LIMIT :candidate_limit"""
                        ),
                        {
                            "embedding": json.dumps(vector),
                            "workspace_id": workspace_id,
                            "owner_user_id": owner_user_id,
                            "kinds": allowed,
                            "candidate_limit": max(1, limit) * 3,
                        },
                    ).mappings().all()
                for row in vector_rows:
                    item = SearchResult.model_validate(row)
                    is_report = (
                        item.kind == "artifact"
                        and item.metadata.get("artifact_type") == "report"
                    )
                    if is_report:
                        item = item.model_copy(update={"kind": "report"})
                    if item.kind in requested:
                        vectors.append(item)
            except Exception:
                vectors = []
        if not vectors and self.vector_hook:
            vectors = list(self.vector_hook(query, workspace_id, owner_user_id, limit * 3))
        with operation_span(
            "retrieval",
            "hybrid_search",
            {"backend": "postgres", "lexical": len(lexical), "vector": len(vectors)},
        ):
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
            self._events[approval.id] = [
                ApprovalDecisionEvent(
                    approval_id=approval.id,
                    to_status=ApprovalStatus.REQUESTED,
                    actor_principal_id=approval.owner_user_id,
                    metadata={"audit_kind": "REQUESTED"},
                    created_at=approval.created_at,
                )
            ]
            return approval

    def get(self, approval_id: str, workspace_id: str, owner_user_id: str) -> ApprovalRequest | None:
        item = self._items.get(approval_id)
        if not item or item.workspace_id != workspace_id or item.owner_user_id != owner_user_id:
            return None
        return item

    def get_for_workspace(
        self, approval_id: str, workspace_id: str
    ) -> ApprovalRequest | None:
        item = self._items.get(approval_id)
        return item if item and item.workspace_id == workspace_id else None

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

    def get_rollback(self, approval_id: str) -> RollbackRecord | None:
        return next(
            (item for item in self._rollbacks.values() if item.approval_id == approval_id),
            None,
        )

    def list_due(self, now: datetime) -> list[ApprovalRequest]:
        return [
            item
            for item in self._items.values()
            if item.status in {ApprovalStatus.REQUESTED, ApprovalStatus.APPROVED}
            and item.expires_at is not None
            and item.expires_at <= now
        ]


class PostgresApprovalRepository:
    def __init__(self, database_url: str | Engine) -> None:
        self.engine = (
            create_engine(database_url) if isinstance(database_url, str) else database_url
        )

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
            created = ApprovalRequest.model_validate(row)
            conn.execute(
                text(
                    """INSERT INTO approval_decision_events
                    (id,approval_id,from_status,to_status,actor_principal_id,reason,metadata,created_at)
                    VALUES (:id,:approval_id,NULL,'REQUESTED',:actor,NULL,
                            CAST(:metadata AS JSONB),:created_at)
                    ON CONFLICT (id) DO NOTHING"""
                ),
                {
                    "id": f"{created.id}:requested",
                    "approval_id": created.id,
                    "actor": created.owner_user_id,
                    "metadata": '{"audit_kind":"REQUESTED"}',
                    "created_at": created.created_at,
                },
            )
        return created

    def get(self, approval_id: str, workspace_id: str, owner_user_id: str) -> ApprovalRequest | None:
        with self.engine.begin() as conn:
            row = conn.execute(text(
                """SELECT * FROM approval_requests WHERE id=:id AND workspace_id=:workspace_id
                   AND owner_user_id=:owner_user_id"""
            ), {"id": approval_id, "workspace_id": workspace_id,
                "owner_user_id": owner_user_id}).mappings().first()
        return ApprovalRequest.model_validate(row) if row else None

    def get_for_workspace(
        self, approval_id: str, workspace_id: str
    ) -> ApprovalRequest | None:
        with self.engine.begin() as conn:
            row = conn.execute(
                text(
                    """SELECT * FROM approval_requests
                    WHERE id=:id AND workspace_id=:workspace_id"""
                ),
                {"id": approval_id, "workspace_id": workspace_id},
            ).mappings().first()
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
                   WHERE id=:id AND status=:expected
                   AND workspace_id=:workspace_id AND owner_user_id=:owner_user_id
                   RETURNING *"""
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
                 CAST(:outcome AS JSONB),:created_at)
                 ON CONFLICT (approval_id) DO NOTHING"""
            ), {**record.model_dump(), "action": json.dumps(record.action),
                "outcome": json.dumps(record.outcome)})
        return record

    def get_rollback(self, approval_id: str) -> RollbackRecord | None:
        with self.engine.begin() as conn:
            row = conn.execute(
                text(
                    """SELECT * FROM approval_rollbacks
                    WHERE approval_id=:approval_id ORDER BY created_at DESC LIMIT 1"""
                ),
                {"approval_id": approval_id},
            ).mappings().first()
        return RollbackRecord.model_validate(row) if row else None

    def list_due(self, now: datetime) -> list[ApprovalRequest]:
        with self.engine.begin() as conn:
            rows = conn.execute(
                text(
                    """SELECT * FROM approval_requests
                    WHERE status IN ('REQUESTED','APPROVED')
                      AND expires_at IS NOT NULL AND expires_at <= :now
                    ORDER BY expires_at"""
                ),
                {"now": now},
            ).mappings().all()
        return [ApprovalRequest.model_validate(row) for row in rows]


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
    if not principal.can_access_workspace(approval.workspace_id):
        raise PermissionError("approval belongs to another workspace")
    if target is ApprovalStatus.CANCELLED and (
        principal.user_id != approval.owner_user_id and not principal.is_admin
    ):
        raise PermissionError("only the requester can cancel this approval")
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
    with operation_span(
        "persistence",
        "approval_transition",
        {
            "approval_id": approval.id,
            "from_status": approval.status.value,
            "to_status": target.value,
        },
    ):
        persisted = repository.transition(approval.id, approval.status.value, updated, event)
    if persisted is None:
        raise RuntimeError("approval changed concurrently")
    return persisted


class ActionExecutor(Protocol):
    """Pluggable side-effect adapter used only after an approval is granted."""

    def preview(self, action: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def execute(
        self, action: Mapping[str, Any], *, idempotency_key: str
    ) -> Mapping[str, Any]: ...

    def rollback(
        self, action: Mapping[str, Any], outcome: Mapping[str, Any]
    ) -> Mapping[str, Any]: ...


CheckpointResumeHook = Callable[[str, Mapping[str, Any]], None]


class ActionExecutionService:
    def __init__(
        self,
        repository: Any,
        executors: Mapping[str, ActionExecutor],
        *,
        checkpoint_resume: CheckpointResumeHook | None = None,
    ) -> None:
        self.repository = repository
        self.executors = dict(executors)
        self.checkpoint_resume = checkpoint_resume
        self._outcomes: dict[str, dict[str, Any]] = {}
        self._executed: dict[str, ApprovalRequest] = {}
        self._lock = RLock()

    def dry_run(self, approval: ApprovalRequest) -> dict[str, Any]:
        executor = self.executors.get(approval.action_type)
        if executor is None:
            raise LookupError(f"no executor for action type {approval.action_type!r}")
        with operation_span("task", "action_preview", {"action_type": approval.action_type}):
            return dict(executor.preview(approval.action_preview))

    def execute(
        self,
        approval: ApprovalRequest,
        principal: Principal,
        *,
        allow_write_actions: bool,
    ) -> tuple[ApprovalRequest, dict[str, Any]]:
        if approval.dry_run:
            return approval, self.dry_run(approval)
        if approval.status is ApprovalStatus.EXECUTED:
            return approval, {
                "idempotent": True,
                "outcome_unavailable": approval.id not in self._outcomes,
                **self._outcomes.get(approval.id, {}),
            }
        executor = self.executors.get(approval.action_type)
        if executor is None:
            raise LookupError(f"no executor for action type {approval.action_type!r}")
        with self._lock:
            if approval.id in self._outcomes:
                return self._executed[approval.id], dict(self._outcomes[approval.id])
            with operation_span(
                "task",
                "action_execute",
                {"action_type": approval.action_type, "approval_id": approval.id},
            ):
                outcome = dict(
                    executor.execute(
                        approval.action_preview,
                        idempotency_key=approval.idempotency_key,
                    )
                )
            executed = transition_approval(
                self.repository,
                approval,
                ApprovalStatus.EXECUTED,
                principal,
                allow_write_actions=allow_write_actions,
            )
            self._outcomes[approval.id] = outcome
            self._executed[approval.id] = executed
            if executed.checkpoint_resume_token and self.checkpoint_resume:
                self.checkpoint_resume(executed.checkpoint_resume_token, outcome)
            return executed, dict(outcome)

    def rollback(
        self,
        approval: ApprovalRequest,
        principal: Principal,
    ) -> tuple[ApprovalRequest, RollbackRecord]:
        executor = self.executors.get(approval.action_type)
        if executor is None:
            raise LookupError(f"no executor for action type {approval.action_type!r}")
        existing = self.repository.get_rollback(approval.id)
        if existing:
            return approval, existing
        original_outcome = self._outcomes.get(approval.id, {})
        try:
            compensation = dict(executor.rollback(approval.action_preview, original_outcome))
            rollback_outcome: dict[str, Any] = {
                "compensated": True,
                "result": compensation,
            }
        except Exception as exc:
            rollback_outcome = {
                "compensated": False,
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        record = self.repository.add_rollback(
            RollbackRecord(
                approval_id=approval.id,
                actor_principal_id=principal.principal_id,
                action={
                    "action_type": approval.action_type,
                    "input": approval.action_preview,
                    "original_outcome": original_outcome,
                },
                outcome=rollback_outcome,
            )
        )
        if not rollback_outcome["compensated"]:
            return approval, record
        rolled_back = transition_approval(
            self.repository,
            approval,
            ApprovalStatus.ROLLED_BACK,
            principal,
        )
        return rolled_back, record


class ApprovalExpiryService:
    """Scheduler-friendly expiry sweep; callers choose the timer mechanism."""

    def __init__(self, repository: Any) -> None:
        self.repository = repository

    def sweep(self, *, now: datetime | None = None) -> int:
        moment = now or datetime.now(UTC)
        expired = 0
        for approval in self.repository.list_due(moment):
            system = Principal(
                principal_id="approval-expiry",
                workspace_id=approval.workspace_id,
                user_id="approval-expiry",
                authenticated=True,
                roles={"internal"},
            )
            try:
                transition_approval(
                    self.repository,
                    approval,
                    ApprovalStatus.EXPIRED,
                    system,
                    reason="scheduled expiry",
                    now=moment,
                )
                expired += 1
            except RuntimeError:
                continue
        return expired
