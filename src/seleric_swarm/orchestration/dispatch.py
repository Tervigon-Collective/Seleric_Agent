"""Single mission entrypoint that routes by complexity (architecture answer 2).

Simple retrieval / comparison stays on the fast ``lookup_v1`` graph; diagnostic,
predictive and prescriptive questions enter the dynamic two-axis swarm. This is
the "fold lookup_v1 into the coordinator as the L0/L1 fast path" wiring - the
lookup graph and its tests are untouched, just no longer the only route.

``POST /v1/missions`` calls this. Callers branch on ``route``: ``result`` is
``MissionResult.model_dump()`` for lookup, ``SwarmMissionResult`` fields for
swarm. ``main.py`` flattens it to ``{"route": ..., **result}``.

Swarm queries run through Coordinator V1 (``coordinator.graph.run_swarm_v2_mission``).
"""

from __future__ import annotations

from typing import Any

from seleric_swarm.orchestration.runner import run_mission
from seleric_swarm.runtime import SwarmRuntime

_SWARM_INTENTS = {"diagnostic", "predictive", "prescriptive", "executive_health"}


async def route_for(
    runtime: SwarmRuntime,
    *,
    query: str,
    timezone: str = "Asia/Kolkata",
    as_of: str | None = None,
) -> str:
    """Return "lookup" or "swarm", based on the LLM's classified intent.

    Diagnostic / predictive / prescriptive / health → swarm.
    Plain retrieval / comparison → the lookup fast path. Falls back to the
    offline regex classifier only when the LLM classification path itself
    isn't usable (no prompt/LLM configured) — same fallback rule
    coordinator.intake.normalize_query uses.
    """
    from seleric_swarm.coordinator.intake.llm_classifier import classify_query_via_llm

    classification = await classify_query_via_llm(query, runtime=runtime, timezone=timezone, as_of=as_of)
    if classification is not None:
        intents = set(classification.intents)
    else:
        from seleric_swarm.coordinator.intake import classify_intents as intake_classify_intents

        intents = set(intake_classify_intents(query))
    return "swarm" if intents & _SWARM_INTENTS else "lookup"


async def run_any_mission(
    runtime: SwarmRuntime,
    *,
    query: str,
    timezone: str = "Asia/Kolkata",
    as_of: str | None = None,
    session_id: str | None = None,
    request_id: str | None = None,
    mission_id: str | None = None,
    **swarm_only: Any,
) -> dict[str, Any]:
    """Classify, then dispatch to the lookup fast path or the dynamic swarm.

    Shared kwargs (``session_id`` / ``request_id`` / ``mission_id``) are forwarded
    to whichever route runs. ``**swarm_only`` (e.g. ``providers``) applies only
    when the swarm route is taken and is ignored on the lookup route.
    """
    route = await route_for(runtime, query=query, timezone=timezone, as_of=as_of)
    if route == "lookup":
        result = await run_mission(
            runtime,
            query=query,
            timezone=timezone,
            as_of=as_of,
            session_id=session_id,
            request_id=request_id,
            mission_id=mission_id,
        )
        return {"route": "lookup", "result": result.model_dump()}

    from seleric_swarm.coordinator.graph import run_swarm_v2_mission

    swarm = await run_swarm_v2_mission(
        runtime,
        query=query,
        timezone=timezone,
        as_of=as_of,
        session_id=session_id,
        request_id=request_id,
        mission_id=mission_id,
        **swarm_only,
    )
    payload = swarm.as_dict()
    rid = request_id or payload.get("request_id")
    sid = session_id or payload.get("session_id")
    if rid and sid:
        payload.setdefault("trace", {"request_id": rid, "session_id": sid})
    return {"route": "swarm", "workflow": "swarm_v2", "result": payload}
