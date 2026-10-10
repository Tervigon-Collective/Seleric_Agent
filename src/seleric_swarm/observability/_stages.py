"""Stage-level observability — one OTel span + Langfuse observation per stage.

M0.3. Every pipeline stage runs inside :func:`stage_span`, which:

* opens an OTel span ``seleric.stage.<stage>`` carrying ``seleric.mission_id``,
  ``seleric.stage.input_fingerprint`` / ``output_fingerprint`` and, for LLM
  stages, the ``gen_ai.*`` attributes pinned to :data:`GENAI_SEMCONV_VERSION`;
* persists the full redacted input/output JSON to a Langfuse observation;
* writes a compact per-stage record to the mission result's ``trace["stages"]``
  and to a local JSON store so a mission can be replayed (M0.4).

Observability never aborts a mission: every external call is guarded so a tracing
failure degrades to a no-op rather than failing the stage.

Public interface is exactly ``stage_span`` and ``configure_tracing`` (A9); the
helpers below are importable from this private module by the replay script and the
pipeline.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from seleric_swarm.contracts.pipeline import fingerprint
from seleric_swarm.observability.tracing import (
    _langfuse_span,
    configure_langfuse_env,
    configure_logging,
    configure_opentelemetry,
    operation_span,
    redact_mapping,
)

__all__ = ["configure_tracing", "stage_span"]

# Pinned OTel GenAI semantic-convention version (plan M0.3 step 1). Bump this — and
# record it in an ADR — whenever the attribute names below change.
GENAI_SEMCONV_VERSION = "0.1.0"

_TRACER_NAME = "seleric_swarm"
_STAGE_SPAN_PREFIX = "seleric.stage."

_MISSION_ID_ATTR = "seleric.mission_id"
_STAGE_NAME_ATTR = "seleric.stage"
_STAGE_INPUT_FP_ATTR = "seleric.stage.input_fingerprint"
_STAGE_OUTPUT_FP_ATTR = "seleric.stage.output_fingerprint"

# OTel GenAI semconv attribute names (pinned above).
_GENAI_OPERATION = "gen_ai.operation.name"
_GENAI_REQUEST_MODEL = "gen_ai.request.model"
_GENAI_RESPONSE_MODEL = "gen_ai.response.model"
_GENAI_USAGE_INPUT = "gen_ai.usage.input_tokens"
_GENAI_USAGE_OUTPUT = "gen_ai.usage.output_tokens"

# Stages that are LLM-driven get ``gen_ai.*`` attributes; everything else is a code
# or data stage. The pipeline registry (M2.1) extends this as new LLM stages land.
LLM_STAGE_OPERATIONS: dict[str, str] = {
    "understand": "chat",
    "answer": "chat",
    "draft": "chat",
    "select": "chat",
    "clarify": "chat",
}


def _primitive(value: Any) -> str | bool | int | float:
    """Coerce an OTel attribute value to a primitive (the SDK rejects others)."""
    return value if isinstance(value, (str, bool, int, float)) else str(value)


def _start_otel_span(name: str, attributes: dict[str, Any]) -> Any:
    """Open an OTel span as current so child spans (LLM calls) nest under it."""
    from opentelemetry import trace

    return trace.get_tracer(_TRACER_NAME).start_as_current_span(
        name, attributes={key: _primitive(v) for key, v in attributes.items()}
    )


class StageSpanHandle:
    """Yielded by :func:`stage_span`. Report the stage output and, for LLM stages,
    the model/token usage — both feed the span attributes and the Langfuse
    observation, and are what make a mission replayable."""

    __slots__ = (
        "_inp",
        "_io",
        "_langfuse",
        "_mission_id",
        "_operation",
        "_otel",
        "_out_setter",
        "_output",
        "_stage",
        "_trace",
    )

    def __init__(
        self,
        *,
        stage: str,
        mission_id: str,
        inp: BaseModel | None,
        out_setter: Callable[[BaseModel], None] | None,
        io_store: StageIOStore,
        trace_store: StageTraceStore,
        operation: str | None,
    ) -> None:
        self._stage = stage
        self._mission_id = mission_id
        self._inp = inp
        self._out_setter = out_setter
        self._io = io_store
        self._trace = trace_store
        self._operation = operation
        self._otel: Any = None
        self._langfuse: Any = None
        self._output: BaseModel | None = None

    def bind(self, otel: Any, langfuse: Any) -> None:
        self._otel = otel
        self._langfuse = langfuse

    def set_output(self, output: BaseModel) -> None:
        """Record the stage's output model: output fingerprint, Langfuse output,
        the compact trace record and the local IO store, then ``out_setter``."""
        self._output = output
        out_fp = fingerprint(output)
        self._set_attr(_STAGE_OUTPUT_FP_ATTR, out_fp)
        if self._langfuse is not None:
            self._langfuse.set_outputs(
                {"output": redact_mapping(output.model_dump(mode="json"))}
            )
        self._trace.record(
            self._mission_id,
            self._stage,
            {"stage": self._stage, "operation": self._operation, "output_fingerprint": out_fp},
        )
        self._io.save(
            self._mission_id,
            self._stage,
            inp=self._inp,
            out=output,
            out_fingerprint=out_fp,
        )
        if self._out_setter is not None:
            self._out_setter(output)

    def set_gen_ai(
        self,
        *,
        model: str,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        """Attach model + token-usage attributes to an LLM stage's span."""
        self._set_attr(_GENAI_REQUEST_MODEL, model)
        self._set_attr(_GENAI_RESPONSE_MODEL, model)
        if input_tokens is not None:
            self._set_attr(_GENAI_USAGE_INPUT, input_tokens)
        if output_tokens is not None:
            self._set_attr(_GENAI_USAGE_OUTPUT, output_tokens)

    def set_attribute(self, key: str, value: Any) -> None:
        self._set_attr(key, value)

    def record_error(self, exc: Exception) -> None:
        """Record a failed stage in the trace store and IO store, then re-raise."""
        message = f"{type(exc).__name__}: {exc}"
        self._trace.record(
            self._mission_id,
            self._stage,
            {"stage": self._stage, "operation": self._operation, "error": message},
        )
        self._io.save(self._mission_id, self._stage, error=message)

    def _set_attr(self, key: str, value: Any) -> None:
        if self._otel is not None and hasattr(self._otel, "set_attribute"):
            try:
                self._otel.set_attribute(key, _primitive(value))
            except Exception:  # noqa: S110 - tracing must never fail the stage
                pass


