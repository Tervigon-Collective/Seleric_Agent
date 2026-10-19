"""MCP adapter for SemanticPort. Capability names live only in this module."""

from __future__ import annotations

import logging
import time
from typing import Any, Protocol

from seleric_swarm.semantic._port import (
    CatalogueError,
    ConceptResolution,
    MetricDefinition,
    MetricsQueryArgs,
    QueryResponse,
    ValueCandidates,
    concept_from_payload,
    definition_from_payload,
    query_from_payload,
    values_from_payload,
)
from seleric_swarm.semantic._read_model import SemanticReadModel

log = logging.getLogger(__name__)

_BOOTSTRAP = "seleric.catalogue_bootstrap"
_LIST_METRICS = "seleric.catalogue_list_metrics"
_LIST_BRANDS = "seleric.catalogue_list_brands"
_RESOLVE_CONCEPT = "seleric.catalogue_resolve_concept"
_RESOLVE_VALUES = "seleric.catalogue_resolve_values"
_GET_METRICS = "seleric.catalogue_get_metrics"
_GET_METRIC = "seleric.catalogue_get_metric"
_SEARCH = "seleric.catalogue_search_metrics"
_RESOLVE_BRAND = "seleric.catalogue_resolve_brand"
_GET_ONTOLOGY = "seleric.catalogue_get_ontology"
_RELATED = "seleric.catalogue_related_metrics"
_LIST_DIMENSIONS = "seleric.catalogue_list_dimensions"
_METRICS_QUERY = "seleric.metrics_query"
_METRICS_DRILL = "seleric.metrics_drilldown"
_BATCH = 10
_PULL = {
    "bootstrap": _BOOTSTRAP,
    "metrics": _LIST_METRICS,
    "dimensions": _LIST_DIMENSIONS,
    "brands": _LIST_BRANDS,
    "ontology": _GET_ONTOLOGY,
    "metric": _GET_METRIC,
    "related": _RELATED,
}


class CatalogueCaller(Protocol):
    async def call(self, *, agent_id: str, capability: str, arguments: dict[str, Any]) -> Any: ...


