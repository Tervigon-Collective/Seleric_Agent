"""Single mission entrypoint that routes by complexity (architecture answer 2).

Simple retrieval / comparison stays off the dynamic two-axis swarm; diagnostic,
predictive and prescriptive questions enter it. Swarm queries run through
Coordinator V1 (``coordinator.graph.run_swarm_v2_mission``).

``POST /v1/missions`` calls this. Callers branch on ``route``: ``result`` is
``MissionResult.model_dump()`` for lookup, ``SwarmMissionResult`` fields for
swarm. ``main.py`` flattens it to ``{"route": ..., **result}``.

[2026-09-15, retiring lookup_v1 -- see docs/features/lookup-v1-retirement.md]
The "lookup" branch now tries ``coordinator.lookup_fast_path.run_lookup_fast_path``
first -- it answers a plain or dimensioned lookup directly via
``BusinessStateService``/the MCP breakdown fetch path, with no agent-to-agent
handoff, so it is structurally immune to the metric-id ping-pong bug that
used to hit the legacy ``orchestration.graph``/``orchestration.runner``
pipeline (``run_mission``) on multi-domain lookups. ``run_mission`` (the
"folding lookup_v1 into the coordinator as the L0/L1 fast path" TODO this
docstring used to reference) is now the documented fallback for what the
fast path doesn't cover yet (comparison intent) -- not an equally-live,
ambiguous second route. Deleting ``orchestration.graph``/``runner`` is
blocked on comparison-intent coverage -- see the doc above for why.
"""

from __future__ import annotations

from typing import Any

from seleric_swarm.coordinator.lookup_fast_path import run_lookup_fast_path
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
    Plain retrieval / comparison → the lookup fast path.

    If the LLM classifier itself is unavailable we route to lookup: the lookup
    entrypoint will then surface the same ``LLM_CLASSIFICATION_UNAVAILABLE``
    reason its own coordinator returns. Nothing here guesses intent from
    keywords.
    """
    from seleric_swarm.coordinator.intake.llm_classifier import classify_query_via_llm

    classification = await classify_query_via_llm(query, runtime=runtime, timezone=timezone, as_of=as_of)
    if classification is None:
        return "lookup"
    intents = set(classification.intents)
    return "swarm" if intents & _SWARM_INTENTS else "lookup"


async def _run_swarm(
    runtime: SwarmRuntime,
    *,
    query: str,
    timezone: str,
    as_of: str | None,
    session_id: str | None,
    request_id: str | None,
    mission_id: str | None,
    swarm_only: dict[str, Any],
) -> dict[str, Any]:
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
    cancellation = getattr(runtime, "cancellation", None)
    if mission_id and cancellation is not None and cancellation.is_requested(mission_id):
        from seleric_swarm.cancellation import MissionCancelledError

        raise MissionCancelledError(f"mission {mission_id} was cancelled")
    route = await route_for(runtime, query=query, timezone=timezone, as_of=as_of)
    if mission_id and cancellation is not None and cancellation.is_requested(mission_id):
        from seleric_swarm.cancellation import MissionCancelledError

        raise MissionCancelledError(f"mission {mission_id} was cancelled")
    try:
        from seleric_swarm.observability.flow import log_mission_step

        log_mission_step(
            mission_id,
            "mission_routed",
            route=route,
            query=query[:160],
            request_id=request_id,
        )
    except Exception:  # noqa: S110 - telemetry must never block mission routing
        pass
    if route == "lookup":
        fast_result = await run_lookup_fast_path(
            runtime,
            query=query,
            timezone=timezone,
            as_of=as_of,
            session_id=session_id,
            request_id=request_id,
            mission_id=mission_id,
        )
        if fast_result is not None:
            return {"route": "lookup", "result": fast_result.model_dump()}
        result = await run_mission(
            runtime,
            query=query,
            timezone=timezone,
            as_of=as_of,
            session_id=session_id,
            request_id=request_id,
            mission_id=mission_id,
        )
        if result.status == "failed" and result.error is not None and result.error.code == "ROUTING_UNSUPPORTED":
            # Classification is occasionally non-deterministic even at
            # temperature=0 (confirmed directly: the same query, re-classified
            # repeatedly, sometimes comes back without any swarm-triggering
            # intent) -- an executive_health/diagnostic ask can misroute to
            # "lookup" on one unlucky call and then get correctly rejected by
            # V1's stricter scope gate, surfacing an opaque routing error for
            # what should have been a normal answer. One retry of the whole
            # routing decision catches that without adding any cost to the
            # common (successful) path -- it only fires on this rare failure.
            retry_route = await route_for(runtime, query=query, timezone=timezone, as_of=as_of)
            if retry_route == "swarm":
                return await _run_swarm(
                    runtime,
                    query=query,
                    timezone=timezone,
                    as_of=as_of,
                    session_id=session_id,
                    request_id=request_id,
                    mission_id=mission_id,
                    swarm_only=swarm_only,
                )
        return {"route": "lookup", "result": result.model_dump()}

    return await _run_swarm(
        runtime,
        query=query,
        timezone=timezone,
        as_of=as_of,
        session_id=session_id,
        request_id=request_id,
        mission_id=mission_id,
        swarm_only=swarm_only,
    )
