"""Where trace events go."""

from __future__ import annotations

from typing import Protocol

from seleric_swarm.diagnosis.domain.events import Event


class EventSink(Protocol):
    def emit(self, event: Event) -> None:
        """Take one event. Called in ``seq`` order. May raise; the emitter records the failure and goes on."""
        ...
