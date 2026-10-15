"""Trace events into the live mission stream (``agent/progress.py::emit_progress``).

Each event goes out as the progress event ``diag.<kind>`` with the whole envelope under the single payload key
``investigation`` (D-35); ``conversations/privacy.py`` lets that one key through to the client. The payload is
engine-produced and size-limited by the emitter (D-36); nothing model-written travels in it."""

from __future__ import annotations

from seleric_swarm.agent.progress import emit_progress
from seleric_swarm.diagnosis.domain.events import Event

EVENT_PREFIX = "diag."
PAYLOAD_KEY = "investigation"


def summary_of(event: Event) -> str:
    """A short human line: the kind and, when the payload names one, its subject."""
    label = event.kind.replace("_", " ")
    for key in ("operator", "metric", "node", "hypothesis", "bound", "termination_reason", "reason"):
        value = event.payload.get(key)
        if isinstance(value, str) and value:
            return f"{label}: {value}"
    return label


class MissionEventSink:
    def __init__(self, mission_id: str) -> None:
        self._mission_id = mission_id

    def emit(self, event: Event) -> None:
        emit_progress(self._mission_id, EVENT_PREFIX + event.kind, summary_of(event), {PAYLOAD_KEY: event.to_dict()})
