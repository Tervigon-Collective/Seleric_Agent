"""One OpenTelemetry span per V3 mission (Sprint 3 Profile A).

Reuses the existing OTel wiring in ``observability/tracing.py`` — that
module already calls ``configure_opentelemetry(settings)`` at startup
(``bootstrap.py``/``main.py``), which sets the global ``TracerProvider``
(OTLP, Langfuse-compatible) when ``settings.otel_enabled``. This module
does not duplicate that setup; it only defines the one span every mission
run should open. When OTel isn't configured (``otel_enabled=False``, the
default), ``get_tracer`` returns a no-op tracer — spans are created and
immediately discarded, so this is always safe to call.

Distinct from that module's per-LLM-call ``traced_run`` (LangSmith-focused,
one span per model call) — this is the mission-level span the profile brief
asks for, a parent for whatever finer-grained spans exist underneath it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace

_TRACER_NAME = "seleric.v3.mission"


@contextmanager
def mission_trace(mission_id: str, **attributes: Any) -> Iterator[trace.Span]:
    """Open one span for a mission run. Non-string attribute values are
    stringified — OTel attributes must be primitives, and callers pass
    things like intent lists that aren't."""
    tracer = trace.get_tracer(_TRACER_NAME)
    # record_exception/set_status_on_exception default to True -- an
    # exception raised inside the block is captured on the span and
    # re-raised, no manual try/except needed here.
    with tracer.start_as_current_span("mission") as span:
        span.set_attribute("mission_id", mission_id)
        for key, value in attributes.items():
            if value is None:
                continue
            if not isinstance(value, (str, bool, int, float)):
                value = str(value)
            span.set_attribute(key, value)
        yield span
