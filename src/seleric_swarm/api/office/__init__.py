"""Seleric AI Office — read-only spatial UI gateway.

This package is a thin projection layer. It never orchestrates anything: it
reads persisted mission state (``store.get_raw`` + ``store.list_events``) and
normalizes the control-plane events into a UI-friendly ``SwarmUIEvent`` stream
plus an ``OfficeSnapshot`` the spatial front-end can hydrate from.

See ``docs/office-ui/05_EVENT_PROTOCOL.md`` and
``docs/office-ui/06_REALTIME_ARCHITECTURE.md``.
"""

from seleric_swarm.api.office.normalize import (
    OFFICE_AGENTS,
    build_office_snapshot,
    normalize_events,
)
from seleric_swarm.api.office.registry import (
    known_mission_ids,
    register_mission,
)

__all__ = [
    "OFFICE_AGENTS",
    "build_office_snapshot",
    "known_mission_ids",
    "normalize_events",
    "register_mission",
]
