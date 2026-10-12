"""Immutable catalogue read model. Unknown payload fields land in ``extra``."""

from __future__ import annotations

import difflib
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

_METRIC_KEYS = frozenset({
    "id",
    "label",
    "display_name",
    "view",
    "cube_view",
    "unit",
    "aggregation",
    "supported_dimensions",
    "date_basis",
    "date_twin",
    "grain_twins",
    "volume_metric",
    "composition",
    "split_by",
    "valid_for",
    "excluded_dimensions",
    "currency",
    "currency_default",
})
_DIMENSION_KEYS = frozenset({
    "id",
    "is_time",
    "family",
    "family_rank",
    "stable_key",
    "hierarchy",
    "allowed_values",
    "aliases",
    "display_name",
})


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _str_tuple(value: Any) -> tuple[str, ...]:
    if not value:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value if item)


class MetricRecord(BaseModel):
    """One catalogue metric. Fields Core has not declared yet sit in ``extra``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    label: str = ""
    view: str = ""
    unit: str | None = None
    aggregation: str | None = None
    supported_dimensions: tuple[str, ...] = ()
    date_basis: str | None = None
    date_twin: str | None = None
    grain_twins: tuple[str, ...] = ()
    volume_metric: str | None = None
    composition: Any | None = None
    split_by: str | None = None
    valid_for: dict[str, tuple[str, ...]] | None = None
    excluded_dimensions: dict[str, str] | None = None
    currency: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class DimensionRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    is_time: bool = False
    family: str | None = None
    family_rank: int = 0
    stable_key: str | None = None
    hierarchy: dict[str, Any] | None = None
    allowed_values: tuple[str, ...] = ()
    extra: dict[str, Any] = Field(default_factory=dict)


class HierarchyRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    levels: tuple[str, ...] = ()
    views: tuple[str, ...] = ()
    extra: dict[str, Any] = Field(default_factory=dict)


class ViewRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    freshness: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class BrandRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    name: str = ""
    code: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class SemanticReadModel(BaseModel):
    """One frozen catalogue snapshot for a mission.

    Methods match ``CatalogueSnapshot``: family lookup, conformed siblings,
    ``carries``, grain twins, and the redirect rank.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = 1
    version: str = ""
    metrics: tuple[MetricRecord, ...] = ()
    dimensions: tuple[DimensionRecord, ...] = ()
    hierarchies: tuple[HierarchyRecord, ...] = ()
    views: tuple[ViewRecord, ...] = ()
    brands: tuple[BrandRecord, ...] = ()
    scope_dimension_ids: tuple[str, ...] = ()
    grain_defaults: dict[str, Any] = Field(default_factory=dict)
    default_brand_id: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> SemanticReadModel:
        return _parse_payload(payload or {})

    @classmethod
    def from_legacy(cls, snapshot: Any) -> SemanticReadModel:
        """Build a read model from a ``CatalogueSnapshot`` (or the same shape)."""
        metrics: list[dict[str, Any]] = []
        for meta in getattr(snapshot, "metrics", ()) or ():
            raw = dict(getattr(meta, "raw", None) or {})
            raw.setdefault("id", meta.id)
            if getattr(meta, "label", ""):
                raw.setdefault("label", meta.label)
            if getattr(meta, "view", ""):
                raw.setdefault("view", meta.view)
            raw.setdefault("supported_dimensions", list(getattr(meta, "supported_dimensions", ()) or ()))
            metrics.append(raw)
        families = {
            dim_id: (family, rank)
            for dim_id, family, rank in (getattr(snapshot, "dimension_families", ()) or ())
        }
        values = dict(getattr(snapshot, "dimension_values", ()) or ())
        facts = {dim_id: dict(pairs) for dim_id, pairs in (getattr(snapshot, "dimension_facts", ()) or ())}
        stable = dict(getattr(snapshot, "stable_keys", ()) or ())
        time_dims = set(getattr(snapshot, "time_dimensions", ()) or ())
        dimensions: list[dict[str, Any]] = []
        for dim_id in getattr(snapshot, "dimensions", ()) or ():
            if not isinstance(dim_id, str):
                continue
            family, rank = families.get(dim_id, (None, 0))
            level = int(facts.get(dim_id, {}).get("hierarchy_level") or 0)
            row: dict[str, Any] = {
                "id": dim_id,
                "is_time": dim_id in time_dims,
                "family": family,
                "family_rank": rank,
                "stable_key": stable.get(dim_id),
            }
            if level:
                row["hierarchy"] = {"level": level}
            if dim_id in values:
                row["allowed_values"] = list(values[dim_id])
            dimensions.append(row)
        return _parse_payload({
            "metrics": metrics,
            "dimensions": dimensions,
            "scope_dimensions": list(getattr(snapshot, "scope_dimension_ids", ()) or ()),
        })

    def _metric(self, metric_id: str) -> MetricRecord | None:
        for metric in self.metrics:
            if metric.id == metric_id:
                return metric
        return None

    def _dimension(self, dimension: str) -> DimensionRecord | None:
        for dim in self.dimensions:
            if dim.id == dimension:
                return dim
        return None

    def dimension_ids(self) -> frozenset[str]:
        ids = {dim.id for dim in self.dimensions}
        for metric in self.metrics:
            ids.update(metric.supported_dimensions)
        return frozenset(ids)

    def dimension_fact(self, dimension: str, key: str) -> int:
        dim = self._dimension(dimension)
        if dim is None:
            return 0
        if key == "hierarchy_level":
            hierarchy = dim.hierarchy or {}
            try:
                return int(hierarchy.get("level") or 0)
            except (TypeError, ValueError):
                return 0
        if key == "n_allowed":
            return len(dim.allowed_values)
        try:
            return int((dim.extra or {}).get(key) or 0)
        except (TypeError, ValueError):
            return 0

    def allowed_values_for(self, dimension: str) -> tuple[str, ...] | None:
        dim = self._dimension(dimension)
        if dim is None or not dim.allowed_values:
            return None
        return dim.allowed_values

    def conformed_sibling(
        self,
        dimension: str,
        supported: set[str] | frozenset[str],
        values: list[str] | None = None,
    ) -> str | None:
        dim = self._dimension(dimension)
        family = dim.family if dim is not None else None
        if not family:
            return None
        members = sorted(
            (other.family_rank, other.id)
            for other in self.dimensions
            if other.family == family and other.id != dimension
        )
        for _, member in members:
            if member not in supported:
                continue
            allowed = self.allowed_values_for(member)
            if not values or allowed is None:
                return member
            lowered = {item.lower() for item in allowed}
            if all(str(value).lower() in lowered for value in values):
                return member
        return None

    def family_head(self, dimension: str) -> str:
        dim = self._dimension(dimension)
        family = dim.family if dim is not None else None
        if not family:
            return dimension
        heads = sorted(
            (other.family_rank, other.id) for other in self.dimensions if other.family == family
        )
        return heads[0][1] if heads else dimension

    def family_members(self, dimension: str) -> frozenset[str]:
        dim = self._dimension(dimension)
        family = dim.family if dim is not None else None
        if not family:
            return frozenset({dimension})
        return frozenset(other.id for other in self.dimensions if other.family == family) | {dimension}

    def carries(self, metric_id: str, dimension: str) -> bool:
        if not self.metrics or self.is_time_dimension(dimension):
            return True
        for mid in (metric_id, *self.grain_twins_for(metric_id)):
            supported = set(self.supported_dimensions_for(mid))
            if not supported or dimension in supported or self.conformed_sibling(dimension, supported):
                return True
        return False

    def stable_key_for(self, dimension: str) -> str | None:
        dim = self._dimension(dimension)
        return dim.stable_key if dim is not None else None

    def is_time_dimension(self, dimension: str) -> bool:
        dim = self._dimension(dimension)
        return bool(dim and dim.is_time)

    def dimension_hierarchy_level(self, dimension: str) -> int | None:
        dim = self._dimension(dimension)
        if dim is None or not dim.hierarchy:
            return None
        return dim.hierarchy.get("level")

    def metric_ids(self) -> frozenset[str]:
        return frozenset(metric.id for metric in self.metrics)

    def has_metric(self, metric_id: str) -> bool:
        return self._metric(metric_id) is not None

    def label_for(self, metric_id: str) -> str | None:
        metric = self._metric(metric_id)
        if metric is None:
            return None
        return metric.label.strip() or None

    def date_basis_for(self, metric_id: str) -> tuple[str | None, str | None]:
        metric = self._metric(metric_id)
        if metric is None:
            return None, None
        return metric.date_basis, metric.date_twin

    def unit_for(self, metric_id: str) -> str | None:
        metric = self._metric(metric_id)
        return metric.unit if metric is not None else None

    def aggregation_for(self, metric_id: str) -> str | None:
        metric = self._metric(metric_id)
        if metric is None or not metric.aggregation:
            return None
        return metric.aggregation.strip().lower() or None

    def supported_dimensions_for(self, metric_id: str) -> list[str]:
        metric = self._metric(metric_id)
        if metric is None:
            return []
        return [dim for dim in metric.supported_dimensions if dim]

    def grain_twins_for(self, metric_id: str) -> list[str]:
        metric = self._metric(metric_id)
        if metric is None:
            return []
        return [twin for twin in metric.grain_twins if twin]

    def volume_metric_for(self, metric_id: str) -> str | None:
        metric = self._metric(metric_id)
        return metric.volume_metric if metric is not None else None

    def metrics_supporting_dimension(
        self, dimension: str, n: int = 6, like: str | None = None
    ) -> list[str]:
        """Redirect candidates for a breakdown the asked metric cannot carry.

        Catalogue signals only: grain twins, then the same unit, then the same
        aggregation, then how many supported dimensions are shared, then
        ``extra.redirect_rank`` when Core sends it.
        """
        target = dimension.strip()
        hits = [metric for metric in self.metrics if target in metric.supported_dimensions]
        if not hits:
            return []
        like_id = (like or "").strip()
        like_metric = self._metric(like_id) if like_id else None
        twins = set(like_metric.grain_twins) if like_metric is not None else set()
        like_unit = like_metric.unit if like_metric is not None else None
        like_agg = (like_metric.aggregation or "").strip().lower() if like_metric is not None else ""
        like_dims = set(like_metric.supported_dimensions) if like_metric is not None else set()

        def score(metric: MetricRecord) -> tuple[int, int, int, int, int, str]:
            same_unit = 1 if like_unit and metric.unit == like_unit else 0
            same_agg = 1 if like_agg and (metric.aggregation or "").strip().lower() == like_agg else 0
            shared = len(set(metric.supported_dimensions) & like_dims)
            try:
                redirect = int((metric.extra or {}).get("redirect_rank") or 0)
            except (TypeError, ValueError):
                redirect = 0
            twin_hit = 1 if metric.id in twins else 0
            return (-twin_hit, -same_unit, -same_agg, -shared, -redirect, metric.id)

        return [metric.id for metric in sorted(hits, key=score)[:n]]

    def closest_metric_ids(self, query: str, n: int = 5) -> list[str]:
        pool: set[str] = set()
        alias_to_id: dict[str, str] = {}
        for metric in self.metrics:
            pool.add(metric.id)
            for alias in metric.extra.get("aliases") or []:
                if alias:
                    pool.add(str(alias))
                    alias_to_id[str(alias)] = metric.id
        matches = difflib.get_close_matches(query, sorted(pool), n=n, cutoff=0.4)
        seen: list[str] = []
        known = self.metric_ids()
        for match in matches:
            resolved = alias_to_id.get(match, match)
            if resolved in known and resolved not in seen:
                seen.append(resolved)
        return seen

    def render_compact(self) -> str:
        if not self.metrics:
            return ""
        lines = ["Metrics (id: label):"]
        lines += [f"- {metric.id}: {metric.label or metric.id}" for metric in sorted(self.metrics, key=lambda m: m.id)]
        dim_ids = sorted(self.dimension_ids())
        if dim_ids:
            lines.append("Dimensions: " + ", ".join(dim_ids))
        return "\n".join(lines)

    def render(self) -> str:
        if not self.metrics:
            return ""
        lines = [
            (
                "Full metric catalogue — resolve the user's metric by matching "
                "this list and use the id exactly as written (Cube validates it):"
            )
        ]
        for metric in sorted(self.metrics, key=lambda item: item.id):
            bits: list[str] = []
            if metric.unit:
                bits.append(f"unit={metric.unit}")
            aliases = [str(alias) for alias in (metric.extra.get("aliases") or []) if alias]
            if aliases:
                bits.append(f"aliases={', '.join(aliases)}")
            if metric.supported_dimensions:
                bits.append(f"dims={', '.join(metric.supported_dimensions)}")
            suffix = f" ({'; '.join(bits)})" if bits else ""
            lines.append(f"- {metric.id}: {metric.label or metric.id}{suffix}")
        keys = {dim.id: dim.stable_key for dim in self.dimensions if dim.stable_key}
        dim_ids = sorted(self.dimension_ids())
        if dim_ids:
            lines.append("Dimensions: " + ", ".join(
                f"{dim_id} (key: {keys[dim_id]})" if dim_id in keys else dim_id for dim_id in dim_ids
            ))
        return "\n".join(lines)


