from __future__ import annotations

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from seleric_swarm.observability.traces import mission_trace

# OTel only allows ``set_tracer_provider`` to succeed once per process (a
# second call is a silent no-op with a warning) -- set it up once, module
# scoped, and clear the exporter between tests instead of re-swapping providers.
_EXPORTER = InMemorySpanExporter()
_PROVIDER = TracerProvider()
_PROVIDER.add_span_processor(SimpleSpanProcessor(_EXPORTER))
trace.set_tracer_provider(_PROVIDER)


@pytest.fixture
def span_exporter():
    _EXPORTER.clear()
    yield _EXPORTER
    _EXPORTER.clear()


def test_mission_trace_records_one_span_with_attributes(span_exporter) -> None:
    with mission_trace("MS3-1", route="swarm", status="completed"):
        pass
    spans = span_exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "mission"
    assert span.attributes["mission_id"] == "MS3-1"
    assert span.attributes["route"] == "swarm"


def test_mission_trace_stringifies_non_primitive_attributes(span_exporter) -> None:
    with mission_trace("MS3-2", intents=["diagnostic", "predictive"]):
        pass
    span = span_exporter.get_finished_spans()[0]
    assert span.attributes["intents"] == "['diagnostic', 'predictive']"


def test_mission_trace_records_and_reraises_exception(span_exporter) -> None:
    with pytest.raises(RuntimeError, match="boom"), mission_trace("MS3-3"):
        raise RuntimeError("boom")
    span = span_exporter.get_finished_spans()[0]
    assert span.status.status_code == trace.StatusCode.ERROR
    assert len(span.events) == 1  # the recorded exception event


def test_mission_trace_is_safe_regardless_of_provider(span_exporter) -> None:
    # Whatever tracer provider is active (a real SDK one here, or the
    # default no-op in production with otel_enabled=False) -- must not raise.
    with mission_trace("MS3-4", route="lookup"):
        pass
