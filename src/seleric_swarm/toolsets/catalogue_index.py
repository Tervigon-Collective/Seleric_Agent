"""Catalogue LTM — local Qdrant-backed semantic search over the live catalogue.

Replaces the remote ``catalogue_search_metrics``/``catalogue_resolve_term``
round trips in ``toolsets/semantic.py::search_semantics`` with a local vector
search kept in sync via ``scripts/sync_catalogue_to_qdrant.py``. This is a
candidate-shortlist layer only: ``query_metrics``/``drilldown``/
``get_metric_definition`` in ``semantic.py`` still go through Cube via
seleric-mcp unchanged (rule 1 — Cube stays the sole authority for resolving
and validating a metric; an id that no longer exists there still surfaces as
a live ``INSUFFICIENT_EVIDENCE`` error, not a fabricated match from here).

One embedding model (``settings.qdrant_embedding_model``, default
``text-embedding-3-small``) for this collection, deliberately separate from
``settings.search_embedding_model`` (conversation/memory search) — different
vector space, different purpose. The client-construction logic is reused
from ``conversations/phase7.py::build_query_embedder`` via its
``model_override`` parameter rather than duplicated here.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from seleric_swarm.conversations.phase7 import QueryEmbeddingHook, build_query_embedder

# Fixed namespace so the same catalogue id always maps to the same Qdrant
# point id across runs (Qdrant point ids must be an unsigned int or a UUID;
# catalogue ids are arbitrary strings like "net_sales").
_NAMESPACE = uuid.UUID("f2a1e774-6b1d-4b8a-9f1a-9a6b0c1d2e3f")

STALE_AFTER_HOURS = 24.0


def point_id(kind: str, catalogue_id: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, f"{kind}:{catalogue_id}"))


def compose_embedding_text(name: str, description: str = "", aliases: list[str] | None = None) -> str:
    """The one formula used at both sync time and query time.

    A mismatch here (e.g. the sync script embedding a different string shape
    than a query-time caller) would silently degrade match quality, so this
    lives in exactly one place. Blank fields are omitted, never replaced
    with placeholder text.
    """
    name = (name or "").strip()
    description = (description or "").strip()
    clean_aliases = [a.strip() for a in (aliases or []) if a and a.strip()]
    text = ". ".join(part for part in (name, description) if part)
    if clean_aliases:
        alias_clause = f"Also known as: {', '.join(clean_aliases)}."
        text = f"{text}. {alias_clause}" if text else alias_clause
    return text or name


def build_embedder(settings: Any) -> QueryEmbeddingHook:
    embedder = build_query_embedder(settings, model_override=settings.qdrant_embedding_model)
    if embedder is None:
        raise RuntimeError(
            "catalogue index embedder not configured: qdrant_embedding_model, "
            "azure_openai_endpoint, and azure_openai_api_key are all required"
        )
    return embedder


def build_client(settings: Any) -> QdrantClient:
    if not settings.qdrant_url:
        raise RuntimeError("qdrant_url is not configured")
    return QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key or None)


def ensure_collection(client: QdrantClient, collection: str, vector_size: int) -> None:
    if not client.collection_exists(collection):
        client.create_collection(
            collection_name=collection,
            vectors_config=qmodels.VectorParams(size=vector_size, distance=qmodels.Distance.COSINE),
        )
    # A managed Qdrant instance rejects filtering (search_catalogue's/list_catalogue's
    # `kind` filter) on an un-indexed keyword field with a 400 -- create it every call,
    # idempotently (Qdrant no-ops re-creating an existing index).
    client.create_payload_index(
        collection_name=collection, field_name="kind", field_schema=qmodels.PayloadSchemaType.KEYWORD
    )


def upsert_entry(
    client: QdrantClient,
    collection: str,
    *,
    kind: str,
    catalogue_id: str,
    name: str,
    vector: list[float],
    description: str = "",
    aliases: list[str] | None = None,
    unit: str | None = None,
    full_definition: dict[str, Any] | None = None,
) -> None:
    payload = {
        "kind": kind,
        "id": catalogue_id,
        "name": name,
        "description": description,
        "aliases": aliases or [],
        "unit": unit,
        "full_definition": full_definition or {},
        "synced_at": datetime.now(UTC).isoformat(),
    }
    client.upsert(
        collection_name=collection,
        points=[qmodels.PointStruct(id=point_id(kind, catalogue_id), vector=vector, payload=payload)],
    )


def search_catalogue(
    client: QdrantClient,
    embedder: QueryEmbeddingHook,
    collection: str,
    query: str,
    *,
    top_k: int = 5,
    kind: str | None = None,
    stale_after_hours: float = STALE_AFTER_HOURS,
) -> list[dict[str, Any]]:
    """Local semantic search over the synced catalogue.

    Returns matches shaped like the catalogue payloads ``search_semantics``
    already reads today, each carrying a ``stale`` flag when ``synced_at`` is
    older than ``stale_after_hours`` — the safety net while sync is a manual
    script, not a live guarantee.
    """
    vector = embedder(query)
    query_filter = None
    if kind:
        query_filter = qmodels.Filter(
            must=[qmodels.FieldCondition(key="kind", match=qmodels.MatchValue(value=kind))]
        )
    response = client.query_points(
        collection_name=collection, query=vector, limit=top_k, query_filter=query_filter
    )
    now = datetime.now(UTC)
    matches: list[dict[str, Any]] = []
    for hit in response.points:
        payload = dict(hit.payload or {})
        stale = True
        synced_at_raw = payload.get("synced_at")
        if synced_at_raw:
            try:
                synced_at = datetime.fromisoformat(synced_at_raw)
                stale = (now - synced_at).total_seconds() > stale_after_hours * 3600
            except ValueError:
                stale = True
        payload["score"] = hit.score
        payload["stale"] = stale
        matches.append(payload)
    return matches


def list_catalogue(
    client: QdrantClient, collection: str, *, kind: str | None = None, page_size: int = 256
) -> list[dict[str, Any]]:
    """Enumerate every stored entry, optionally filtered by ``kind``.

    No embedding/similarity involved (Qdrant ``scroll``, not ``search``) — used
    where a caller wants the *whole* cached catalogue (e.g.
    ``CatalogueBootstrap``'s full-list warmup), not a text match.
    """
    query_filter = None
    if kind:
        query_filter = qmodels.Filter(
            must=[qmodels.FieldCondition(key="kind", match=qmodels.MatchValue(value=kind))]
        )
    payloads: list[dict[str, Any]] = []
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=collection,
            scroll_filter=query_filter,
            limit=page_size,
            offset=offset,
            with_payload=True,
        )
        payloads.extend(dict(point.payload or {}) for point in points)
        if offset is None:
            break
    return payloads


_client: QdrantClient | None = None
_embedder: QueryEmbeddingHook | None = None


def _get_client() -> QdrantClient:
    global _client
    if _client is None:
        from seleric_swarm.config.settings import get_settings

        _client = build_client(get_settings())
    return _client


def search(query: str, *, top_k: int = 5, kind: str | None = None) -> list[dict[str, Any]]:
    """Process-level entry point used by ``toolsets/semantic.py``.

    Lazily builds and caches the Qdrant client + embedder for this process
    (one connection, reused across calls) from the global settings.
    """
    global _embedder
    from seleric_swarm.config.settings import get_settings

    settings = get_settings()
    if _embedder is None:
        _embedder = build_embedder(settings)
    return search_catalogue(_get_client(), _embedder, settings.qdrant_collection, query, top_k=top_k, kind=kind)


def list_all(*, kind: str | None = None) -> list[dict[str, Any]]:
    """Process-level entry point for ``list_catalogue`` — used by
    ``CatalogueBootstrap``'s local-index warmup fallback."""
    from seleric_swarm.config.settings import get_settings

    settings = get_settings()
    return list_catalogue(_get_client(), settings.qdrant_collection, kind=kind)
