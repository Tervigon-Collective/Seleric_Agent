"""Visibility policy primitives for conversation data.

Payloads are delivered as-is (no value redaction). Access is enforced via
principal/workspace checks and a capability-limited public field allowlist.
"""

from __future__ import annotations

from typing import Any

from seleric_swarm.conversations.contracts import (
    ActivityEvent,
    EventVisibility,
    Principal,
)

# Public activity payloads are deliberately capability-limited. In particular,
# reasoning, prompts, scratchpads, traces, and model messages are not delivered.
_PUBLIC_EVENT_FIELDS = frozenset(
    {
        "agent",
        "agent_id",
        "artifact_id",
        "artifact_type",
        "claim_id",
        "confidence",
        "decision",
        "delta",
        "description",
        "duration_ms",
        "error",
        "error_code",
        "evidence_id",
        "evidence_ids",
        "family",
        "from_agent",
        "intent",
        "investigation",  # diagnosis trace envelope: engine-built, size-capped, no model text (D-35)
        "mapping_version",
        "message",
        "message_id",
        "mission_id",
        "name",
        "note",
        "reason_code",
        "route",
        "source_kind",
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


def redact_text(value: str) -> str:
    """Identity: values are not masked (kept for call-site compatibility)."""

    return value


def redact_data(value: Any, *, key: object = "") -> Any:
    """Identity: values are not masked (kept for call-site compatibility)."""

    del key  # retained for API compatibility with prior call sites
    return value


def can_view_event(event: ActivityEvent, principal: Principal) -> bool:
    """Return whether ``principal`` may receive an activity event."""

    if not principal.authenticated or not principal.can_access_workspace(event.workspace_id):
        return False
    if event.visibility is EventVisibility.INTERNAL:
        return principal.is_internal
    if event.visibility is EventVisibility.ADMIN:
        return principal.is_admin
    return event.owner_user_id is None or principal.owns(
        workspace_id=event.workspace_id,
        user_id=event.owner_user_id,
    )


def event_for_principal(
    event: ActivityEvent, principal: Principal
) -> ActivityEvent | None:
    """Filter an event to the public field allowlist at the delivery boundary."""

    if not can_view_event(event, principal):
        return None
    public_payload = {
        key: value for key, value in event.payload.items() if key in _PUBLIC_EVENT_FIELDS
    }
    return event.model_copy(
        update={
            "title": event.title,
            "summary": event.summary,
            "payload": public_payload,
            "metadata": {},
        }
    )
