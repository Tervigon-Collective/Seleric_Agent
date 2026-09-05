"""Shared mission status helpers — one terminal set for API / runner / tests."""

from __future__ import annotations

TERMINAL_STATUSES = frozenset(
    {
        "completed",
        "prototype_completed",
        "partial",
        "blocked",
        "failed",
        "cancelled",
    }
)

# Statuses allowed on MissionResult / store typed views
TYPED_MISSION_STATUSES = frozenset(
    {
        "completed",
        "prototype_completed",
        "partial",
        "blocked",
        "failed",
        "running",
        "cancelled",
    }
)


def is_terminal_status(status: str | None) -> bool:
    return str(status or "") in TERMINAL_STATUSES


def coerce_typed_status(status: str | None, *, default: str = "partial") -> str:
    """Map a swarm status into a value accepted by MissionResult.status."""
    s = str(status or default)
    if s in TYPED_MISSION_STATUSES:
        return s
    return default
