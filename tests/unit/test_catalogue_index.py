"""Unit tests for toolsets/catalogue_index.py — the local Qdrant catalogue
search that replaces the remote catalogue_search_metrics/catalogue_resolve_term
round trips in toolsets/semantic.py::search_semantics.

No live Qdrant/embedding calls: a fake client/embedder stand in, matching the
pattern already used for the fake MCP client in test_semantic_toolset.py.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from seleric_swarm.toolsets import catalogue_index


class _FakeHit:
    def __init__(self, payload: dict[str, Any], score: float = 0.9) -> None:
        self.payload = payload
        self.score = score


class _FakeQueryResponse:
    def __init__(self, points: list[_FakeHit]) -> None:
        self.points = points


class _FakeQdrantClient:
    """Matches qdrant-client's current ``query_points`` API (``search`` was
    removed in the client version this repo pins)."""

    def __init__(self, hits: list[_FakeHit]) -> None:
        self._hits = hits
        self.search_calls: list[dict[str, Any]] = []

    def query_points(self, **kwargs: Any) -> _FakeQueryResponse:
        self.search_calls.append(kwargs)
        return _FakeQueryResponse(self._hits)


def _embedder(query: str) -> list[float]:
    return [float(len(query))]


def test_compose_embedding_text_matches_at_sync_and_query_time():
    """The one shared formula: sync script and query-time search must build
    the identical string for the same inputs, or matches silently degrade."""
    sync_side = catalogue_index.compose_embedding_text(
        "Net Sales", "Total revenue after refunds", ["revenue", "gross sale"]
    )
    query_side = catalogue_index.compose_embedding_text(
        "Net Sales", "Total revenue after refunds", ["revenue", "gross sale"]
    )
    assert sync_side == query_side
    assert "Net Sales" in sync_side
    assert "Also known as: revenue, gross sale" in sync_side


def test_compose_embedding_text_omits_blank_fields_not_placeholders():
    text = catalogue_index.compose_embedding_text("Net Sales", "", [])
    assert text == "Net Sales"
    assert "Also known as" not in text


def test_search_catalogue_returns_matches_shaped_like_old_payload():
    fresh = datetime.now(UTC).isoformat()
    hits = [_FakeHit({"id": "net_sales", "kind": "metric", "synced_at": fresh})]
    client = _FakeQdrantClient(hits)
    matches = catalogue_index.search_catalogue(client, _embedder, "seleric_catalogue", "revenue")
    assert matches[0]["id"] == "net_sales"
    assert matches[0]["stale"] is False
    assert "score" in matches[0]


def test_search_catalogue_flags_stale_entries():
    old = (datetime.now(UTC) - timedelta(hours=48)).isoformat()
    hits = [_FakeHit({"id": "net_sales", "kind": "metric", "synced_at": old})]
    client = _FakeQdrantClient(hits)
    matches = catalogue_index.search_catalogue(client, _embedder, "seleric_catalogue", "revenue")
    assert matches[0]["stale"] is True


def test_search_catalogue_missing_synced_at_is_treated_as_stale():
    hits = [_FakeHit({"id": "net_sales", "kind": "metric"})]
    client = _FakeQdrantClient(hits)
    matches = catalogue_index.search_catalogue(client, _embedder, "seleric_catalogue", "revenue")
    assert matches[0]["stale"] is True


def test_search_catalogue_filters_by_kind():
    client = _FakeQdrantClient([])
    catalogue_index.search_catalogue(client, _embedder, "seleric_catalogue", "revenue", kind="metric")
    assert client.search_calls[0]["query_filter"] is not None
