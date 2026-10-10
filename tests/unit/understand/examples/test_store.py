"""Unit tests for the verified examples store (M1.3).

Retrieval uses the in-memory backend; re-validation uses a FakeSemanticPort (or
an injected read model) so the semantic-pickup skip is exercised without Qdrant.
"""

from __future__ import annotations

from typing import Any

import pytest

from seleric_swarm.semantic import SemanticReadModel
from seleric_swarm.semantic._fake import FakeSemanticPort
from seleric_swarm.understand.examples import (
    ExampleStore,
    InMemoryExampleBackend,
    VerifiedExample,
)
from seleric_swarm.understand.examples._store import (
    spec_dimension_ids,
    spec_is_current,
    spec_metric_ids,
)

_CURRENT = {
    "catalogue_version": "v1",
    "metrics": [
        {
            "id": "metric.net_sales",
            "label": "net sales",
            "unit": "USD",
            "aggregation": "sum",
            "supported_dimensions": ["dim.date", "dim.product"],
        },
        {
            "id": "metric.gross_sales",
            "label": "gross sales",
            "unit": "USD",
            "aggregation": "sum",
            "supported_dimensions": ["dim.date"],
        },
    ],
    "dimensions": [
        {"id": "dim.date", "is_time": True},
        {"id": "dim.product"},
    ],
}

_MUTATED = {
    "catalogue_version": "v2",
    "metrics": [
        {
            "id": "metric.gross_sales",
            "label": "gross sales",
            "unit": "USD",
            "aggregation": "sum",
            "supported_dimensions": ["dim.date"],
        },
    ],
    "dimensions": [
        {"id": "dim.date", "is_time": True},
    ],
}


def _example(example_id: str, question: str, metric_id: str) -> VerifiedExample:
    return VerifiedExample(
        id=example_id,
        question=question,
        spec={"kind": "analysis", "measures": [{"metric_id": metric_id}]},
        template_id="lookup",
        verified_by="test",
        verified_at="2026-10-11T00:00:00+00:00",
        catalogue_version="v1",
    )


def _store(port: Any | None = None, on_stale: Any = None) -> ExampleStore:
    return ExampleStore(InMemoryExampleBackend(), port=port, on_stale=on_stale)


# --- re-validation helper ---------------------------------------------------


def test_spec_metric_ids_collects_measures_and_ranking() -> None:
    spec = {
        "measures": [{"metric_id": "metric.a"}, {"metric_id": "metric.b"}],
        "ranking": {"by_metric_id": "metric.c"},
    }
    assert set(spec_metric_ids(spec)) == {"metric.a", "metric.b", "metric.c"}


def test_spec_dimension_ids_collects_filters_entities_breakdowns() -> None:
    spec = {
        "filters": [{"dimension": "dim.date"}],
        "entities": [{"dimension": "dim.product"}],
        "breakdowns": ["dim.channel"],
    }
    assert set(spec_dimension_ids(spec)) == {"dim.date", "dim.product", "dim.channel"}


def test_spec_is_current_true_when_all_ids_exist() -> None:
    model = SemanticReadModel.from_payload(_CURRENT)
    spec = {
        "measures": [{"metric_id": "metric.net_sales"}],
        "breakdowns": ["dim.product"],
    }
    assert spec_is_current(spec, model) is True


def test_spec_is_current_false_when_metric_removed() -> None:
    model = SemanticReadModel.from_payload(_MUTATED)
    spec = {"measures": [{"metric_id": "metric.net_sales"}]}
    assert spec_is_current(spec, model) is False


def test_spec_is_current_false_when_dimension_removed() -> None:
    model = SemanticReadModel.from_payload(_MUTATED)
    spec = {"measures": [{"metric_id": "metric.gross_sales"}], "breakdowns": ["dim.product"]}
    assert spec_is_current(spec, model) is False


# --- retrieval ---------------------------------------------------------------


async def test_similar_returns_nearest_example() -> None:
    store = _store()
    await store.add(_example("ex1", "net sales yesterday", "metric.net_sales"))
    await store.add(_example("ex2", "net sales last week", "metric.gross_sales"))
    await store.add(_example("ex3", "gross sales last week", "metric.gross_sales"))

    results = await store.similar("net sales last week", k=1)
    assert len(results) == 1
    assert results[0].id == "ex2"


async def test_similar_without_port_skips_no_validation() -> None:
    store = _store()  # no port -> no re-validation
    await store.add(_example("ex1", "net sales yesterday", "metric.net_sales"))
    results = await store.similar("net sales", k=3)
    assert [r.id for r in results] == ["ex1"]


async def test_stale_example_skipped_after_catalogue_mutation() -> None:
    current = SemanticReadModel.from_payload(_CURRENT)
    mutated = SemanticReadModel.from_payload(_MUTATED)
    store = _store()
    await store.add(_example("keep", "net sales yesterday", "metric.gross_sales"))
    await store.add(_example("drop", "net sales last week", "metric.net_sales"))

    # Current catalogue: both examples are valid.
    assert {r.id for r in await store.similar("net sales", k=3, read_model=current)} == {
        "keep",
        "drop",
    }
    # Mutated catalogue: metric.net_sales is gone -> "drop" is skipped.
    assert [r.id for r in await store.similar("net sales", k=3, read_model=mutated)] == ["keep"]


async def test_on_stale_called_for_skipped_example() -> None:
    mutated = SemanticReadModel.from_payload(_MUTATED)
    stale: list[VerifiedExample] = []
    store = _store(on_stale=stale.append)
    await store.add(_example("drop", "net sales last week", "metric.net_sales"))

    results = await store.similar("net sales", k=3, read_model=mutated)
    assert results == []
    assert [e.id for e in stale] == ["drop"]


async def test_similar_uses_port_read_model_by_default() -> None:
    port = FakeSemanticPort(_MUTATED)
    store = _store(port=port)
    await store.add(_example("drop", "net sales last week", "metric.net_sales"))
    assert await store.similar("net sales", k=3) == []


@pytest.mark.asyncio
async def test_add_persists_to_backend() -> None:
    backend = InMemoryExampleBackend()
    store = ExampleStore(backend)
    await store.add(_example("ex1", "net sales yesterday", "metric.net_sales"))
    results = await store.similar("net sales", k=1)
    assert len(results) == 1
