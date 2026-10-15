"""One id on everything an investigation touches (plan P1.6, D-21): log lines, Langfuse spans, artifacts, events.

``investigation_scope`` binds the id for the code running inside it, in both logging paths the service uses
(structlog context variables, and an attribute on every stdlib ``LogRecord``). ``with_investigation`` adds it to the
metadata dicts that ``observability.tracing.traced_span`` / ``mission_metadata`` take (plain key, so it does not collide
with Langfuse 4.x's reserved ``tags=``/``session_id=`` arguments)."""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

import structlog

METADATA_KEY = "investigation_id"
_current: ContextVar[str] = ContextVar("diagnosis_investigation_id", default="")
_factory_installed = False


def current_investigation_id() -> str:
    """The id bound for the running code, or an empty string outside any investigation."""
    return _current.get()


@contextmanager
def investigation_scope(investigation_id: str) -> Iterator[None]:
    install_log_record_attribute()
    token = _current.set(investigation_id)
    try:
        with structlog.contextvars.bound_contextvars(**{METADATA_KEY: investigation_id}):
            yield
    finally:
        _current.reset(token)


def install_log_record_attribute() -> None:
    """Give every stdlib log record an ``investigation_id`` attribute (empty outside an investigation), so any
    formatter or handler can print it. Idempotent."""
    global _factory_installed
    if _factory_installed:
        return
    previous = logging.getLogRecordFactory()

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = previous(*args, **kwargs)
        record.investigation_id = _current.get()
        return record

    logging.setLogRecordFactory(factory)
    _factory_installed = True


def with_investigation(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """``metadata`` plus the current investigation id (unchanged outside an investigation)."""
    out = dict(metadata)
    current = _current.get()
    if current:
        out[METADATA_KEY] = current
    return out


def tag_current_span(investigation_id: str) -> None:
    """Put the id on the active OpenTelemetry span (which Langfuse receives), under the plain key and under
    Langfuse's metadata convention. Best effort: tracing that is off or absent is not an error."""
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        if span.is_recording():
            span.set_attribute(METADATA_KEY, investigation_id)
            span.set_attribute(f"langfuse.observation.metadata.{METADATA_KEY}", investigation_id)
    except Exception:
        logging.getLogger("seleric.diagnosis.trace").debug("span_tag_failed", exc_info=True)
