"""Single mission entrypoint that routes by complexity (architecture answer 2).

Simple retrieval / comparison stays on the fast ``lookup_v1`` graph; diagnostic,
predictive and prescriptive questions enter the dynamic two-axis swarm. This is
the "fold lookup_v1 into the coordinator as the L0/L1 fast path" wiring - the
lookup graph and its tests are untouched, just no longer the only route.

Not yet wired into ``main.py`` (the HTTP API still calls ``run_mission``
directly); this is the intended future entrypoint. Callers branch on ``route``:
``result`` is ``MissionResult.model_dump()`` for lookup, ``SwarmMissionResult``
fields for swarm.
"""

from __future__ import annotations

from typing import Any

from seleric_swarm.orchestration.runner import run_mission
from seleric_swarm.runtime import SwarmRuntime
from seleric_swarm.swarm.orchestrator import classify_intents, run_swarm_mission

# Retrieval / comparison verbs that are safe for the lookup fast path.
_LOOKUP_RE = ("what were", "what was", "what is", "how much", "compare", " vs ", "versus", "show me")


async def route_for(runtime: SwarmRuntime, *, query: str) -> str:
    """Return "lookup" or "swarm" (cheap, deterministic, no LLM).

    Any diagnostic / predictive / prescriptive signal -> swarm. Otherwise, only a
    plain retrieval / comparison phrasing stays on the lookup fast path.
    """
    q = query.lower().strip()
    intents = classify_intents(query)  # never empty; defaults to {"diagnostic"}
    diagnostic_signal = intents != {"diagnostic"} or any(
        k in q for k in ("why", "root cause", "diagnose", "what changed", "caused", "explain", "driver of")
    )
    if diagnostic_signal:
        return "swarm"
    return "lookup" if any(q.startswith(p) or p in q for p in _LOOKUP_RE) else "swarm"


async def run_any_mission(
    runtime: SwarmRuntime,
    *,
    query: str,
    timezone: str = "Asia/Kolkata",
    as_of: str | None = None,
    session_id: str | None = None,
    request_id: str | None = None,
    **swarm_only: Any,
) -> dict[str, Any]:
    """Classify, then dispatch to the lookup fast path or the dynamic swarm.

    Shared kwargs (``session_id`` / ``request_id``) are forwarded to whichever
    route runs. ``**swarm_only`` (e.g. ``providers``, ``scenario_id``) applies
    only when the swarm route is taken and is ignored on the lookup route.
    """
    route = await route_for(runtime, query=query)
    if route == "lookup":
        result = await run_mission(
            runtime,
            query=query,
            timezone=timezone,
            as_of=as_of,
            session_id=session_id,
            request_id=request_id,
        )
        return {"route": "lookup", "result": result.model_dump()}
    swarm = await run_swarm_mission(
        runtime,
        query=query,
        timezone=timezone,
        as_of=as_of,
        session_id=session_id,
        request_id=request_id,
        **swarm_only,
    )
    return {"route": "swarm", "result": swarm.as_dict()}