class McpSemanticPort:
    """Live catalogue. A failed warm keeps the last good snapshot."""

    def __init__(
        self,
        mcp: CatalogueCaller,
        *,
        agent_id: str = "v3_agent",
        ttl_seconds: int = 900,
    ) -> None:
        self._mcp = mcp
        self._agent_id = agent_id
        self._ttl = ttl_seconds
        self._model: SemanticReadModel | None = None
        self._warmed_at: float | None = None
        self._stale = False

    def version(self) -> str:
        return self._model.version if self._model is not None else ""

    def with_read_model(self, model: SemanticReadModel) -> McpSemanticPort:
        """Use an already-loaded snapshot (the mission's frozen catalogue) instead of warming."""
        self._model = model
        return self

    def read_model(self) -> SemanticReadModel:
        return self._model if self._model is not None else SemanticReadModel()

    def note_version(self, version: str) -> None:
        """Mark the snapshot stale when any response carries a new catalogue version."""
        current = self.version()
        if version and current and version != current:
            self._stale = True

    async def ensure_fresh(self) -> int:
        """Rewarm when the version changed or the TTL elapsed. Version peek is ``catalogue_list_brands``."""
        if self._model is None or self._warmed_at is None:
            return await self.warm()
        if self._stale or (time.monotonic() - self._warmed_at) > self._ttl:
            return await self.warm()
        peeked = await self._call(_LIST_BRANDS, {})
        peeked_version = str(peeked.get("catalogue_version") or "")
        if peeked_version and peeked_version != self.version():
            return await self.warm()
        return len(self._model.metrics)

    async def warm(self) -> int:
        payload = await self._call(_BOOTSTRAP, {})
        if not _metric_rows(payload):
            payload = await self._call(_LIST_METRICS, {})
        if not _metric_rows(payload):
            if self._model is not None and self._model.metrics:
                log.warning("semantic port warm failed; serving the last good snapshot")
                return len(self._model.metrics)
            self._model = SemanticReadModel()
            self._warmed_at = time.monotonic()
            self._stale = False
            log.warning("semantic port warm failed; no snapshot to serve")
            return 0
        brands = await self._call(_LIST_BRANDS, {})
        payload = _merge_brands(payload, brands)
        self._model = SemanticReadModel.from_payload(payload)
        self._warmed_at = time.monotonic()
        self._stale = False
        return len(self._model.metrics)

    async def resolve_concept(
        self, text: str, axes: dict[str, str] | None = None
    ) -> ConceptResolution:
        arguments: dict[str, Any] = {"text": text}
        if axes:
            arguments["axes"] = dict(axes)
        payload = await self._call(_RESOLVE_CONCEPT, arguments)
        self.note_version(str(payload.get("catalogue_version") or ""))
        return concept_from_payload(payload)

    async def resolve_values(self, text: str, axes_only: bool = False) -> ValueCandidates:
        arguments: dict[str, Any] = {"text": text}
        if axes_only:
            arguments["axes_only"] = True
        payload = await self._call(_RESOLVE_VALUES, arguments)
        self.note_version(str(payload.get("catalogue_version") or ""))
        return values_from_payload(payload)

    def supports(self, kind: str) -> bool:
        """True when the gateway advertises this catalogue operation."""
        capabilities = getattr(self._mcp, "capabilities", ())
        return _PULL[kind] in capabilities

    async def pull(self, kind: str, *, agent_id: str | None = None) -> dict[str, Any]:
        """One catalogue read. Raises when the gateway call fails."""
        return await self._call_raising(_PULL[kind], {}, agent_id=agent_id)

    async def listing(self, kind: str) -> dict[str, Any]:
        """Catalogue read that fails open. The legacy warm uses this."""
        try:
            return await self.pull(kind)
        except Exception as exc:
            log.warning("semantic port %s failed (%s: %s)", kind, type(exc).__name__, exc)
            return {}

    async def search_metrics(self, query: str, *, agent_id: str | None = None) -> dict[str, Any]:
        return await self._call_raising(_SEARCH, {"query": query}, agent_id=agent_id)

    async def resolve_brand(self, text: str, *, agent_id: str | None = None) -> dict[str, Any]:
        return await self._call_raising(_RESOLVE_BRAND, {"text": text}, agent_id=agent_id)

    async def fetch_metric(self, metric_id: str, *, agent_id: str | None = None) -> dict[str, Any]:
        return await self._call_raising(_GET_METRIC, {"metric_id": metric_id}, agent_id=agent_id)

    async def fetch_metrics(self, metric_ids: list[str], *, agent_id: str | None = None) -> dict[str, Any]:
        return await self._call_raising(_GET_METRICS, {"metric_ids": list(metric_ids)}, agent_id=agent_id)

    async def resolve_concept_payload(
        self,
        text: str,
        axes: dict[str, str] | None = None,
        *,
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {"text": text}
        if axes is not None:
            arguments["axes"] = dict(axes)
        return await self._call_raising(_RESOLVE_CONCEPT, arguments, agent_id=agent_id)

    async def resolve_values_payload(
        self,
        text: str,
        *,
        axes_only: bool = False,
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {"text": text}
        if axes_only:
            arguments["axes_only"] = True
        return await self._call_raising(_RESOLVE_VALUES, arguments, agent_id=agent_id)

    async def ontology(
        self,
        *,
        agent_id: str,
        module: str | None = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {}
        if module is not None:
            arguments["module"] = module
        return await self._call_raising(_GET_ONTOLOGY, arguments, agent_id=agent_id)

    async def related_metrics(self, metric_id: str, *, agent_id: str) -> dict[str, Any]:
        return await self._call_raising(_RELATED, {"metric_id": metric_id}, agent_id=agent_id)

    async def metric_definitions(self, ids: list[str]) -> dict[str, MetricDefinition]:
        unique = list(dict.fromkeys(item for item in ids if item))
        found: dict[str, MetricDefinition] = {}
        for start in range(0, len(unique), _BATCH):
            batch = unique[start : start + _BATCH]
            payload = await self._call(_GET_METRICS, {"metric_ids": batch})
            self.note_version(str(payload.get("catalogue_version") or ""))
            metrics_raw = payload.get("metrics")
            metrics = metrics_raw if isinstance(metrics_raw, dict) else {}
            for key, value in metrics.items():
                if isinstance(value, dict):
                    row = dict(value)
                    row.setdefault("id", str(key))
                    definition = definition_from_payload(row)
                    if definition.id:
                        found[definition.id] = definition
        return found

    async def query(self, args: MetricsQueryArgs) -> QueryResponse:
        refusal = self._refuse_measures(args.measures)
        if refusal is not None:
            return refusal
        payload = await self._call(_METRICS_QUERY, args.as_query())
        self.note_version(str(payload.get("catalogue_version") or ""))
        return query_from_payload(payload)

    async def drill(self, args: MetricsQueryArgs) -> QueryResponse:
        measures = args.measures or ((args.metric_id,) if args.metric_id else ())
        refusal = self._refuse_measures(measures)
        if refusal is not None:
            return refusal
        payload = await self._call(_METRICS_DRILL, args.as_drill())
        self.note_version(str(payload.get("catalogue_version") or ""))
        return query_from_payload(payload)

    def _refuse_measures(self, measures: tuple[str, ...]) -> QueryResponse | None:
        model = self._model
        if model is None or not model.metrics:
            return QueryResponse(error=CatalogueError(
                code="CATALOGUE_UNAVAILABLE",
                message="The catalogue snapshot is empty.",
                retryable=True,
            ))
        missing = [metric_id for metric_id in measures if metric_id and not model.has_metric(metric_id)]
        if missing:
            return QueryResponse(error=CatalogueError(
                code="METRIC_NOT_FOUND",
                message="Unknown metric: " + ", ".join(missing),
            ))
        return None

    async def _call(self, capability: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            return await self._call_raising(capability, arguments)
        except Exception as exc:
            log.warning("semantic port %s failed (%s: %s)", capability, type(exc).__name__, exc)
            return {}

    async def _call_raising(
        self,
        capability: str,
        arguments: dict[str, Any],
        *,
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        result = await self._mcp.call(
            agent_id=agent_id or self._agent_id,
            capability=capability,
            arguments=arguments,
        )
        return result if isinstance(result, dict) else {}


def _metric_rows(payload: dict[str, Any]) -> list[Any]:
    rows = payload.get("metrics") or payload.get("matches") or []
    return rows if isinstance(rows, list) else []


def _merge_brands(payload: dict[str, Any], brands: dict[str, Any]) -> dict[str, Any]:
    if not brands:
        return payload
    merged = dict(payload)
    if isinstance(brands.get("brands"), list):
        merged["brands"] = brands["brands"]
    if brands.get("default_brand_id") and not merged.get("default_brand_id"):
        merged["default_brand_id"] = brands["default_brand_id"]
    if brands.get("scope_dimension") and not merged.get("scope_dimensions"):
        merged["scope_dimensions"] = [brands["scope_dimension"]]
    if brands.get("catalogue_version") and not merged.get("catalogue_version"):
        merged["catalogue_version"] = brands["catalogue_version"]
    return merged
