"""Context providers (M4.1): calendar, incidents and data health for diagnosis."""

from seleric_swarm.context.calendar import CalendarProvider
from seleric_swarm.context.health import FreshnessHealth
from seleric_swarm.context.incidents import IncidentProvider
from seleric_swarm.context.model import (
    ContextEvent,
    ContextProvider,
    ContextWindow,
    DataHealth,
    HealthSource,
)
from seleric_swarm.context.registry import disabled_ids, gather_events, gather_health

__all__ = [
    "CalendarProvider",
    "ContextEvent",
    "ContextProvider",
    "ContextWindow",
    "DataHealth",
    "FreshnessHealth",
    "HealthSource",
    "IncidentProvider",
    "disabled_ids",
    "gather_events",
    "gather_health",
]
