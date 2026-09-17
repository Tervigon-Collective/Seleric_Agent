from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from seleric_swarm.contracts.lookup import TimeRangeV1
from seleric_swarm.domain.models import SeriesPoint
from seleric_swarm.services.mcp_query import build_metrics_query_args, call_metrics_query, row_date
from seleric_swarm.services.measure import module_args, resolve_measure
from seleric_swarm.services.time_range import resolve_time_range

if TYPE_CHECKING:
    from seleric_swarm.runtime import SwarmRuntime
    from seleric_swarm.services.metrics import MetricDefinition


async def fetch_series(
    runtime: SwarmRuntime,
    *,
    definition: MetricDefinition,
    agent_id: str,
    time_range: TimeRangeV1,
    brand_id: str,
    grain: str = "day",
    max_lookback_days: int = 90,
) -> tuple[list[SeriesPoint], dict[str, Any]]:
    """Fetch a normalized ascending ``{ts, value}`` series for one catalogue metric.

    Returns ``(series, provenance)``. Empty series + ``provenance["error"]``
    on any MCP-side refusal -- callers turn that into a quality flag, never a
    fabricated point.
    """
    resolved = resolve_time_range(time_range, definition.timezone, None)
    start, end = resolved.start, resolved.end
    if not start or not end:
        return [], {"error": "time_range did not resolve to concrete dates"}
    start_date, end_date = date.fromisoformat(start), date.fromisoformat(end)
    if (end_date - start_date).days > max_lookback_days:
        start_date = end_date - timedelta(days=max_lookback_days)
        start = start_date.isoformat()

    measure = await resolve_measure(
        definition,
        mcp=runtime.mcp,
        agent_id=agent_id,
        bootstrap=getattr(runtime, "bootstrap", None),
        metrics=getattr(runtime, "metrics", None),
    )
    if measure is None:
        return [], {"error": "catalogue measure not found"}

    extra = module_args(definition)
    arguments = build_metrics_query_args(
        measure=measure,
        start=start,
        end=end,
        grain=grain,
        filters=[{"dimension": "brand_id", "operator": "equals", "values": [brand_id]}],
        module=extra["module"] if extra else ...,
    )
    result = await call_metrics_query(runtime.mcp, agent_id=agent_id, arguments=arguments)
    provenance = dict(result.get("provenance") or {})
    if result.get("error"):
        provenance["error"] = result["error"]
        return [], provenance

    points: list[SeriesPoint] = []
    for row in result.get("rows") or []:
        ts = row_date(row)
        raw_value = row.get(measure)
        if ts is None or raw_value is None:
            continue
        points.append(SeriesPoint(ts=ts, value=float(raw_value)))
    points.sort(key=lambda p: p.ts)
    return points, provenance