def _metric_from_row(row: dict[str, Any]) -> MetricRecord | None:
    metric_id = str(row.get("id") or "")
    if not metric_id:
        return None
    valid_for = row.get("valid_for")
    parsed_valid: dict[str, tuple[str, ...]] | None = None
    if isinstance(valid_for, dict):
        parsed_valid = {str(key): _str_tuple(value) for key, value in valid_for.items()}
    excluded = row.get("excluded_dimensions")
    parsed_excluded = (
        {str(key): str(value) for key, value in excluded.items()} if isinstance(excluded, dict) else None
    )
    extra = {str(key): value for key, value in row.items() if key not in _METRIC_KEYS}
    return MetricRecord(
        id=metric_id,
        label=str(row.get("label") or row.get("display_name") or ""),
        view=str(row.get("view") or row.get("cube_view") or ""),
        unit=_opt_str(row.get("unit")),
        aggregation=_opt_str(row.get("aggregation")),
        supported_dimensions=_str_tuple(row.get("supported_dimensions")),
        date_basis=_opt_str(row.get("date_basis")),
        date_twin=_opt_str(row.get("date_twin")),
        grain_twins=_str_tuple(row.get("grain_twins")),
        volume_metric=_opt_str(row.get("volume_metric")),
        composition=row.get("composition"),
        split_by=_opt_str(row.get("split_by")),
        valid_for=parsed_valid,
        excluded_dimensions=parsed_excluded,
        currency=_opt_str(row.get("currency") or row.get("currency_default")),
        extra=extra,
    )


