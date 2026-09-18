"""V3 mission -> swarm_v2-shaped raw dict, for the Office UI's read path.

``api/office/normalize.py::build_office_snapshot`` and the frontend that
consumes it (``office-ui/``) are entirely swarm_v2-shaped (``mission_lead``,
``leadership_epoch``, ``handoff_history``, typed artifact buckets) and have
no native concept of a V3 mission (see
``docs/refactor/01_PROFILE_RUNTIME.md`` Key risks — this gap wasn't planned
anywhere in the refactor docs before 2026-09-18). Building a real V3-native
office view is bigger than Sprint 1/2 scaffolding warrants while no
toolset exists to produce an interesting V3 mission to render.

This adapter is the pragmatic middle ground: translate a V3 ``Mission`` +
its ``Artifact``s into the same raw-dict shape ``build_office_snapshot``
already knows how to render, reusing that logic instead of forking it. The
one V3 concept it fakes is the mission lead — V3 has no leadership handoff,
so every V3 mission reports ``"coordinator"`` (the one office-ui agent node
that isn't a swarm_v2 domain/specialist) as its lead. This is a stand-in,
not a real mapping — a genuine V3 view (showing the single-agent loop's own
tool calls) is still tracked as exit criterion 5 in that profile brief.
"""

from __future__ import annotations

from typing import Any

from seleric_swarm.api.v3_state import get_v3_artifact_store, get_v3_mission_store
from seleric_swarm.state.missions import Mission

# V3 Artifact.artifact_type (agent/artifacts.py) -> the closest swarm_v2
# artifact bucket normalize.py already knows how to render/attribute.
_ARTIFACT_TYPE_TO_BUCKET = {
    "evidence": "evidence",
    "finding": "hypothesis",
    "causal": "causal",
    "prediction": "prediction",
}


def _mission_events(mission: Mission) -> list[dict[str, Any]]:
    kind = {
        "running": "mission_created",
        "completed": "mission_completed",
        "partial": "mission_partial",
        "failed": "mission_failed",
        "cancelled": "mission_cancelled",
    }[mission.status]
    return [
        {
            "kind": kind,
            "family": "mission",
            "mission_id": mission.mission_id,
            "seq": 1,
            "ts": mission.updated_at.isoformat().replace("+00:00", "Z"),
            "route": "v3",
        }
    ]


def v3_raw_snapshot(mission_id: str) -> dict[str, Any] | None:
    """The office gateway's ``_raw()`` fallback for a V3 mission id.
    Returns ``None`` when ``mission_id`` isn't a V3 mission at all (the
    gateway then treats it as a genuine 404, same as today)."""
    mission = get_v3_mission_store().get(mission_id)
    if mission is None:
        return None

    artifacts = get_v3_artifact_store().list_for_mission(mission_id)
    buckets: dict[str, list[str]] = {}
    for artifact in artifacts:
        bucket = _ARTIFACT_TYPE_TO_BUCKET.get(artifact.artifact_type)
        if bucket:
            buckets.setdefault(bucket, []).append(artifact.id)

    return {
        "route": "v3",
        "workflow": "seleric_agent",
        "mission_id": mission.mission_id,
        "query": mission.query,
        "status": mission.status,
        "mission_lead": "coordinator",
        "initial_mission_lead": "coordinator",
        "leadership_epoch": 0,
        "workspace_id": mission.workspace_id,
        "owner_user_id": mission.owner_user_id,
        "events": _mission_events(mission),
        "artifacts": buckets,
        "final_response": mission.final_response or "",
        "error_code": mission.error_code,
        "trace": {"request_id": mission.run_id, "session_id": mission.thread_id},
    }
