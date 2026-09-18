from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from seleric_swarm.contracts.lookup import TimeRangeV1
from seleric_swarm.domain.models import SeriesPoint
from seleric_swarm.services.mcp_query import row_date
from seleric_swarm.services.measure import module_args
from seleric_swarm.services.time_range import resolve_time_range
from seleric_swarm.toolsets.semantic import raw_query_metric

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

    # Sprint 2 consolidation (docs/refactor/SPRINT_PLAN.md): use the static
    # catalogue_metric field directly, no resolve_measure() keyword-search
    # fallback. A stale/missing id now surfaces as a live Cube error below,
    # not a silent substitution.
    measure = definition.catalogue_metric or None
    if measure is None:
        return [], {"error": "catalogue measure not found"}

    extra = module_args(definition)
    result = await raw_query_metric(
        runtime.mcp,
        agent_id=agent_id,
        metric_id=measure,
        start=start,
        end=end,
        grain=grain,
        filters=[{"dimension": "brand_id", "operator": "equals", "values": [brand_id]}],
        module=extra["module"] if extra else ...,
    )
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
