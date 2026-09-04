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

# Retrieval / comparison verbs that are safe for the lookup fast path.
_LOOKUP_RE = (
    "what were",
    "what was",
    "what is",
    "how much",
    "how many",
    "compare",
    " vs ",
    "versus",
    "show me",
    "tell me",
    "get me",
)

_CAUSAL_MARKERS = (
    "why",
    "root cause",
    "diagnose",
    "what changed",
    "caused",
    "explain",
    "driver of",
    "driving",
    "drove",
    "change in",
    "drop in",
    "fall in",
    "decline in",
    "increase in",
    "rise in",
    "spike in",
    "what's behind",
    "what is behind",
    "attributed to",
)

# Degradation / anomaly stems — lookup verbs + these ⇒ swarm (not a plain retrieval).
_DEGRADATION_MARKERS = (
    "dropped",
    "drop",
    "fell",
    "falling",
    "declined",
    "decline",
    "decreased",
    "decrease",
    "increased",
    "increase",
    "rose",
    "rising",
    "spiked",
    "spike",
    "degraded",
    "degradation",
    "worsened",
    "broken",
    "crash",
    "slump",
)


def _has_degradation(q: str) -> bool:
    return any(m in q for m in _DEGRADATION_MARKERS)


async def route_for(runtime: SwarmRuntime, *, query: str) -> str:
    """Return "lookup" or "swarm" (cheap, deterministic, no LLM).

    Diagnostic / predictive / prescriptive / health → swarm.
    Plain retrieval / comparison phrasing stays on the lookup fast path.
    Lookup verbs combined with degradation/causal language still go to swarm
    (e.g. "show me how many orders dropped").
    """
    q = query.lower().strip()
    intake = set(intake_classify_intents(query))
    causal = any(k in q for k in _CAUSAL_MARKERS)
    degraded = _has_degradation(q)
    lookupish = bool(intake & {"lookup", "comparison"})
    lookup_verb = any(q.startswith(p) or p in q for p in _LOOKUP_RE)
    # Always swarm for investigation / forecast / action / health.
    if intake & {"predictive", "prescriptive", "executive_health"}:
        return "swarm"
    if causal or degraded or ("diagnostic" in intake and not lookupish):
        return "swarm"
    if lookupish or lookup_verb:
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
    mission_id: str | None = None,
    **swarm_only: Any,
) -> dict[str, Any]:
    """Classify, then dispatch to the lookup fast path or the dynamic swarm.

    Shared kwargs (``session_id`` / ``request_id`` / ``mission_id``) are forwarded
    to whichever route runs. ``**swarm_only`` (e.g. ``providers``, ``scenario_id``)
    applies only when the swarm route is taken and is ignored on the lookup route.
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
            mission_id=mission_id,
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
            mission_id=mission_id,
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
        mission_id=mission_id,
        **swarm_only,
    )
    return {"route": "swarm", "workflow": "swarm_v1", "result": swarm.as_dict()}
