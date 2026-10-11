"""Context providers (M4.1): calendar, incidents and data health for diagnosis."""

from seleric_swarm.context.calendar import CalendarProvider
from seleric_swarm.context.changes import ChangeLogProvider, EventReader, change_sources
from seleric_swarm.context.health import FreshnessHealth
from seleric_swarm.context.incidents import IncidentProvider
from seleric_swarm.context.model import (
    AsyncContextProvider,
    ContextEvent,
    ContextProvider,
    ContextWindow,
    DataHealth,
    HealthSource,
)
from seleric_swarm.context.registry import (
    agather_events,
    disabled_ids,
    gather_events,
    gather_health,
)
from seleric_swarm.context.snapshots import SnapshotHealth

__all__ = [
    "AsyncContextProvider",
    "CalendarProvider",
    "ChangeLogProvider",
    "ContextEvent",
    "ContextProvider",
    "ContextWindow",
    "DataHealth",
    "EventReader",
    "FreshnessHealth",
    "HealthSource",
    "IncidentProvider",
    "SnapshotHealth",
    "agather_events",
    "change_sources",
    "disabled_ids",
    "gather_events",
    "gather_health",
]
