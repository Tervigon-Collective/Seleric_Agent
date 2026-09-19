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


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return str(value.isoformat()).replace("+00:00", "Z")
    text = str(value).strip()
    return text or None


def _mission_events(mission: Mission, *, has_evidence: bool) -> list[dict[str, Any]]:
    created = {
        "kind": "mission_created",
        "family": "mission",
        "mission_id": mission.mission_id,
        "seq": 1,
        "ts": mission.created_at.isoformat().replace("+00:00", "Z"),
        "route": "v3",
    }
    events: list[dict[str, Any]] = [created]
    seq = 2
    if has_evidence:
        events.append(
            {
                "kind": "task_wave_executed",
                "family": "mission",
                "mission_id": mission.mission_id,
                "seq": seq,
                "ts": mission.updated_at.isoformat().replace("+00:00", "Z"),
                "route": "v3",
                "mission_lead": "coordinator",
            }
        )
        seq += 1
    if mission.status != "running":
        kind = {
            "completed": "mission_completed",
            "partial": "mission_partial",
            "failed": "mission_failed",
            "cancelled": "mission_cancelled",
        }.get(mission.status, "mission_completed")
        events.append(
            {
                "kind": kind,
                "family": "mission",
                "mission_id": mission.mission_id,
                "seq": seq,
                "ts": mission.updated_at.isoformat().replace("+00:00", "Z"),
                "route": "v3",
            }
        )
    return events


def _evidence_rows(artifacts: list[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for artifact in artifacts:
        if getattr(artifact, "artifact_type", None) != "evidence":
            continue
        payload = artifact.payload if isinstance(artifact.payload, dict) else {}
        start = _iso(payload.get("period_start"))
        end = _iso(payload.get("period_end")) or start
        rows.append(
            {
                "evidence_id": artifact.id,
                "metric_or_fact": str(payload.get("metric_id") or "Evidence"),
                "value": payload.get("value"),
                "unit": payload.get("unit"),
                "time_range": {"start": start, "end": end},
                "source": "seleric-mcp",
                "freshness": _iso(payload.get("fetched_at")),
                "dimensions": payload.get("dimensions") or {},
                "provenance": {"artifact_id": artifact.id},
            }
        )
    return rows


def v3_raw_snapshot(mission_id: str) -> dict[str, Any] | None:
    """The office gateway's ``_raw()`` fallback for a V3 mission id.
    Returns ``None`` when ``mission_id`` isn't a V3 mission at all (the
    gateway then treats it as a genuine 404, same as today)."""
    mission = get_v3_mission_store().get(mission_id)
    if mission is None:
        return None

    artifacts = get_v3_artifact_store().list_for_mission(mission_id)
    buckets: dict[str, list[str]] = {
        "evidence": [],
        "anomaly": [],
        "hypothesis": [],
        "causal": [],
        "prediction": [],
        "strategy": [],
        "skeptic": [],
    }
    for artifact in artifacts:
        bucket = _ARTIFACT_TYPE_TO_BUCKET.get(artifact.artifact_type)
        if bucket:
            buckets.setdefault(bucket, []).append(artifact.id)
    evidence = _evidence_rows(artifacts)

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
        "thread_id": mission.thread_id,
        "run_id": mission.run_id,
        "events": _mission_events(mission, has_evidence=bool(evidence)),
        "artifacts": buckets,
        "evidence": evidence,
        "final_response": mission.final_response or "",
        "error_code": mission.error_code,
        "limitations": [] if mission.status != "failed" else [mission.final_response or "failed"],
        "trace": {"request_id": mission.run_id, "session_id": mission.thread_id},
    }
