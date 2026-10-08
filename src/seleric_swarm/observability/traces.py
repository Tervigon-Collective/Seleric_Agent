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
from seleric_swarm.observability.tracing import _langfuse_span

_TRACER_NAME = "seleric.v3.mission"


class MissionTraceHandle:
    """Wrapper around OTel span and Langfuse span handle."""

    def __init__(self, otel_span: trace.Span, langfuse_handle: Any) -> None:
        self._otel_span = otel_span
        self._langfuse_handle = langfuse_handle

    def set_attribute(self, key: str, value: Any) -> None:
        if hasattr(self._otel_span, "set_attribute"):
            if not isinstance(value, (str, bool, int, float)):
                value = str(value)
            self._otel_span.set_attribute(key, value)

    def set_output(self, output: Any) -> None:
        payload = {"response": output} if isinstance(output, str) else output if isinstance(output, dict) else {"output": str(output)}
        if self._langfuse_handle is not None and hasattr(self._langfuse_handle, "set_outputs"):
            self._langfuse_handle.set_outputs(payload)
        if hasattr(self._otel_span, "set_attribute"):
            try:
                import json
                output_str = str(output)
                self._otel_span.set_attribute("final_response", output_str[:2000])
                self._otel_span.set_attribute("output.value", output_str)
                payload_json = json.dumps(payload)
                self._otel_span.set_attribute("langfuse.observation.output", payload_json)
                self._otel_span.set_attribute("langfuse.trace.output", payload_json)
            except Exception:
                pass

    def __getattr__(self, name: str) -> Any:
        return getattr(self._otel_span, name)


@contextmanager
def mission_trace(mission_id: str, **attributes: Any) -> Iterator[Any]:
    """Open one span for a mission run. Non-string attribute values are
    stringified — OTel attributes must be primitives, and callers pass
    things like intent lists that aren't."""
    tracer = trace.get_tracer(_TRACER_NAME)
    inputs = {"query": attributes.get("query")} if attributes.get("query") else None
    metadata = {"mission_id": mission_id, **attributes}
    with tracer.start_as_current_span("mission") as span, _langfuse_span(
        "mission",
        metadata=metadata,
        enabled=True,
        inputs=inputs,
        run_type="chain",
    ) as handle:
        handle_wrapper = MissionTraceHandle(span, handle)
        handle_wrapper.set_attribute("mission_id", mission_id)
        handle_wrapper.set_attribute("langfuse.trace.name", "mission")
        if attributes.get("query"):
            try:
                import json
                query_str = str(attributes["query"])
                handle_wrapper.set_attribute("input.value", query_str)
                query_json = json.dumps({"query": query_str})
                handle_wrapper.set_attribute("langfuse.observation.input", query_json)
                handle_wrapper.set_attribute("langfuse.trace.input", query_json)
            except Exception:
                pass
        for key, value in attributes.items():
            if value is None:
                continue
            handle_wrapper.set_attribute(key, value)
        # No flush here: Langfuse exports spans in the background and flushes
        # at process exit. A per-mission flush blocked the event loop for
        # ~3s at the end of every mission.
        yield handle_wrapper

