"""Single mission entrypoint that routes by complexity (architecture answer 2).

Simple retrieval / comparison stays on the fast ``lookup_v1`` graph; diagnostic,
predictive and prescriptive questions enter the dynamic two-axis swarm. This is
the "fold lookup_v1 into the coordinator as the L0/L1 fast path" wiring - the
lookup graph and its tests are untouched, just no longer the only route.

``POST /v1/missions`` calls this. Callers branch on ``route``: ``result`` is
``MissionResult.model_dump()`` for lookup, ``SwarmMissionResult`` fields for
swarm. ``main.py`` flattens it to ``{"route": ..., **result}``.

Swarm workflow is selected by ``settings.swarm_workflow``:
``swarm_v1`` (legacy imperative) or ``swarm_v2`` (Coordinator V1 control plane).
"""

from __future__ import annotations

from typing import Any

from seleric_swarm.coordinator.intake import classify_intents as intake_classify_intents
from seleric_swarm.orchestration.runner import run_mission
from seleric_swarm.runtime import SwarmRuntime
from seleric_swarm.swarm.orchestrator import run_swarm_mission


async def route_for(runtime: SwarmRuntime, *, query: str) -> str:
    """Return "lookup" or "swarm" (cheap, deterministic, no LLM).

    Diagnostic / predictive / prescriptive / health → swarm.
    Plain retrieval / comparison phrasing stays on the lookup fast path.

    Classification is delegated entirely to coordinator.intake.classify_intents
    (word-boundary regexes) rather than a second hand-rolled substring keyword
    list here, so routing and in-swarm specialist activation can't drift apart.
    """
    intake = set(intake_classify_intents(query))
    lookupish = bool(intake & {"lookup", "comparison"})
    # Always swarm for investigation / forecast / action / health.
    if intake & {"predictive", "prescriptive", "executive_health"}:
        return "swarm"
    if "diagnostic" in intake and not lookupish:
        return "swarm"
    if lookupish:
        return "lookup"
    return "swarm"


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

    workflow = getattr(runtime.settings, "swarm_workflow", "swarm_v1")
    if workflow == "swarm_v2":
        from seleric_swarm.coordinator.graph import run_swarm_v2_mission

        swarm = await run_swarm_v2_mission(
            runtime,
            query=query,
            timezone=timezone,
            as_of=as_of,
            session_id=session_id,
            request_id=request_id,
            **swarm_only,
        )
        return {"route": "swarm", "workflow": "swarm_v2", "result": swarm.as_dict()}

    swarm = await run_swarm_mission(
        runtime,
        query=query,
        timezone=timezone,
        as_of=as_of,
        session_id=session_id,
        request_id=request_id,
        **swarm_only,
    )
    return {"route": "swarm", "workflow": "swarm_v1", "result": swarm.as_dict()}
