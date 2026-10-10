"""In-memory SemanticPort for tests. No MCP."""

from __future__ import annotations

from typing import Any

from seleric_swarm.semantic._port import (
    CatalogueError,
    ConceptResolution,
    MetricDefinition,
    MetricsQueryArgs,
    QueryResponse,
    ValueCandidates,
    concept_from_payload,
    definition_from_payload,
    values_from_payload,
)
from seleric_swarm.semantic._read_model import SemanticReadModel


class FakeSemanticPort:
    """Serves one payload. ``query_rows`` maps a metric id to result rows."""

    def __init__(
        self,
        payload: dict[str, Any] | None = None,
        *,
        concepts: dict[str, dict[str, Any]] | None = None,
        values: dict[str, dict[str, Any]] | None = None,
        definitions: dict[str, dict[str, Any]] | None = None,
        query_rows: dict[str, list[dict[str, Any]]] | None = None,
    ) -> None:
        self._model = SemanticReadModel.from_payload(payload or {})
        self._concepts = dict(concepts or {})
        self._values = dict(values or {})
        self._definitions = dict(definitions or {})
        self._query_rows = {key: list(rows) for key, rows in (query_rows or {}).items()}

    def load(self, payload: dict[str, Any]) -> None:
        self._model = SemanticReadModel.from_payload(payload)

    def version(self) -> str:
        return self._model.version

    def read_model(self) -> SemanticReadModel:
        return self._model

    async def resolve_concept(
        self, text: str, axes: dict[str, str] | None = None
    ) -> ConceptResolution:
        del axes
        payload = self._concepts.get(text)
        if payload is None:
            return ConceptResolution(kind="unknown_concept", extra={"text": text})
        return concept_from_payload(payload)

    async def resolve_values(self, text: str, axes_only: bool = False) -> ValueCandidates:
        del axes_only
        payload = self._values.get(text)
        if payload is None:
            return ValueCandidates()
        return values_from_payload(payload)

    async def metric_definitions(self, ids: list[str]) -> dict[str, MetricDefinition]:
        found: dict[str, MetricDefinition] = {}
        for metric_id in ids:
            payload = self._definitions.get(metric_id)
            if payload is None:
                metric = next((item for item in self._model.metrics if item.id == metric_id), None)
                if metric is None:
                    continue
                payload = {
                    "id": metric.id,
                    "label": metric.label,
                    "unit": metric.unit,
                    "aggregation": metric.aggregation,
                    **metric.extra,
                }
            found[metric_id] = definition_from_payload(payload)
        return found

    async def query(self, args: MetricsQueryArgs) -> QueryResponse:
        return self._rows(args.measures)

    async def drill(self, args: MetricsQueryArgs) -> QueryResponse:
        measures = args.measures or ((args.metric_id,) if args.metric_id else ())
        return self._rows(measures)

    def _rows(self, measures: tuple[str, ...]) -> QueryResponse:
        if not self._model.metrics:
            return QueryResponse(error=CatalogueError(
                code="CATALOGUE_UNAVAILABLE",
                message="The catalogue snapshot is empty.",
                retryable=True,
            ))
        missing = [metric_id for metric_id in measures if not self._model.has_metric(metric_id)]
        if missing:
            return QueryResponse(error=CatalogueError(
                code="METRIC_NOT_FOUND",
                message="Unknown metric: " + ", ".join(missing),
            ))
        rows: list[dict[str, Any]] = []
        for metric_id in measures:
            rows.extend(self._query_rows.get(metric_id, []))
        return QueryResponse(rows=tuple(rows), catalogue_version=self._model.version)
