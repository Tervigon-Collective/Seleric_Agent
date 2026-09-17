"""Redaction and visibility policy primitives for conversation data."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from seleric_swarm.conversations.contracts import (
    ActivityEvent,
    EventVisibility,
    Principal,
)

REDACTED = "[REDACTED]"

_SENSITIVE_KEYS = (
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "password",
    "secret",
    "token",
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
        "description",
        "duration_ms",
        "error",
        "error_code",
        "evidence_id",
        "evidence_ids",
        "family",
        "from_agent",
        "intent",
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

_TEXT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"(?i)\b(bearer)\s+[a-z0-9._~+/=-]{8,}"),
        rf"\1 {REDACTED}",
    ),
    (
        re.compile(
            r"(?i)\b(api[_ -]?key|password|secret|token)\b"
            r"(\s*(?:=|:|is)\s*)"
            r"(?!\[REDACTED\])([^\s,;]{4,})"
        ),
        rf"\1\2{REDACTED}",
    ),
    (
        re.compile(r"(?i)\b(sk-[a-z0-9_-]{12,})\b"),
        REDACTED,
    ),
    (
        re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),
        "[REDACTED_EMAIL]",
    ),
    (
        re.compile(r"(?<!\w)(?:\+?\d[\d .()-]{7,}\d)(?!\w)"),
        "[REDACTED_PHONE]",
    ),
)


def _is_sensitive_key(key: object) -> bool:
    lowered = str(key).lower()
    return any(fragment in lowered for fragment in _SENSITIVE_KEYS)


def redact_text(value: str) -> str:
    """Mask common credentials and direct PII embedded in arbitrary text."""

    redacted = value
    for pattern, replacement in _TEXT_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def redact_data(value: Any, *, key: object = "") -> Any:
    """Recursively redact sensitive keys and free-text values."""

    if _is_sensitive_key(key):
        return REDACTED
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {item_key: redact_data(item, key=item_key) for item_key, item in value.items()}
    if isinstance(value, list):
        return [redact_data(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_data(item) for item in value)
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
    """Filter an event and redact its payload at the delivery boundary."""

    if not can_view_event(event, principal):
        return None
    public_payload = {
        key: value for key, value in event.payload.items() if key in _PUBLIC_EVENT_FIELDS
    }
    return event.model_copy(
        update={
            "title": redact_text(event.title) if event.title else None,
            "summary": redact_text(event.summary) if event.summary else None,
            "payload": redact_data(public_payload),
            "metadata": {},
        }
    )