def _dimension_from_row(row: dict[str, Any]) -> DimensionRecord | None:
    dim_id = str(row.get("id") or "")
    if not dim_id:
        return None
    hierarchy = row.get("hierarchy") if isinstance(row.get("hierarchy"), dict) else None
    family = _opt_str(row.get("family"))
    extra = {str(key): value for key, value in row.items() if key not in _DIMENSION_KEYS}
    return DimensionRecord(
        id=dim_id,
        is_time=bool(row.get("is_time")),
        family=family,
        family_rank=int(row.get("family_rank") or 0) if family else 0,
        stable_key=_opt_str(row.get("stable_key")),
        hierarchy=dict(hierarchy) if hierarchy else None,
        allowed_values=_str_tuple(row.get("allowed_values")),
        extra=extra,
    )


def _hierarchies(payload: dict[str, Any]) -> tuple[HierarchyRecord, ...]:
    raw = payload.get("hierarchies") or {}
    records: list[HierarchyRecord] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and item.get("id"):
                extra = {
                    str(key): value
                    for key, value in item.items()
                    if key not in {"id", "levels", "views"}
                }
                records.append(HierarchyRecord(
                    id=str(item["id"]),
                    levels=_str_tuple(item.get("levels")),
                    views=_str_tuple(item.get("views")),
                    extra=extra,
                ))
        return tuple(records)
    if not isinstance(raw, dict):
        return ()
    for key, value in raw.items():
        if isinstance(value, dict):
            extra = {
                str(item_key): item
                for item_key, item in value.items()
                if item_key not in {"levels", "views", "note"}
            }
            if value.get("note"):
                extra["note"] = value["note"]
            records.append(HierarchyRecord(
                id=str(key),
                levels=_str_tuple(value.get("levels")),
                views=_str_tuple(value.get("views")),
                extra=extra,
            ))
        else:
            records.append(HierarchyRecord(id=str(key), levels=_str_tuple(value)))
    return tuple(records)


