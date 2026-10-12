"""Unit tests for observability stage spans (M0.3).

Span assertions patch ``opentelemetry.trace.get_tracer`` to return a test-owned
tracer backed by an in-memory exporter — a real OTel exporter without mutating
the global tracer provider (which ``test_v3_mission_trace.py`` also sets, and
OTel only allows one set per process). Store assertions use an injected
temporary StageIOStore / StageTraceStore.
"""

from __future__ import annotations

from typing import Any
from unittest import mock

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import BaseModel

from seleric_swarm.observability import stage_span
from seleric_swarm.observability._stages import (
    StageIOStore,
    StageTraceStore,
    configure_tracing,
    get_stage_trace_store,
)


class _In(BaseModel):
    question: str
    api_token: str


class _Out(BaseModel):
    answer: str
    secret_note: str


@pytest.fixture
def otel_spans():
    """Capture stage spans in an in-memory exporter via a patched tracer."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("seleric_swarm")
    with mock.patch("opentelemetry.trace.get_tracer", return_value=tracer):
        yield exporter


@pytest.fixture
def stores(tmp_path):
    return StageIOStore(tmp_path), StageTraceStore()


def _finished_spans(exporter: InMemorySpanExporter) -> list[Any]:
    return list(exporter.get_finished_spans())


def test_stage_span_emits_span_with_fingerprints(otel_spans, stores) -> None:
    io_store, trace_store = stores
    inp = _In(question="net sales last week", api_token="shh")
    with stage_span(
        "understand",
        mission_id="m1",
        inp=inp,
        io_store=io_store,
        trace_store=trace_store,
    ) as span:
        span.set_output(_Out(answer="42", secret_note="nope"))

    spans = _finished_spans(otel_spans)
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "seleric.stage.understand"
    attrs = dict(span.attributes or {})
    assert attrs["seleric.mission_id"] == "m1"
    assert attrs["seleric.stage.input_fingerprint"]
    assert attrs["seleric.stage.output_fingerprint"]
    # LLM stage -> gen_ai.operation.name
    assert attrs["gen_ai.operation.name"] == "chat"


def test_non_llm_stage_has_no_gen_ai_operation(otel_spans, stores) -> None:
    io_store, trace_store = stores
    with stage_span(
        "execute", mission_id="m1", io_store=io_store, trace_store=trace_store
    ) as span:
        span.set_output(_Out(answer="x", secret_note="y"))
    span = _finished_spans(otel_spans)[0]
    assert span.name == "seleric.stage.execute"
    assert "gen_ai.operation.name" not in dict(span.attributes or {})


def test_set_gen_ai_adds_model_and_usage(otel_spans, stores) -> None:
    io_store, trace_store = stores
    with stage_span(
        "understand", mission_id="m1", io_store=io_store, trace_store=trace_store
    ) as span:
        span.set_gen_ai(model="gpt-5-mini", input_tokens=120, output_tokens=80)
        span.set_output(_Out(answer="a", secret_note="s"))
    attrs = dict(_finished_spans(otel_spans)[0].attributes or {})
    assert attrs["gen_ai.request.model"] == "gpt-5-mini"
    assert attrs["gen_ai.response.model"] == "gpt-5-mini"
    assert attrs["gen_ai.usage.input_tokens"] == 120
    assert attrs["gen_ai.usage.output_tokens"] == 80


def test_io_store_preserves_all_fields(stores) -> None:
    io_store, _ = stores
    inp = _In(question="q", api_token="super-secret-token")
    with stage_span(
        "understand",
        mission_id="m-preserve",
        inp=inp,
        io_store=io_store,
        trace_store=StageTraceStore(),
    ) as span:
        span.set_output(_Out(answer="a", secret_note="hidden-value"))

    record = io_store.load("m-preserve", "understand")
    assert record is not None
    assert record["input"]["api_token"] == "super-secret-token"
    assert record["output"]["secret_note"] == "hidden-value"
    assert record["input"]["question"] == "q"


def test_out_setter_receives_output(stores) -> None:
    io_store, _ = stores
    captured: list[_Out] = []
    with stage_span(
        "understand",
        mission_id="m1",
        out_setter=captured.append,
        io_store=io_store,
        trace_store=StageTraceStore(),
    ) as span:
        span.set_output(_Out(answer="a", secret_note="s"))
    assert len(captured) == 1
    assert captured[0].answer == "a"


def test_trace_store_records_stage(stores) -> None:
    io_store, trace_store = stores
    with stage_span(
        "understand", mission_id="m1", io_store=io_store, trace_store=trace_store
    ) as span:
        span.set_output(_Out(answer="a", secret_note="s"))
    stages = trace_store.pop("m1")
    assert "understand" in stages
    assert stages["understand"]["output_fingerprint"]


def test_error_is_recorded_and_reraises(stores) -> None:
    io_store, trace_store = stores
    with pytest.raises(RuntimeError, match="boom"), stage_span(
        "understand", mission_id="m1", io_store=io_store, trace_store=trace_store
    ):
        raise RuntimeError("boom")
    stages = trace_store.pop("m1")
    assert "RuntimeError: boom" in stages["understand"]["error"]
    record = io_store.load("m1", "understand")
    assert record["error"]


def test_configure_tracing_none_is_noop() -> None:
    class _Settings:
        trace_backend = "none"
        log_level = "INFO"

    assert configure_tracing(_Settings()) is False


def test_get_stage_trace_store_is_singleton() -> None:
    assert get_stage_trace_store() is get_stage_trace_store()
