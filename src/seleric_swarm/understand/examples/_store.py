"""Verified examples store (M1.3).

Retrieves verified ``(question -> spec -> template_id)`` examples as understand
exemplars. Every stored spec is re-validated against the current catalogue read
model at load: an example whose metric or dimension ids no longer exist is
skipped and reported (the semantic pickup rule), so a renamed catalogue id never
reaches a prompt as if it were still valid.

The store is pure domain logic (L2): it depends on an ``ExampleBackend`` port and
a ``SemanticPort`` (for the read model), both injected. The Qdrant backend lives in
``adapters/examples_qdrant.py``; the in-memory backend is for tests and local dev.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from seleric_swarm.semantic import SemanticReadModel

__all__ = ["ExampleStore", "VerifiedExample"]


class VerifiedExample(BaseModel):
    """One verified question -> spec -> template mapping.

    ``spec`` is the QuestionSpec JSON (partial or full). It is re-validated
    against the read model on every retrieval, so a stale example is dropped the
    moment the catalogue moves.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = 1
    id: str
    question: str
    spec: dict[str, Any]
    template_id: str
    verified_by: str
    verified_at: str
    catalogue_version: str


def spec_metric_ids(spec: dict[str, Any]) -> tuple[str, ...]:
    """Metric ids a spec depends on (measures + ranking)."""
    ids: list[str] = []
    for measure in spec.get("measures") or []:
        if isinstance(measure, dict) and measure.get("metric_id"):
            ids.append(str(measure["metric_id"]))
    ranking = spec.get("ranking")
    if isinstance(ranking, dict) and ranking.get("by_metric_id"):
        ids.append(str(ranking["by_metric_id"]))
    return tuple(ids)


def spec_dimension_ids(spec: dict[str, Any]) -> tuple[str, ...]:
    """Dimension ids a spec depends on (filters, entities, breakdowns)."""
    ids: list[str] = []
    for group in ("filters", "entities"):
        for item in spec.get(group) or []:
            if isinstance(item, dict) and item.get("dimension"):
                ids.append(str(item["dimension"]))
    for breakdown in spec.get("breakdowns") or []:
        if breakdown:
            ids.append(str(breakdown))
    return tuple(ids)


def spec_is_current(spec: dict[str, Any], read_model: SemanticReadModel) -> bool:
    """True when every metric and dimension id in the spec still exists."""
    for metric_id in spec_metric_ids(spec):
        if not read_model.has_metric(metric_id):
            return False
    known_dimensions = read_model.dimension_ids()
    for dimension in spec_dimension_ids(spec):
        if dimension not in known_dimensions:
            return False
    return True


def check_no_collision(
    case_ids: set[str] | frozenset[str],
    example_ids: set[str] | frozenset[str],
) -> None:
    """Raise if any eval case id is also a verified-example id.

    Eval cases and verified examples must stay separate: a case the harness
    scores must not silently become a trusted exemplar.
    """
    overlap = set(case_ids) & set(example_ids)
    if overlap:
        raise ValueError(
            "eval case ids also present in the verified examples store: "
            + ", ".join(sorted(overlap))
        )


class ExampleBackend(Protocol):
    """Storage port for verified examples. Implemented by the Qdrant adapter and
    by an in-memory backend for tests."""

    async def add(self, example: VerifiedExample) -> None: ...

    async def search(self, question: str, k: int) -> list[tuple[VerifiedExample, float]]: ...


class InMemoryExampleBackend:
    """Test/dev backend: token-overlap similarity, no vector store."""

    def __init__(self) -> None:
        self._examples: list[VerifiedExample] = []

    async def add(self, example: VerifiedExample) -> None:
        self._examples.append(example)

    async def search(self, question: str, k: int) -> list[tuple[VerifiedExample, float]]:
        query_tokens = _tokens(question)
        scored: list[tuple[float, int, VerifiedExample]] = []
        for index, example in enumerate(self._examples):
            scored.append((_overlap(query_tokens, _tokens(example.question)), index, example))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [(example, score) for score, _, example in scored[:k]]


def _tokens(text: str) -> set[str]:
    return {token for token in text.lower().split() if token}


def _overlap(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


class ExampleStore:
    """Retrieve verified examples as understand exemplars, re-validated against
    the current catalogue so a renamed/removed id never reaches the prompt.

    ``on_stale`` is called with each example skipped because its ids no longer
    exist, so the caller can report the semantic pickup.
    """

    def __init__(
        self,
        backend: ExampleBackend,
        *,
        port: Any | None = None,
        on_stale: Callable[[VerifiedExample], None] | None = None,
    ) -> None:
        self._backend = backend
        self._port = port
        self._on_stale = on_stale

    async def add(self, example: VerifiedExample) -> None:
        await self._backend.add(example)

    async def similar(
        self,
        question: str,
        k: int = 3,
        *,
        read_model: SemanticReadModel | None = None,
    ) -> list[VerifiedExample]:
        """Up to ``k`` verified examples nearest ``question``, stale ones skipped."""
        if read_model is None and self._port is not None:
            read_model = self._port.read_model()
        candidates = await self._backend.search(question, k * 2)
        results: list[VerifiedExample] = []
        for example, _score in candidates:
            if len(results) >= k:
                break
            if read_model is not None and not spec_is_current(example.spec, read_model):
                if self._on_stale is not None:
                    self._on_stale(example)
                continue
            results.append(example)
        return results