def _views(payload: dict[str, Any]) -> tuple[ViewRecord, ...]:
    raw = payload.get("views") or []
    records: list[ViewRecord] = []
    if isinstance(raw, dict):
        raw = [{"id": key, **value} if isinstance(value, dict) else {"id": key} for key, value in raw.items()]
    if not isinstance(raw, list):
        return ()
    for item in raw:
        if isinstance(item, str):
            records.append(ViewRecord(id=item))
            continue
        if not isinstance(item, dict):
            continue
        view_id = str(item.get("id") or item.get("view") or item.get("name") or "")
        if not view_id:
            continue
        freshness = _opt_str(item.get("freshness") or item.get("freshness_status"))
        extra = {
            str(key): value
            for key, value in item.items()
            if key not in {"id", "view", "name", "freshness", "freshness_status"}
        }
        records.append(ViewRecord(id=view_id, freshness=freshness, extra=extra))
    return tuple(records)


def _brands(payload: dict[str, Any]) -> tuple[BrandRecord, ...]:
    raw = payload.get("brands") or []
    if not isinstance(raw, list):
        return ()
    records: list[BrandRecord] = []
    for item in raw:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        extra = {str(key): value for key, value in item.items() if key not in {"id", "name", "code"}}
        records.append(BrandRecord(
            id=str(item["id"]),
            name=str(item.get("name") or ""),
            code=_opt_str(item.get("code")),
            extra=extra,
        ))
    return tuple(records)


