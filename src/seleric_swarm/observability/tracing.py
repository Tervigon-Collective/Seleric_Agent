from __future__ import annotations

import logging
import os
import sys
from collections.abc import Iterator, Mapping, MutableMapping
from contextlib import contextmanager
from typing import Any, Literal

RunType = Literal["tool", "chain", "llm", "retriever", "embedding", "prompt", "parser"]

import structlog

from seleric_swarm.config.settings import Settings
from seleric_swarm.conversations.privacy import redact_data

SENSITIVE_KEY_FRAGMENTS = (
    "api_key",
    "apikey",
    "authorization",
    "auth_token",
    "password",
    "secret",
    "token",
    "credential",
)

REQUIRED_SPAN_METADATA = (
    "request_id",
    "session_id",
    "mission_id",
    "workflow_name",
    "workflow_version",
    "agent_name",
    "agent_version",
)

# Coordinator task / specialist spans (plan Phase 10) — optional keys are filled
# when known; required base keys still come from REQUIRED_SPAN_METADATA.
COORDINATOR_TASK_METADATA_KEYS = (
    "task_id",
    "subquestion_id",
    "active_specialist",
    "mission_lead",
    "remediation_round",
    "decomposition_id",
    "decomposition_version",
    "leadership_epoch",
    "synthetic",
)

# LLM invocation spans must additionally identify the model, prompt, and retry
# accounting (plan section 6: "Metadata on every run (required)").
REQUIRED_LLM_RUN_METADATA = REQUIRED_SPAN_METADATA + (
    "agent_id",
    "prompt_id",
    "prompt_version",
    "model",
    "retry_count",
)


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(fragment in lowered for fragment in SENSITIVE_KEY_FRAGMENTS)


