"""One metrics_query argument builder + call. Used by BSS series, Hybrid fetch, and fetch_series."""

from __future__ import annotations

import re
from typing import Any

_GRANULARITY_DATE_KEY = re.compile(r"\.day$")


def row_date(row: dict[str, Any]) -> str | None:
    """Cube names a grain time dimension ``<view>.<dimension>.<granularity>``."""
    for key, value in row.items():
        if _GRANULARITY_DATE_KEY.search(key) and isinstance(value, str):
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
) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "measures": [measure],
        "time_range": {"start": start, "end": end},
    }
    if grain:
        arguments["granularity"] = grain
    if dimensions:
        arguments["dimensions"] = list(dimensions)
    if filters:
        arguments["filters"] = list(filters)
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
