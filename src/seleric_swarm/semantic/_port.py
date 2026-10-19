"""SemanticPort and the values that cross it."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from seleric_swarm.semantic._read_model import SemanticReadModel


class CatalogueError(BaseModel):
    """Typed refusal from the port. ``CATALOGUE_UNAVAILABLE`` and ``METRIC_NOT_FOUND``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    message: str
    retryable: bool = False


class ConceptResolution(BaseModel):
    """Tolerant view of ``catalogue_resolve_concept``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str
    metric_id: str | None = None
    concept: str | None = None
    axes: dict[str, str] = Field(default_factory=dict)
    reason: str | None = None
    suggestions: tuple[str, ...] = ()
    nearest_metrics: tuple[str, ...] = ()
    extra: dict[str, Any] = Field(default_factory=dict)


class ValueCandidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    dimension: str
    value: str
    extra: dict[str, Any] = Field(default_factory=dict)


class ValueCandidates(BaseModel):
    """Tolerant view of ``catalogue_resolve_values``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: str = ""
    candidates: tuple[ValueCandidate, ...] = ()
    axes: dict[str, str] = Field(default_factory=dict)
    extra: dict[str, Any] = Field(default_factory=dict)


class MetricDefinition(BaseModel):
    """One ``catalogue_get_metrics`` definition. Unknown keys sit in ``extra``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    label: str = ""
    unit: str | None = None
    aggregation: str | None = None
    description: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)


class MetricsQueryArgs(BaseModel):
    """Arguments for ``metrics_query`` and ``metrics_drilldown``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    measures: tuple[str, ...] = ()
    time_range: dict[str, str] = Field(default_factory=dict)
    dimensions: tuple[str, ...] = ()
    filters: tuple[dict[str, Any], ...] = ()
    granularity: str | None = None
    limit: int | None = None
    sort: tuple[dict[str, Any], ...] = ()
    module: str | None = None
    metric_id: str | None = None
    row: dict[str, Any] | None = None

    def as_query(self) -> dict[str, Any]:
        arguments: dict[str, Any] = {
            "measures": list(self.measures),
            "time_range": dict(self.time_range),
        }
        if self.dimensions:
            arguments["dimensions"] = list(self.dimensions)
        if self.filters:
            arguments["filters"] = [dict(item) for item in self.filters]
        if self.granularity:
            arguments["granularity"] = self.granularity
        if self.limit is not None:
            arguments["limit"] = self.limit
        if self.sort:
            arguments["sort"] = [dict(item) for item in self.sort]
        if self.module is not None:
            arguments["module"] = self.module
        return arguments

    def as_drill(self) -> dict[str, Any]:
        arguments = self.as_query()
        if self.metric_id:
            arguments["metric_id"] = self.metric_id
        if self.row:
            arguments["row"] = dict(self.row)
        return arguments


class QueryResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    rows: tuple[dict[str, Any], ...] = ()
    error: CatalogueError | None = None
    catalogue_version: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)


def concept_from_payload(payload: dict[str, Any] | None) -> ConceptResolution:
    raw = dict(payload or {})
    kind = str(raw.get("kind") or "")
    if not kind:
        if raw.get("metric_id"):
            kind = "resolved_concept"
        elif raw.get("error"):
            kind = "error"
        else:
            kind = "unknown_concept"
    known = {"kind", "metric_id", "concept", "axes", "reason", "suggestions", "nearest_metrics"}
    axes_raw = raw.get("axes")
    axes = axes_raw if isinstance(axes_raw, dict) else {}
    return ConceptResolution(
        kind=kind,
        metric_id=_opt(raw.get("metric_id")),
        concept=_opt(raw.get("concept")),
        axes={str(key): str(value) for key, value in axes.items()},
        reason=_opt(raw.get("reason") or raw.get("error")),
        suggestions=tuple(str(item) for item in (raw.get("suggestions") or []) if item),
        nearest_metrics=tuple(str(item) for item in (raw.get("nearest_metrics") or []) if item),
        extra={str(key): value for key, value in raw.items() if key not in known},
    )


def values_from_payload(payload: dict[str, Any] | None) -> ValueCandidates:
    raw = dict(payload or {})
    candidates: list[ValueCandidate] = []
    for source in (raw.get("candidates"), raw.get("matches"), raw.get("values")):
        if not isinstance(source, list):
            continue
        for item in source:
            if not isinstance(item, dict):
                continue
            dimension = str(item.get("dimension") or item.get("dimension_id") or "")
            value = str(item.get("value") or "")
            if not dimension and not value:
                continue
            candidates.append(ValueCandidate(
                dimension=dimension,
                value=value,
                extra={
                    str(key): item_value
                    for key, item_value in item.items()
                    if key not in {"dimension", "dimension_id", "value"}
                },
            ))
    axes_raw = raw.get("axes")
    axes = axes_raw if isinstance(axes_raw, dict) else {}
    known = {"status", "candidates", "matches", "values", "axes"}
    return ValueCandidates(
        status=str(raw.get("status") or ""),
        candidates=tuple(candidates),
        axes={str(key): str(value) for key, value in axes.items()},
        extra={str(key): value for key, value in raw.items() if key not in known},
    )


def definition_from_payload(payload: dict[str, Any]) -> MetricDefinition:
    known = {"id", "label", "display_name", "unit", "aggregation", "description"}
    return MetricDefinition(
        id=str(payload.get("id") or ""),
        label=str(payload.get("label") or payload.get("display_name") or ""),
        unit=_opt(payload.get("unit")),
        aggregation=_opt(payload.get("aggregation")),
        description=str(payload.get("description") or ""),
        extra={str(key): value for key, value in payload.items() if key not in known},
    )


def query_from_payload(payload: dict[str, Any] | None) -> QueryResponse:
    raw = dict(payload or {})
    rows_raw = raw.get("rows")
    rows = rows_raw if isinstance(rows_raw, list) else []
    message = _opt(raw.get("error"))
    error = CatalogueError(code="QUERY_FAILED", message=message, retryable=True) if message else None
    known = {"rows", "error", "catalogue_version"}
    return QueryResponse(
        rows=tuple(dict(row) for row in rows if isinstance(row, dict)),
        error=error,
        catalogue_version=str(raw.get("catalogue_version") or ""),
        extra={str(key): value for key, value in raw.items() if key not in known},
    )


def _opt(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


@runtime_checkable
class SemanticPort(Protocol):
    """The only catalogue entry point. Callers do not name MCP capabilities."""

    def version(self) -> str: ...

    def read_model(self) -> SemanticReadModel: ...

    async def resolve_concept(
        self, text: str, axes: dict[str, str] | None = None
    ) -> ConceptResolution: ...

    async def resolve_values(self, text: str, axes_only: bool = False) -> ValueCandidates: ...

    async def metric_definitions(self, ids: list[str]) -> dict[str, MetricDefinition]: ...

    async def query(self, args: MetricsQueryArgs) -> QueryResponse: ...

    async def drill(self, args: MetricsQueryArgs) -> QueryResponse: ...
