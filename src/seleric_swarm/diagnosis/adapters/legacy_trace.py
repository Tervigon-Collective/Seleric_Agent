"""Trace events for one call of the legacy diagnosis tool (plan P1.5b, P1.6b).

The tool still runs through the semantic layer's ``RunContext``, not the ports, so what it produces is a *live trace*
(events and a correlation id), not a replayable record. The trace is a view: a failure to emit never changes or blocks
the analysis, and is counted in the closing event."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from seleric_swarm.diagnosis.adapters.mission_events import MissionEventSink
from seleric_swarm.diagnosis.domain.canonical import fingerprint
from seleric_swarm.diagnosis.domain.errors import DiagnosisError, GraphRejected
from seleric_swarm.diagnosis.domain.events import Event
from seleric_swarm.diagnosis.domain.graph import LoadedGraph
from seleric_swarm.diagnosis.domain.report import DiagnosisReport
from seleric_swarm.diagnosis.graph.live import build_live_graph
from seleric_swarm.diagnosis.ports.events import EventSink
from seleric_swarm.diagnosis.trace import events as kinds
from seleric_swarm.diagnosis.trace.active import activate_emitter
from seleric_swarm.diagnosis.trace.correlation import investigation_scope, tag_current_span
from seleric_swarm.diagnosis.trace.emitter import DEFAULT_PAYLOAD_CAP_BYTES, Emitter, MemorySink
from seleric_swarm.diagnosis.trace.graph_events import emit_graph_loaded
from seleric_swarm.diagnosis.trace.report_events import emit_report_events

_log = logging.getLogger("seleric.diagnosis.trace")
_current: ContextVar[LegacyTrace | None] = ContextVar("diagnosis_legacy_trace", default=None)
# The operator the running task is inside. Context-local, because the tool runs fetches concurrently.
_parent: ContextVar[int | None] = ContextVar("diagnosis_parent_event", default=None)


def current_trace() -> LegacyTrace | None:
    return _current.get()


@dataclass
class OperatorScope:
    """Filled in by the code inside ``LegacyTrace.operator`` so the closing event can say what it produced."""

    result: Mapping[str, Any] = field(default_factory=dict)


class LegacyTrace:
    def __init__(
        self,
        mission_id: str,
        outcome: str,
        scope: Mapping[str, Any],
        *,
        clock: Callable[[], datetime] | None = None,
        sinks: tuple[EventSink, ...] | None = None,
    ) -> None:
        self.investigation_id = f"inv_{uuid.uuid4().hex[:12]}"
        self._outcome, self._scope = outcome, dict(scope)
        self.memory = MemorySink()
        live = (MissionEventSink(mission_id),) if sinks is None and mission_id else (sinks or ())
        self.emitter = Emitter(
            self.investigation_id, clock or (lambda: datetime.now(UTC)), [self.memory, *live],
            cap_bytes=DEFAULT_PAYLOAD_CAP_BYTES,
        )
        self._queries = 0
        self.instrumentation_errors = 0

    # ------------------------------------------------------------------ plumbing
    def _emit(self, kind: str, payload: Mapping[str, Any], *, parent: int | None = None) -> int | None:
        try:
            return self.emitter.emit(kind, payload, parent=parent if parent is not None else _parent.get()).seq
        except DiagnosisError:
            self.instrumentation_errors += 1
            _log.error("trace_event_refused kind=%s", kind, exc_info=True)
            return None

    @contextmanager
    def activate(self) -> Iterator[LegacyTrace]:
        token = _current.set(self)
        try:
            with investigation_scope(self.investigation_id), activate_emitter(self.emitter):
                tag_current_span(self.investigation_id)
                yield self
        finally:
            _current.reset(token)

    # ------------------------------------------------------------------ events
    def started(self) -> None:
        self._emit(kinds.INVESTIGATION_STARTED, {"outcome": self._outcome, "scope": self._scope})

    def window(self, start: date, end: date, *, baseline: int = 0) -> None:
        self._emit(kinds.WINDOW_RESOLVED, {"start": start.isoformat(), "end": end.isoformat(), "baseline_days": baseline})

    def graph(self, definitions: Mapping[str, Mapping[str, Any]]) -> LoadedGraph | None:
        """Load the reviewed graph against the live catalogue and show what was set aside. Returns what it loaded, or
        None when the graph was rejected (the rejection is on the trace)."""
        try:
            loaded = build_live_graph(definitions)
        except GraphRejected as exc:
            self._emit(kinds.GRAPH_ISSUE, {"code": exc.reason_code, "subject": "graph", "detail": exc.detail})
            return None
        try:
            emit_graph_loaded(self.emitter, loaded)
        except DiagnosisError:
            self.instrumentation_errors += 1
            _log.error("trace_graph_events_refused", exc_info=True)
        return loaded

    def query(self, metric: str, *, cache_hit: bool) -> None:
        self._queries += 1
        self._emit(kinds.QUERY_ISSUED, {"metric": metric, "cache_hit": cache_hit})

    @contextmanager
    def operator(self, name: str, inputs: Mapping[str, Any] | None = None) -> Iterator[OperatorScope]:
        scope = OperatorScope()
        queries_before, began = self._queries, time.monotonic()
        seq = self._emit(kinds.OPERATOR_STARTED, {"operator": name, "inputs": dict(inputs or {})})
        token = _parent.set(seq if seq is not None else _parent.get())
        try:
            yield scope
        except Exception as exc:
            self._emit(kinds.OPERATOR_FAILED, {
                "operator": name, "error_type": type(exc).__name__, "message": str(exc)[:300],
                "inputs_digest": fingerprint(dict(inputs or {})) if _plain(inputs) else "",
            })
            raise
        else:
            self._emit(kinds.OPERATOR_FINISHED, {
                "operator": name, "query_count": self._queries - queries_before,
                "duration_ms": round((time.monotonic() - began) * 1000),
                "result_digest": fingerprint(dict(scope.result)) if _plain(scope.result) else "",
            })
        finally:
            _parent.reset(token)

    def report(self, report: DiagnosisReport) -> None:
        """One resolved hypothesis per driver the engine tested, with its refuters, and the unexplained share."""
        emit_report_events(lambda kind, payload, parent: self._emit(kind, payload, parent=parent), report)

    def finished(self, reason: str) -> None:
        self._emit(kinds.INVESTIGATION_FINISHED, {
            "termination_reason": reason, "events": self.emitter.last_seq, "sink_failures": len(self.emitter.sink_failures),
            "instrumentation_errors": self.instrumentation_errors,
        })


def _plain(value: Mapping[str, Any] | None) -> bool:
    try:
        fingerprint(dict(value or {}))
    except (TypeError, ValueError):
        return False
    return True


class TraceForward:
    """An event sink that shows a run's events on a live trace: the stages, hypotheses, refuters and failures of the
    run being recorded appear in the investigation panel under whatever the tool is inside. The run's own start, window
    and finish are the trace's already, so they are not repeated; a parent is mapped to where its event landed."""

    _SKIP = frozenset({kinds.INVESTIGATION_STARTED, kinds.WINDOW_RESOLVED, kinds.INVESTIGATION_FINISHED})

    def __init__(self, trace: LegacyTrace) -> None:
        self._trace = trace
        self._landed: dict[int, int | None] = {}

    def emit(self, event: Event) -> None:
        if event.kind in self._SKIP:
            return
        parent = self._landed.get(event.parent_id) if event.parent_id is not None else None
        self._landed[event.seq] = self._trace._emit(event.kind, event.payload, parent=parent)
