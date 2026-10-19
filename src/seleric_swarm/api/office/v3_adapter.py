"""V3 mission -> office raw dict, for the Office UI's read path.

``api/office/normalize.py::build_office_snapshot`` renders ``route=v3``
missions with the V3-native single-agent roster (``seleric_agent`` walking
capability stations) while old ``route=swarm`` records keep the retired
14-character roster. This adapter translates a V3 ``Mission`` + its
``Artifact``s into the raw-dict shape ``build_office_snapshot`` consumes:
lifecycle events plus one ``tool_completed`` beat per artifact bucket, so
the timeline shows the loop's own tool walk (fetch → analyze → diagnose →
forecast) instead of a single coordinator placeholder.
"""

from __future__ import annotations

import logging
from typing import Any

from seleric_swarm.api.v3_state import get_v3_artifact_store, get_v3_mission_store
from seleric_swarm.state.missions import Mission

_log = logging.getLogger("seleric.api.office.v3")

# V3 Artifact.artifact_type (agent/artifacts.py) -> the closest swarm_v2
# artifact bucket normalize.py already knows how to render/attribute.
_ARTIFACT_TYPE_TO_BUCKET = {
    "evidence": "evidence",
    "finding": "hypothesis",
    "causal": "causal",
    "prediction": "prediction",
    "forecast": "prediction",
    "forecast_input": "evidence",
}


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return str(value.isoformat()).replace("+00:00", "Z")
    text = str(value).strip()
    return text or None


def _mission_events(
    mission: Mission,
    *,
    has_evidence: bool,
    buckets: dict[str, list[str]],
) -> list[dict[str, Any]]:
    from seleric_swarm.api.office.normalize import V3_AGENT_ID

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
    # One timeline beat per capability the loop demonstrably used, so the
    # office shows the tool walk even though V3 persists no per-call event
    # log (live tool calls stream as agent.tool_* while running).
    tool_beats: list[tuple[str, str]] = []
    if has_evidence or buckets.get("evidence"):
        tool_beats.append(("query_metrics", "Fetching metric data"))
    if buckets.get("hypothesis"):
        tool_beats.append(("analyze", "Analysing the fetched data"))
    if buckets.get("causal"):
        tool_beats.append(("estimate_effect", "Estimating causal effect"))
    if buckets.get("prediction"):
        tool_beats.append(("forecast_metrics", "Forecasting"))
    for tool, summary in tool_beats:
        events.append(
            {
                "kind": "tool_completed",
                "family": "agent",
                "mission_id": mission.mission_id,
                "seq": seq,
                "ts": mission.updated_at.isoformat().replace("+00:00", "Z"),
                "route": "v3",
                "agent": V3_AGENT_ID,
                "tool": tool,
                "success": True,
                "summary": summary,
            }
        )
        seq += 1
    if has_evidence and not tool_beats:
        events.append(
            {
                "kind": "task_wave_executed",
                "family": "mission",
                "mission_id": mission.mission_id,
                "seq": seq,
                "ts": mission.updated_at.isoformat().replace("+00:00", "Z"),
                "route": "v3",
                "mission_lead": V3_AGENT_ID,
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
                "final_response": mission.final_response or "",
                "status": mission.status,
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
    # One chart per (chart_type, metrics) set — the most recent. A mission that revised,
    # reset an answer, or was retried re-charts with different evidence or intent;
    # only the latest chart for that metric set should render on the answer.
    latest_chart: dict[Any, tuple[Any, str]] = {}
    for artifact in artifacts:
        if getattr(artifact, "artifact_type", None) != "chart_spec" or not isinstance(artifact.payload, dict):
            continue
        payload = artifact.payload
        # The spec always names a canonical form (see chart_vocabulary). Only a
        # payload that somehow lost it falls back here, and it says so.
        chart_type = payload.get("chart_type")
        if not chart_type:
            _log.warning("chart_spec %s has no chart_type; defaulting to bar", artifact.id)
            chart_type = "bar"
        metrics_key = tuple(sorted(payload.get("metrics") or []))
        key = (chart_type, metrics_key) if metrics_key else frozenset(getattr(artifact, "evidence_ids", None) or [artifact.id])
        prior = latest_chart.get(key)
        if prior is None or artifact.created_at >= prior[0].created_at:
            latest_chart[key] = (artifact, str(chart_type))
    charts = [
        {
            "chart_type": chart_type,
            "artifact_id": artifact.id,
            "data": artifact.payload,
        }
        for (artifact, chart_type) in sorted(latest_chart.values(), key=lambda pair: pair[0].created_at)
    ]

    return {
        "route": "v3",
        "workflow": "seleric_agent",
        "mission_id": mission.mission_id,
        "query": mission.query,
        "status": mission.status,
        "mission_lead": "seleric_agent",
        "initial_mission_lead": "seleric_agent",
        "leadership_epoch": 0,
        "workspace_id": mission.workspace_id,
        "owner_user_id": mission.owner_user_id,
        "thread_id": mission.thread_id,
        "run_id": mission.run_id,
        "events": _mission_events(mission, has_evidence=bool(evidence), buckets=buckets),
        "artifacts": buckets,
        "evidence": evidence,
        "charts": charts,
        "final_response": mission.final_response or "",
        "error_code": mission.error_code,
        "limitations": [] if mission.status != "failed" else [mission.final_response or "failed"],
        "trace": {"request_id": mission.run_id, "session_id": mission.thread_id},
    }
