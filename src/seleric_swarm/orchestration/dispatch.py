"""Single mission entrypoint that routes by complexity (architecture answer 2).

Simple retrieval / comparison stays on the fast ``lookup_v1`` graph; diagnostic,
predictive and prescriptive questions enter the dynamic two-axis swarm. This is
the "fold lookup_v1 into the coordinator as the L0/L1 fast path" wiring - the
lookup graph and its tests are untouched, just no longer the only route.
"""

from __future__ import annotations

from typing import Any

from seleric_swarm.coordinator.planning.complexity import classify_complexity
from seleric_swarm.orchestration.runner import run_mission
from seleric_swarm.runtime import SwarmRuntime
from seleric_swarm.swarm.orchestrator import classify_intents, run_swarm_mission

_FAST_PATH = {"L0", "L1", "L2"}


async def route_for(runtime: SwarmRuntime, *, query: str) -> str:
    """Return "lookup" or "swarm" for a query (cheap, deterministic, no LLM)."""
    intents = classify_intents(query)
    if intents - {"diagnostic"} or _looks_diagnostic(query):
        return "swarm"
    level = classify_complexity(query_class="lookup", query=query, metric_hints=[], entities=[])
    return "lookup" if level.name in _FAST_PATH else "swarm"


def _looks_diagnostic(query: str) -> bool:
    q = query.lower()
    return any(k in q for k in ("why", "root cause", "diagnose", "what changed"))


async def run_any_mission(
    runtime: SwarmRuntime,
    *,
    query: str,
    timezone: str = "Asia/Kolkata",
    as_of: str | None = None,
    **kw: Any,
) -> dict[str, Any]:
    """Classify, then dispatch to the lookup fast path or the dynamic swarm.

    Returns a uniform dict so callers do not need to know which route ran.
    """
    route = await route_for(runtime, query=query)
    if route == "lookup":
        result = await run_mission(runtime, query=query, timezone=timezone, as_of=as_of)
        return {"route": "lookup", "result": result.model_dump()}
    swarm = await run_swarm_mission(runtime, query=query, timezone=timezone, as_of=as_of, **kw)
    return {"route": "swarm", "result": swarm.__dict__}
