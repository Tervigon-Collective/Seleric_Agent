"""The emitter of the investigation that is running, for code that cannot be handed one (plan P1.5b).

The legacy engine is thousands of lines of functions that call each other; threading an emitter through every
signature would rewrite them. Instead the tool activates its emitter for the duration of the call, and a place that
used to swallow a failure calls ``report_failure``. With no investigation active both are no-ops, so the engine's
unit tests and every other caller are unchanged. Context variables follow ``asyncio.to_thread``; they do not cross
into worker processes (those report through the result they return)."""

from __future__ import annotations

import logging
import traceback
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from seleric_swarm.diagnosis.domain.canonical import fingerprint
from seleric_swarm.diagnosis.domain.errors import DiagnosisError
from seleric_swarm.diagnosis.trace import events as kinds
from seleric_swarm.diagnosis.trace.emitter import Emitter

_log = logging.getLogger("seleric.diagnosis.trace")
_active: ContextVar[Emitter | None] = ContextVar("diagnosis_active_emitter", default=None)
_TRACEBACK_CHARS = 1500


@contextmanager
def activate_emitter(emitter: Emitter) -> Iterator[None]:
    token = _active.set(emitter)
    try:
        yield
    finally:
        _active.reset(token)


def active_emitter() -> Emitter | None:
    return _active.get()


def emit_active(kind: str, payload: Mapping[str, Any], *, parent: int | None = None) -> None:
    """Emit on the active investigation, if any. Instrumentation must not break an answer: a refused event is logged
    loudly with its traceback (it is a producer bug), never raised into the analysis."""
    emitter = _active.get()
    if emitter is None:
        return
    try:
        emitter.emit(kind, payload, parent=parent)
    except DiagnosisError:
        _log.error("trace_event_refused kind=%s", kind, exc_info=True)


def report_failure(operator: str, exc: BaseException, inputs: Mapping[str, Any] | None = None) -> None:
    """A step failed and the engine is about to carry on with a weaker fallback: say so in the trace."""
    detail = dict(inputs or {})
    emit_active(
        kinds.OPERATOR_FAILED,
        {
            "operator": operator, "error_type": type(exc).__name__, "message": str(exc)[:300],
            "inputs_digest": fingerprint(detail) if _jsonable(detail) else "",
            "traceback": "".join(traceback.format_exception(exc))[-_TRACEBACK_CHARS:],
        },
    )


def _jsonable(value: Mapping[str, Any]) -> bool:
    try:
        fingerprint(value)
    except (TypeError, ValueError):
        return False
    return True
