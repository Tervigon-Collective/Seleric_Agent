"""Orders, stamps, validates and size-limits trace events, then hands them to the sinks (plan P1.5, D-36)."""

from __future__ import annotations

import logging
import math
import threading
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any

import numpy

from seleric_swarm.diagnosis.domain.canonical import canonical_json, fingerprint
from seleric_swarm.diagnosis.domain.events import SCHEMA_VERSION, Event
from seleric_swarm.diagnosis.ports.events import EventSink
from seleric_swarm.diagnosis.trace.events import InvalidEvent, required_keys

_log = logging.getLogger("seleric.diagnosis.trace")

DEFAULT_PAYLOAD_CAP_BYTES = 4096  # D-36


NON_FINITE_KEY = "_non_finite"


def clean_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The payload as plain JSON values. Statistics produce numpy scalars and sometimes NaN or infinity (a p-value of a
    degenerate test, a correlation of a constant series): numpy values become Python values, and a non-finite number
    becomes ``null`` with its path listed under ``_non_finite``, so the event keeps its shape and the fact is visible.
    Anything that is not a JSON value raises ``InvalidEvent`` (it is a bug in the producer)."""
    found: list[str] = []

    def walk(value: Any, path: str) -> Any:
        if isinstance(value, numpy.generic):
            value = value.item()
        if isinstance(value, numpy.ndarray):
            value = value.tolist()
        if isinstance(value, float) and not math.isfinite(value):
            found.append(path)
            return None
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        if isinstance(value, Mapping):
            return {str(k): walk(v, f"{path}.{k}" if path else str(k)) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [walk(v, f"{path}[{i}]") for i, v in enumerate(value)]
        raise InvalidEvent(f"payload value at {path or '<root>'} is a {type(value).__name__}, not a JSON value")

    clean = walk(dict(payload), "")
    if found:
        clean[NON_FINITE_KEY] = sorted(found)
    return dict(clean)


class MemorySink:
    """Keeps every event, in order. The run record is built from it."""

    def __init__(self) -> None:
        self.events: list[Event] = []

    def emit(self, event: Event) -> None:
        self.events.append(event)


class Emitter:
    """One per investigation. ``seq`` is assigned under a lock, so concurrent operators still produce one total order.

    An event whose payload would exceed ``cap_bytes`` is sent as a stub (size, digest, keys, a reference); the full
    payload stays in ``overflow`` under that reference for the run record, so the stream stays small and nothing is lost.
    A sink that raises is logged and counted in ``sink_failures``; the investigation goes on, because the stream is a
    view of the run, and the run record is built from a sink that does not fail (``MemorySink``)."""

    def __init__(
        self,
        investigation_id: str,
        clock: Callable[[], datetime],
        sinks: Sequence[EventSink],
        *,
        cap_bytes: int = DEFAULT_PAYLOAD_CAP_BYTES,
    ) -> None:
        self.investigation_id = investigation_id
        self._clock = clock
        self._sinks = list(sinks)
        self._cap = cap_bytes
        self._lock = threading.Lock()
        self._seq = 0
        self.overflow: dict[str, dict[str, Any]] = {}
        self.sink_failures: list[tuple[int, str]] = []

    def emit(
        self,
        kind: str,
        payload: Mapping[str, Any] | None = None,
        *,
        depth: int = 0,
        branch: int = 0,
        parent: int | None = None,
    ) -> Event:
        body = clean_payload(payload or {})
        missing = sorted(required_keys(kind) - body.keys())
        if missing:
            raise InvalidEvent(f"{kind} is missing payload keys {missing}")
        size = len(canonical_json(body).encode("utf-8"))
        with self._lock:
            self._seq += 1
            seq = self._seq
            if size > self._cap:
                ref = f"{self.investigation_id}:{seq}"
                self.overflow[ref] = body
                body = {"overflow": {"ref": ref, "bytes": size, "digest": fingerprint(body), "keys": sorted(body)}}
            event = Event(self.investigation_id, seq, self._clock(), depth, branch, parent, kind, SCHEMA_VERSION, body)
            for sink in self._sinks:
                try:
                    sink.emit(event)
                except Exception as exc:
                    self.sink_failures.append((seq, f"{type(exc).__name__}: {exc}"))
                    _log.warning("trace_sink_failed investigation=%s seq=%s", self.investigation_id, seq, exc_info=True)
        return event

    @property
    def last_seq(self) -> int:
        return self._seq