def _scope_ids(payload: dict[str, Any], dimensions: tuple[DimensionRecord, ...]) -> tuple[str, ...]:
    declared: list[str] = []
    raw = payload.get("scope_dimensions") or payload.get("scope_dimension")
    if isinstance(raw, str) and raw.strip():
        declared.append(raw.strip())
    elif isinstance(raw, list):
        declared.extend(str(item) for item in raw if item)
    for dim in dimensions:
        if dim.extra.get("is_scope") or dim.extra.get("role") == "scope":
            declared.append(dim.id)
    seen: list[str] = []
    for item in declared:
        if item not in seen:
            seen.append(item)
    return tuple(seen)


_TOP_LEVEL = frozenset({
    "metrics",
    "matches",
    "dimensions",
    "hierarchies",
    "views",
    "brands",
    "scope_dimensions",
    "scope_dimension",
    "grain_defaults",
    "catalogue_version",
    "default_brand_id",
    "semantic_version",
})


def _parse_payload(payload: dict[str, Any]) -> SemanticReadModel:
    rows = payload.get("metrics") or payload.get("matches") or []
    metrics = tuple(
        record
        for record in (_metric_from_row(row) for row in rows if isinstance(row, dict))
        if record is not None
    )
    dim_rows = payload.get("dimensions") or []
    dimensions = tuple(
        record
        for record in (
            _dimension_from_row(row) for row in dim_rows if isinstance(row, dict)
        )
        if record is not None
    )
    defaults = payload.get("grain_defaults") or {}
    extra = {str(key): value for key, value in payload.items() if key not in _TOP_LEVEL}
    return SemanticReadModel(
        version=str(payload.get("catalogue_version") or ""),
        metrics=metrics,
        dimensions=dimensions,
        hierarchies=_hierarchies(payload),
        views=_views(payload),
        brands=_brands(payload),
        scope_dimension_ids=_scope_ids(payload, dimensions),
        grain_defaults=dict(defaults) if isinstance(defaults, dict) else {},
        default_brand_id=_opt_str(payload.get("default_brand_id")),
        extra=extra,
    )
