"""Scoped search and reciprocal-rank fusion."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from typing import Any, Literal

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from seleric_swarm.conversations.contracts import SearchResult
from seleric_swarm.observability.tracing import operation_span

VectorSearchHook = Callable[[str, str, str, int], Iterable[SearchResult]]
QueryEmbeddingHook = Callable[[str], list[float]]


def build_query_embedder(settings: Any, *, model_override: str | None = None) -> QueryEmbeddingHook | None:
    """Build a synchronous embedding hook only when fully configured.

    ``model_override`` lets a second embedding consumer (e.g. the catalogue
    index) reuse this same Azure/OpenAI client-construction logic with its
    own model id instead of ``settings.search_embedding_model`` — one client
    factory, multiple embedding models/collections.
    """
    model = (model_override or str(getattr(settings, "search_embedding_model", "") or "")).strip()
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
