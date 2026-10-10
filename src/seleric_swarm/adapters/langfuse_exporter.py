"""Langfuse exporter adapter (M0.3).

The single place outside :mod:`seleric_swarm.observability` that talks to the
Langfuse SDK. It wraps the same low-level observation span the stage exporter
uses, so stage and non-stage observations share redaction and metadata
conventions. Imports from ``observability`` (adapters -> observability, A1).

The pipeline and the replay script use this to write observations for artifacts
that are not pipeline stages (derivations, findings, exploration maps).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from seleric_swarm.observability.tracing import RunType, _langfuse_span, redact_mapping

__all__ = ["LangfuseExporter", "LangfuseObservation"]


class LangfuseObservation:
    """One open Langfuse observation. Set its output, then let it close."""

    def __init__(self, handle: Any) -> None:
        self._handle = handle

    def set_output(self, output: dict[str, Any]) -> None:
        self._handle.set_outputs(redact_mapping(output))


class LangfuseExporter:
    """Create redacted Langfuse observations for pipeline artifacts.

    ``enabled=False`` (or missing Langfuse keys) makes every observation a no-op,
    so callers never branch on whether tracing is configured.
    """

    def __init__(self, *, enabled: bool = True) -> None:
        self._enabled = enabled

    @contextmanager
    def observation(
        self,
        name: str,
        *,
        metadata: dict[str, Any] | None = None,
        inputs: dict[str, Any] | None = None,
        run_type: RunType = "chain",
    ) -> Iterator[LangfuseObservation]:
        with _langfuse_span(
            name,
            metadata or {},
            self._enabled,
            inputs=redact_mapping(inputs) if inputs else None,
            run_type=run_type,
        ) as handle:
            yield LangfuseObservation(handle)