def redact_value(key: str, value: Any) -> Any:
    if _is_sensitive_key(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return redact_mapping(value)
    if isinstance(value, list):
        return [redact_value(key, item) for item in value]
    return redact_data(value, key=key)


def redact_mapping(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {key: redact_value(key, value) for key, value in payload.items()}


def redact_processor(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    return redact_mapping(event_dict)


def configure_logging(settings: Settings) -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            redact_processor,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.log_level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def configure_langsmith_env(settings: Settings) -> None:
    """Push Settings into process env so LangSmith client/wrappers see them.

    Pydantic reads ``.env`` into Settings; it does not export those values to
    ``os.environ``. LangSmith only looks at the process environment, so we copy
    here. Assignment (not setdefault) so ``LANGSMITH_TRACING=true`` in ``.env``
    wins over a stale false in the shell.
    """
    try:
        tracing = "true" if settings.langsmith_tracing else "false"
        os.environ["LANGSMITH_TRACING"] = tracing
        os.environ["LANGCHAIN_TRACING_V2"] = tracing
        if settings.langsmith_project:
            os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
            os.environ["LANGCHAIN_PROJECT"] = settings.langsmith_project
        if settings.langsmith_endpoint:
            os.environ["LANGSMITH_ENDPOINT"] = settings.langsmith_endpoint
        if settings.langsmith_api_key:
            os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key
            os.environ["LANGCHAIN_API_KEY"] = settings.langsmith_api_key
        if settings.langsmith_workspace_id:
            os.environ["LANGSMITH_WORKSPACE_ID"] = settings.langsmith_workspace_id
    except Exception:
        return


_otel_configured = False


def _headers(raw: str) -> dict[str, str]:
    return dict(
        part.split("=", 1) for part in raw.split(",")
        if "=" in part and part.split("=", 1)[0].strip()
    )


def configure_opentelemetry(settings: Settings) -> bool:
    """Configure OTLP once; Langfuse is supported through its stable OTLP endpoint."""
    global _otel_configured
    if _otel_configured or not settings.otel_enabled:
        return _otel_configured
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

        provider = TracerProvider(
            resource=Resource.create({"service.name": settings.otel_service_name}),
            sampler=ParentBased(TraceIdRatioBased(settings.otel_trace_sample_ratio)),
        )
        endpoints = [
            (settings.otel_exporter_otlp_endpoint, settings.otel_exporter_otlp_headers),
            (settings.langfuse_otel_endpoint, settings.langfuse_otel_headers),
        ]
        for endpoint, headers in endpoints:
            if endpoint:
                provider.add_span_processor(BatchSpanProcessor(
                    OTLPSpanExporter(endpoint=endpoint, headers=_headers(headers))
                ))
        trace.set_tracer_provider(provider)
        # Emit PydanticAI agent/LLM/tool spans to the provider above (deep trace).
        try:
            from pydantic_ai import Agent

            Agent.instrument_all()
        except Exception:  # pydantic-ai optional at import time; never block OTel
            pass
        _otel_configured = True
    except Exception:
        logging.getLogger("seleric.observability").warning(
            "otel_configuration_failed", exc_info=True
        )
    return _otel_configured


def instrument_fastapi(app: Any) -> None:
    if not _otel_configured:
        return
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(
            app,
            excluded_urls="health,readyz",
            http_capture_headers_server_request=["x-request-id"],
        )
    except Exception:
        logging.getLogger("seleric.observability").warning(
            "otel_fastapi_instrumentation_failed", exc_info=True
        )


@contextmanager
def operation_span(
    kind: Literal["http", "run", "task", "agent", "llm", "mcp", "retrieval", "persistence"],
    name: str,
    attributes: Mapping[str, Any] | None = None,
) -> Iterator[Any]:
    """Create a sanitized OTel span and propagate the active trace context."""
    try:
        from opentelemetry import trace

        manager = trace.get_tracer("seleric_swarm").start_as_current_span(
            f"{kind}.{name}",
            attributes={
                str(key): value if isinstance(value, (str, bool, int, float)) else str(value)
                for key, value in redact_mapping(attributes or {}).items()
            },
        )
    except Exception:
        yield None
        return
    with manager as span:
        yield span


def current_trace_context() -> dict[str, str]:
    try:
        from opentelemetry import trace

        context = trace.get_current_span().get_span_context()
        if not context.is_valid:
            return {}
        return {
            "trace_id": format(context.trace_id, "032x"),
            "span_id": format(context.span_id, "016x"),
        }
    except Exception:
        return {}


def mission_metadata(
    *,
    request_id: str,
    session_id: str,
    mission_id: str,
    workflow_name: str,
    workflow_version: str,
    agent_name: str,
    agent_version: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "request_id": request_id,
        "session_id": session_id,
        "mission_id": mission_id,
        "workflow_name": workflow_name,
        "workflow_version": workflow_version,
        "agent_name": agent_name,
        "agent_version": agent_version,
    }
    if extra:
        payload.update(extra)
    return redact_mapping(payload)


def missing_required_metadata(
    metadata: dict[str, Any], required: tuple[str, ...] = REQUIRED_SPAN_METADATA
) -> list[str]:
    return [key for key in required if metadata.get(key) in (None, "")]


def assert_required_metadata(
    metadata: dict[str, Any],
    *,
    strict: bool,
    required: tuple[str, ...] = REQUIRED_SPAN_METADATA,
    context: str = "span",
) -> list[str]:
    """Return missing required keys. Raise when ``strict`` (dev/CI: D5 "test failure")."""
    missing = missing_required_metadata(metadata, required)
    if missing and strict:
        raise ValueError(f"{context} is missing required trace metadata: {missing}")
    if missing:
        logging.getLogger("seleric.observability").warning(
            "trace_metadata_incomplete", extra={"context": context, "missing": missing}
        )
    return missing


class SpanHandle:
    """Lightweight handle yielded by :func:`traced_span`.

    Callers attach the coordinator's work to the trace with ``set_outputs`` (what
    the step produced). A no-op when tracing is off or LangSmith is unavailable,
    so call sites never need to branch.
    """

    __slots__ = ("_outputs", "_run")

    def __init__(self, run: Any = None) -> None:
        self._run = run
        self._outputs: dict[str, Any] | None = None

    def set_outputs(self, outputs: dict[str, Any]) -> None:
        self._outputs = outputs

    def _flush(self) -> None:
        if self._run is None or self._outputs is None:
            return
        try:
            self._run.end(outputs=redact_mapping(self._outputs))
        except Exception:
            pass


@contextmanager
def _langsmith_span(
    name: str,
    metadata: dict[str, Any],
    enabled: bool,
    *,
    inputs: dict[str, Any] | None = None,
    run_type: RunType = "chain",
    tags: list[str] | None = None,
) -> Iterator[SpanHandle]:
    """Open a LangSmith span. Tracing failures never abort the mission.

    Yields a :class:`SpanHandle`; use ``.set_outputs({...})`` to record what the
    step produced so the LangSmith run tree shows inputs and outputs, not just a
    name.
    """
    if not enabled:
        yield SpanHandle()
        return
    try:
        from langsmith import trace

        cm = trace(
            name=name,
            run_type=run_type,
            tags=tags,
            metadata=redact_mapping(metadata),
            inputs=redact_mapping(inputs or {}),
        )
        run = cm.__enter__()
    except Exception:
        logging.getLogger("seleric.observability").warning(
            "langsmith_span_failed", extra={"span": name}
        )
        yield SpanHandle()
        return

    handle = SpanHandle(run)
    exc_info: tuple[Any, Any, Any] = (None, None, None)
    try:
        yield handle
    except Exception:
        exc_info = sys.exc_info()
        raise
    finally:
        try:
            handle._flush()
        except Exception:
            pass
        try:
            cm.__exit__(*exc_info)
        except Exception:
            pass


@contextmanager
def traced_span(
    name: str,
    metadata: dict[str, Any],
    enabled: bool,
    *,
    inputs: dict[str, Any] | None = None,
    run_type: RunType = "chain",
    tags: list[str] | None = None,
) -> Iterator[SpanHandle]:
    """Emit matching OTel and optional LangSmith spans from existing call sites."""
    kind: Literal["run", "task", "agent", "llm", "retrieval"] = (
        "llm" if run_type in {"llm", "embedding", "prompt"}
        else "retrieval" if run_type == "retriever"
        else "agent" if "agent" in name or name.startswith("node.")
        else "task" if "task" in name
        else "run"
    )
    with operation_span(kind, name, metadata), _langsmith_span(
        name, metadata, enabled, inputs=inputs, run_type=run_type, tags=tags
    ) as handle:
        yield handle


def langsmith_run_url(project: str, run_id: str | None, org: str = "default") -> str | None:
    if not run_id:
        return None
    return f"https://smith.langchain.com/o/{org}/projects/p/{project}/r/{run_id}"


def langfuse_trace_url(
    base_url: str, project_id: str, trace_id: str | None
) -> str | None:
    if not trace_id or not project_id:
        return None
    return (
        f"{base_url.rstrip('/')}/project/{project_id}/traces/{trace_id}"
    )
