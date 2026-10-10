"""Qdrant-backed verified-examples backend (M1.3).

Reuses the client, collection, embedding, and point-id patterns from
``toolsets/catalogue_index.py`` so the examples collection is built and queried
the same way as the catalogue index. One embedding model
(``settings.qdrant_embedding_model``), deliberately separate from the
conversation-search model.

The Qdrant client is synchronous; the backend keeps the async ``ExampleBackend``
signature so the store can treat it uniformly (the pipeline wraps the call with
``asyncio.to_thread`` when it matters).
"""

from __future__ import annotations

from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from seleric_swarm.toolsets.catalogue_index import (
    build_client,
    build_embedder,
    ensure_collection,
    point_id,
)
from seleric_swarm.understand.examples import VerifiedExample

DEFAULT_COLLECTION = "seleric_examples"


class QdrantExampleBackend:
    """Store and retrieve verified examples in a Qdrant collection."""

    def __init__(
        self,
        client: QdrantClient,
        embedder: Any,
        collection: str,
        *,
        vector_size: int,
    ) -> None:
        self._client = client
        self._embedder = embedder
        self._collection = collection
        self._vector_size = vector_size

    @classmethod
    def from_settings(
        cls, settings: Any, *, collection: str = DEFAULT_COLLECTION
    ) -> QdrantExampleBackend:
        client = build_client(settings)
        embedder = build_embedder(settings)
        if embedder is None:
            raise RuntimeError(
                "examples embedder not configured: qdrant_embedding_model, "
                "azure_openai_endpoint, and azure_openai_api_key are all required"
            )
        vector_size = len(embedder("examples"))
        ensure_collection(client, collection, vector_size)
        return cls(client, embedder, collection, vector_size=vector_size)

    async def add(self, example: VerifiedExample) -> None:
        self._client.upsert(
            collection_name=self._collection,
            points=[
                qmodels.PointStruct(
                    id=point_id("example", example.id),
                    vector=self._embedder(example.question),
                    payload={
                        "id": example.id,
                        "question": example.question,
                        "spec": example.spec,
                        "template_id": example.template_id,
                        "verified_by": example.verified_by,
                        "verified_at": example.verified_at,
                        "catalogue_version": example.catalogue_version,
                    },
                )
            ],
        )

    async def search(
        self, question: str, k: int
    ) -> list[tuple[VerifiedExample, float]]:
        response = self._client.query_points(
            collection_name=self._collection, query=self._embedder(question), limit=k
        )
        results: list[tuple[VerifiedExample, float]] = []
        for hit in response.points:
            example = VerifiedExample.model_validate(dict(hit.payload or {}))
            results.append((example, hit.score))
        return results
