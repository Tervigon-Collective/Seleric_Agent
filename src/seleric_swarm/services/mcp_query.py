"""One metrics_query argument builder + call. Used by BSS series, Hybrid fetch, and fetch_series."""

from __future__ import annotations

import re
from typing import Any

# Cube appends the granularity to the time dimension:
# ``meta_ad_performance.report_date.week``, ``….day``, ``….month``, and the
# rest of its set. Matching only ``.day`` dropped every weekly bucket onto
# the query window (live 2026-09-23 MS3-b45585f72d: two meta_ctr weeks, both
# labelled 2026-09-09..2026-09-22, flagged as one source contradiction).
_GRANULARITY_DATE_KEY = re.compile(
    r"\.(?:second|minute|hour|day|week|month|quarter|year)$"
)

# The tenant's default brand. A metrics_query with no brand filter and no brand
# breakdown is scoped to this brand HERE, in the API arguments — the single place
# the default lives. Previously the MCP server injected it and several services
# hardcoded "20" of their own; both are gone in favour of this constant.
DEFAULT_BRAND_ID = "20"

# Dimension keys that name a brand filter/breakdown. If a query already scopes or
# groups by brand, the default is NOT injected (a brand breakdown must span all
# brands; an explicit brand filter is the caller's own choice).
_BRAND_DIM_KEYS = frozenset({"brand_id", "brand", "brand_name"})


def _has_brand(
    dimensions: list[str] | None, filters: list[dict[str, Any]] | None
) -> bool:
    keys = {str(d).strip().lower() for d in (dimensions or [])}
    keys |= {str(f.get("dimension", "")).strip().lower() for f in (filters or [])}
    return bool(keys & _BRAND_DIM_KEYS)


def row_date(row: dict[str, Any]) -> str | None:
    """Cube names a grain time dimension ``<view>.<dimension>.<granularity>``."""
    for key, value in row.items():
        if _GRANULARITY_DATE_KEY.search(key) and isinstance(value, str) and len(value) >= 10:
            return value[:10]
    return None


def dimension_value(row: dict[str, Any], dim_id: str) -> Any:
    if dim_id in row:
        return row[dim_id]
    suffix = f".{dim_id}"
    for key, value in row.items():
        if str(key).endswith(suffix):
            return value
    return None


def split_dimension_dict(
    dimensions: dict[str, Any] | None,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Empty values → Cube breakdown dims; set values → equals filters."""
    breakdown: list[str] = []
    filters: list[dict[str, Any]] = []
    for key, value in (dimensions or {}).items():
        if value is None or value == "":
            breakdown.append(str(key))
        else:
            filters.append({"dimension": str(key), "operator": "equals", "values": [str(value)]})
    return breakdown, filters


def build_metrics_query_args(
    *,
    measure: str,
    start: str,
    end: str,
    grain: str | None = None,
    dimensions: list[str] | None = None,
    filters: list[dict[str, Any]] | None = None,
    limit: int | None = None,
    sort: list[dict[str, Any]] | None = None,
    compare_period: str | None = None,
    module: Any = ...,
    inject_default_brand: bool = True,
) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "measures": [measure],
        "time_range": {"start": start, "end": end},
    }
    if grain:
        arguments["granularity"] = grain
    if dimensions:
        arguments["dimensions"] = list(dimensions)
    all_filters = list(filters) if filters else []
    if inject_default_brand and not _has_brand(dimensions, filters):
        all_filters.append(
            {"dimension": "brand_id", "operator": "equals", "values": [DEFAULT_BRAND_ID]}
        )
    if all_filters:
        arguments["filters"] = all_filters
    if limit is not None:
        arguments["limit"] = limit
    if sort:
        arguments["sort"] = list(sort)
    if compare_period:
        arguments["compare_period"] = compare_period
    if module is not ...:
        arguments["module"] = module
    return arguments


async def call_metrics_query(
    mcp: Any,
    *,
    agent_id: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    try:
        result = await mcp.call(
            agent_id=agent_id, capability="seleric.metrics_query", arguments=arguments
        )
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}", "rows": [], "provenance": {}}
    if not isinstance(result, dict):
        return {"error": "invalid metrics_query payload", "rows": [], "provenance": {}}
    return result