class StageTraceStore:
    """Accumulates the compact per-stage records for ``trace["stages"]``.

    The pipeline reads :meth:`pop` once per mission and folds the result into
    :class:`~seleric_swarm.agent.output.MissionResult.trace`. Keyed by mission id
    so concurrent missions don't collide.
    """

    def __init__(self) -> None:
        self._missions: dict[str, dict[str, dict[str, Any]]] = {}

    def record(self, mission_id: str, stage: str, record: dict[str, Any]) -> None:
        self._missions.setdefault(mission_id, {})[stage] = record

    def pop(self, mission_id: str) -> dict[str, Any]:
        return self._missions.pop(mission_id, {})


_stage_trace_store: StageTraceStore | None = None


def get_stage_trace_store() -> StageTraceStore:
    global _stage_trace_store
    if _stage_trace_store is None:
        _stage_trace_store = StageTraceStore()
    return _stage_trace_store


def _reset_stage_trace_store() -> None:
    """Test helper: drop the process-wide store so each test starts clean."""
    global _stage_trace_store
    _stage_trace_store = None


class StageIOStore:
    """Local, redacted JSON store of stage input/output, one file per stage.

    This is what makes a mission replayable without Langfuse: the replay script
    (M0.4) reads these files to re-run a stage. Writes are guarded — a store
    failure never fails a stage.
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    def _path(self, mission_id: str, stage: str) -> Path:
        return self._root / mission_id / f"{stage}.json"

    def save(
        self,
        mission_id: str,
        stage: str,
        *,
        inp: BaseModel | None = None,
        out: BaseModel | None = None,
        out_fingerprint: str | None = None,
        error: str | None = None,
    ) -> None:
        try:
            path = self._path(mission_id, stage)
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "mission_id": mission_id,
                "stage": stage,
                "input": redact_mapping(inp.model_dump(mode="json")) if inp else None,
                "output": redact_mapping(out.model_dump(mode="json")) if out else None,
                "output_fingerprint": out_fingerprint,
                "error": error,
            }
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception:  # noqa: S110 - a store write must never fail the stage
            pass

    def load(self, mission_id: str, stage: str) -> dict[str, Any] | None:
        try:
            return json.loads(self._path(mission_id, stage).read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def stages(self, mission_id: str) -> list[str]:
        directory = self._root / mission_id
        if not directory.is_dir():
            return []
        return sorted(path.stem for path in directory.glob("*.json"))


def _default_io_store() -> StageIOStore:
    from seleric_swarm.config.settings import get_settings

    return StageIOStore(Path(get_settings().trace_local_dir))


@contextmanager
def stage_span(
    stage: str,
    *,
    mission_id: str,
    inp: BaseModel | None = None,
    out_setter: Callable[[BaseModel], None] | None = None,
    enabled: bool = True,
    io_store: StageIOStore | None = None,
    trace_store: StageTraceStore | None = None,
) -> Iterator[StageSpanHandle]:
    """Wrap one pipeline stage in an OTel span + Langfuse observation.

    ``out_setter`` is invoked with the stage's output model (via
    :meth:`StageSpanHandle.set_output`) so callers can capture it without a
    separate variable. ``io_store`` / ``trace_store`` are injectable for tests.
    """
    io_store = io_store or _default_io_store()
    trace_store = trace_store or get_stage_trace_store()
    operation = LLM_STAGE_OPERATIONS.get(stage)

    input_fp = fingerprint(inp) if inp is not None else None
    input_json = redact_mapping(inp.model_dump(mode="json")) if inp is not None else None
    metadata = {
        _MISSION_ID_ATTR: mission_id,
        _STAGE_NAME_ATTR: stage,
        _STAGE_INPUT_FP_ATTR: input_fp,
    }
    attributes: dict[str, Any] = {
        _MISSION_ID_ATTR: mission_id,
        _STAGE_NAME_ATTR: stage,
    }
    if input_fp is not None:
        attributes[_STAGE_INPUT_FP_ATTR] = input_fp
    if operation is not None:
        attributes[_GENAI_OPERATION] = operation

    handle = StageSpanHandle(
        stage=stage,
        mission_id=mission_id,
        inp=inp,
        out_setter=out_setter,
        io_store=io_store,
        trace_store=trace_store,
        operation=operation,
    )

    otel_cm = _start_otel_span(f"{_STAGE_SPAN_PREFIX}{stage}", attributes) if enabled else nullcontext()
    with otel_cm as otel_span:
        langfuse_cm = _langfuse_span(
            stage,
            metadata,
            enabled,
            inputs=input_json,
            run_type=("llm" if operation else "chain"),
        )
        with langfuse_cm as langfuse_handle:
            handle.bind(otel_span, langfuse_handle)
            io_store.save(mission_id, stage, inp=inp)
            try:
                yield handle
            except Exception as exc:
                handle.record_error(exc)
                io_store.save(mission_id, stage, inp=inp, error=str(exc))
                raise


@contextmanager
def llm_call_span(
    name: str, *, model: str, mission_id: str
) -> Iterator[StageSpanHandle]:
    """Wrap a raw pydantic-ai model call in an OTel span with ``gen_ai.*``
    attributes (plan M0.3 step 3). Nests under the enclosing stage span."""
    handle = StageSpanHandle(
        stage=name,
        mission_id=mission_id,
        inp=None,
        out_setter=None,
        io_store=_NullIOStore(),
        trace_store=_NullTraceStore(),
        operation="chat",
    )
    attributes = {
        _GENAI_OPERATION: "chat",
        _GENAI_REQUEST_MODEL: model,
        _GENAI_RESPONSE_MODEL: model,
        _MISSION_ID_ATTR: mission_id,
    }
    with operation_span("llm", name, attributes) as otel_span:
        handle.bind(otel_span, None)
        yield handle


class _NullIOStore(StageIOStore):
    def __init__(self) -> None:
        super().__init__(Path("/dev/null"))

    def save(self, *args: Any, **kwargs: Any) -> None:
        return None


class _NullTraceStore(StageTraceStore):
    def record(self, *args: Any, **kwargs: Any) -> None:
        return None


def configure_tracing(settings: Any) -> bool:
    """Unified, idempotent tracing setup driven by ``settings.trace_backend``.

    Always configures logging and exports Langfuse env when keys exist; configures
    OTel export when the backend includes it. Returns True when OTel is active.
    Never raises — tracing must not block startup.
    """
    configure_logging(settings)
    configure_langfuse_env(settings)
    backend = getattr(settings, "trace_backend", "both")
    if backend == "none":
        return False
    if backend in {"otel", "both"}:
        try:
            return configure_opentelemetry(settings)
        except Exception:
            return False
    return False
