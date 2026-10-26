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

DATE_AXES: tuple[str, ...] = ("order_date", "event_date")  # Core's request-level ``date_axis`` (semantic v3, D-28)


def scope_date_axis(scope: dict[str, Any]) -> str | None:
    """``scope.date_axis`` (``dateAxis``) as one of ``DATE_AXES``; None when the request does not choose. Raises ValueError."""
    axis = scope.get("date_axis") or scope.get("dateAxis")
    if axis in (None, "", "string"):  # "string" is the Swagger placeholder
        return None
    if axis not in DATE_AXES:
        raise ValueError(f"scope.date_axis must be one of {list(DATE_AXES)}, got {axis!r}")
    return str(axis)


def with_date_axis(arguments: dict[str, Any], axis: str | None, catalogue: Any = None) -> dict[str, Any]:
    """``arguments`` on the run's date axis. An axis already in the arguments (the user's own wording asked for it) wins.
    Measures the catalogue marks as not switchable do not get it: on event_date Core counts older orders that merely had a
    cancel / RTO / return that day (45 "orders" for 26 placed on 2026-10-24).
    shortcut: a call mixing switchable and non-switchable measures still sends the axis, until Core ignores it per measure."""
    if not axis or "date_axis" in arguments:
        return arguments
    measures = [m for m in (arguments.get("measures") or arguments.get("metrics") or []) if isinstance(m, str)]
    if catalogue is not None and measures and all(catalogue.ignores_date_axis(m) for m in measures):
        return arguments
    return {**arguments, "date_axis": axis}


UNDATED_NOTE = (
    "Undated = cancelled / RTO / returned amounts with no outcome date, summed over ALL history. They are NOT part of the dated "
    "window returned here and did not move this period's figures; dated + Undated does not reconcile to the order-date total for one period."
)


def relabel_undated(result: Any) -> Any:
    """Core's own undated note ('dated + Undated reconcile to the order_date total') only holds over all history; say what it is instead."""
    undated = result.get("undated") if isinstance(result, dict) else None
    if isinstance(undated, dict):
        undated["note"] = UNDATED_NOTE
    return result


def applied_axes(result: dict[str, Any]) -> dict[str, str]:
    """metric -> what Core says it applied (``date_axis_applied``), from the result and from each part of a composed one."""
    out: dict[str, str] = {}
    for holder in (result, *[p for p in result.get("parts") or [] if isinstance(p, dict)]):
        applied = holder.get("date_axis_applied")
        if isinstance(applied, dict):
            out.update({str(k): str(v) for k, v in applied.items()})
    return out


def axis_statement(applied: dict[str, str]) -> str | None:
    """'order_date' / 'event_date', plus the metrics that run on their own clock; None when Core reported no axis."""
    axes = sorted({v.split(" ", 1)[0] for v in applied.values() if v.split(" ", 1)[0] in DATE_AXES})
    if not axes:
        return None
    native = sorted(k for k, v in applied.items() if v.startswith("native"))
    text = " and ".join(axes)
    return text + (f" ({', '.join(native)} run on their own clock and ignore it)" if native else "")


def _scope_keys() -> frozenset[str]:
    from seleric_swarm.exploration.space import configured_scope_ids

    return frozenset(item.lower() for item in configured_scope_ids())


def _has_scope(
    dimensions: list[str] | None, filters: list[dict[str, Any]] | None
) -> bool:
    """True when the query already filters or groups by a tenant dimension."""
    keys = {str(d).strip().lower() for d in (dimensions or [])}
    keys |= {str(f.get("dimension", "")).strip().lower() for f in (filters or [])}
    return bool(keys & _scope_keys())


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
    if inject_default_brand and not _has_scope(dimensions, filters):
        from seleric_swarm.exploration.space import configured_scope_ids, default_brand_id

        scope_ids = configured_scope_ids()
        brand = default_brand_id()
        if scope_ids and brand:
            all_filters.append(
                {"dimension": scope_ids[0], "operator": "equals", "values": [brand]}
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
    return relabel_undated(result)
