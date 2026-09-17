"""Version 1 mapping from legacy mission events to public activity events."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from seleric_swarm.conversations.contracts import ActivityEvent, EventVisibility, Run
from seleric_swarm.coordinator.observability.events import canonical_kind

MAPPING_VERSION = "1"

CANONICAL_EVENT_TYPES = frozenset(
    {
        "run.started",
        "intent.resolved",
        "plan.created",
        "agent.started",
        "agent.completed",
        "agent.handoff",
        "tool.started",
        "tool.completed",
        "tool.failed",
        "evidence.added",
        "decision.recorded",
        "warning.created",
        "artifact.created",
        "answer.delta",
        "answer.completed",
        "run.completed",
        "run.failed",
    }
)

_EXACT_KIND_MAP = {
    "mission_accepted": "run.started",
    "mission_created": "run.started",
    "query_normalized": "intent.resolved",
    "decomposition_created": "intent.resolved",
    "decomposition_refined": "intent.resolved",
    "task_plan_created": "plan.created",
    "plan_created": "plan.created",
    "task_specialists_activated": "agent.started",
    "leadership_transfer": "agent.handoff",
    "leadership_rejected": "warning.created",
    "claim_proposed": "evidence.added",
    "claim_validated": "decision.recorded",
    "claim_challenged": "warning.created",
    "claim_rejected": "warning.created",
    "skeptic_gate": "decision.recorded",
    "skeptic_pass": "decision.recorded",
    "skeptic_revise": "warning.created",
    "skeptic_reject": "warning.created",
    "remediation_planned": "plan.created",
    "remediation_activated": "agent.started",
    "remediation_round_done": "agent.completed",
    "specialist_error": "tool.failed",
    "specialist_skipped_policy": "warning.created",
    "observed": "agent.completed",
    "observe_skipped": "warning.created",
    "anomaly_done": "agent.completed",
    "diagnostic_done": "agent.completed",
    "prediction_done": "agent.completed",
    "strategy_done": "agent.completed",
    "mission_completed": "run.completed",
    "mission_partial": "run.completed",
    "mission_failed": "run.failed",
    "mission_budget_exhausted": "warning.created",
}

_SAFE_SOURCE_FIELDS = frozenset(
    {
        "agent",
        "agent_id",
        "artifact_id",
        "artifact_type",
        "claim_id",
        "confidence",
        "decision",
        "description",
        "duration_ms",
        "error",
        "error_code",
        "evidence_id",
        "evidence_ids",
        "family",
        "from_agent",
        "intent",
        "legacy_kind",
        "message",
        "mission_id",
        "name",
        "reason_code",
        "route",
        "specialist",
        "status",
        "summary",
        "task_id",
        "title",
        "to_agent",
        "tool",
        "tool_name",
        "warning",
        "wave",
        "workflow_name",
        "workflow_version",
    }
)


def event_type_for_mission_kind(kind: str) -> str | None:
    """Return the public type for a legacy mission event kind."""

    original = kind.strip().lower()
    canonical = canonical_kind(original)
    mapped = _EXACT_KIND_MAP.get(original) or _EXACT_KIND_MAP.get(canonical)
    if mapped:
        return mapped
    if original.startswith("tool_"):
        if any(term in original for term in ("failed", "error")):
            return "tool.failed"
        if any(term in original for term in ("completed", "done", "result")):
            return "tool.completed"
        return "tool.started"
    if original.startswith("artifact_"):
        return "artifact.created"
    if original.startswith("specialist_"):
        return "agent.completed" if original.endswith(("_done", "_completed")) else "agent.started"
    return None


def _safe_payload(source: dict[str, Any], kind: str) -> dict[str, Any]:
    payload = {
        key: value
        for key, value in source.items()
        if key in _SAFE_SOURCE_FIELDS and key not in {"title", "summary"}
    }
    payload["source_kind"] = kind
    payload["mapping_version"] = MAPPING_VERSION
    return payload


def map_mission_event(event: dict[str, Any], run: Run) -> ActivityEvent | None:
    """Map one legacy event without forwarding private reasoning fields."""

    kind = str(event.get("kind") or event.get("legacy_kind") or "").strip().lower()
    event_type = event_type_for_mission_kind(kind)
    if event_type is None:
        return None
    created_at = event.get("ts")
    if not isinstance(created_at, (str, datetime)):
        created_at = run.created_at
    return ActivityEvent(
        id=f"event_mission_{run.id}_{int(event.get('seq') or 0)}_{kind}",
        thread_id=run.thread_id,
        workspace_id=run.workspace_id,
        run_id=run.id,
        owner_user_id=run.requested_by_user_id,
        event_type=event_type,
        actor_type="agent" if event_type.startswith(("agent.", "tool.")) else "system",
        actor_id=str(event.get("specialist") or event.get("agent_id") or "") or None,
        title=str(event.get("title") or "") or None,
        summary=str(event.get("summary") or event.get("message") or "") or None,
        visibility=EventVisibility.USER,
        evidence_ids=[str(item) for item in event.get("evidence_ids", [])],
        payload=_safe_payload(event, kind),
        metadata={"source": "mission_event", "mapping_version": MAPPING_VERSION},
        duration_ms=event.get("duration_ms") if isinstance(event.get("duration_ms"), int) else None,
        created_at=cast(datetime, created_at),
    )
