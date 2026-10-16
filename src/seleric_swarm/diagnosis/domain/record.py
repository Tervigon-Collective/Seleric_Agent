"""The run record (plan §5.3 item 1, D-21): everything an investigation depended on, so it can be replayed offline.

One self-contained, JSON-able value. Fetched series travel inside it as gzip+base64 blobs under ``blobs``; a store
that wants them elsewhere can move them and keep the references in ``snapshots``."""

from __future__ import annotations

import base64
import gzip
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from seleric_swarm.diagnosis.domain.canonical import canonical_json, fingerprint
from seleric_swarm.diagnosis.domain.types import Scope, SeriesRequest, SeriesResult, Window

RECORD_SCHEMA_VERSION = 1


def request_to_dict(request: SeriesRequest) -> dict[str, Any]:
    return {
        "metric_id": request.metric_id,
        "start": request.window.start.isoformat(), "end": request.window.end.isoformat(),
        "scope": {dim: list(values) for dim, values in request.scope.filters},
        "dimension": request.dimension,
    }


def request_from_dict(raw: Mapping[str, Any]) -> SeriesRequest:
    return SeriesRequest(
        str(raw["metric_id"]),
        Window(date.fromisoformat(raw["start"]), date.fromisoformat(raw["end"])),
        Scope.of({k: tuple(v) for k, v in dict(raw["scope"]).items()}),
        raw["dimension"],
    )


def request_key(request: SeriesRequest) -> str:
    """Stable identity of a series request (what a replay looks the recorded answer up by)."""
    return fingerprint(request_to_dict(request), length=16)


def encode_series(result: SeriesResult) -> str:
    body: dict[str, Any] = {
        "values": {d.isoformat(): v for d, v in result.values.items()},
        "segments": {s: {d.isoformat(): v for d, v in series.items()} for s, series in result.segments.items()},
    }
    if result.currency or result.dropped_filters or result.rows:
        body["meta"] = {"currency": result.currency, "dropped_filters": list(result.dropped_filters), "rows": result.rows}
    return base64.b64encode(gzip.compress(canonical_json(body).encode("utf-8"), mtime=0)).decode("ascii")


def decode_series(blob: str) -> tuple[dict[date, float], dict[str, dict[date, float]]]:
    values, segments, _ = decode_series_with_meta(blob)
    return values, segments


def decode_series_with_meta(blob: str) -> tuple[dict[date, float], dict[str, dict[date, float]], dict[str, Any]]:
    """The series, the segments and what the source said besides the numbers (empty for a record that has none)."""
    body = json.loads(gzip.decompress(base64.b64decode(blob)).decode("utf-8"))
    values = {date.fromisoformat(d): float(v) for d, v in body["values"].items()}
    segments = {s: {date.fromisoformat(d): float(v) for d, v in series.items()} for s, series in body["segments"].items()}
    return values, segments, dict(body.get("meta") or {})


@dataclass(frozen=True)
class SeriesSnapshot:
    """One answer the data source gave during the run, in the order it was given."""

    key: str
    request: Mapping[str, Any]
    source_query: str
    fingerprint: str
    data_ref: str
    error: str | None = None


@dataclass(frozen=True)
class EstimateSnapshot:
    """One causal estimate the run asked for, in the order it was asked: the digest of the request and the answer."""

    key: str
    result: Mapping[str, Any]


@dataclass(frozen=True)
class RunRecord:
    investigation_id: str
    created_at: datetime
    runner: str
    request: Mapping[str, Any]
    versions: Mapping[str, Any]
    config: Mapping[str, Any]
    seed: int
    snapshots: tuple[SeriesSnapshot, ...]
    blobs: Mapping[str, str]
    events: tuple[Mapping[str, Any], ...]
    overflow: Mapping[str, Mapping[str, Any]]
    runtime_trace: Mapping[str, Any]
    report: Mapping[str, Any] | None
    subgraph: Mapping[str, Any] = field(default_factory=dict)
    estimates: tuple[EstimateSnapshot, ...] = ()  # additive since the first version; absent in older records
    schema_version: int = RECORD_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version, "investigation_id": self.investigation_id,
            "created_at": self.created_at.isoformat(), "runner": self.runner, "request": dict(self.request),
            "versions": dict(self.versions), "config": dict(self.config), "seed": self.seed,
            "subgraph": dict(self.subgraph),
            "estimates": [{"key": e.key, "result": dict(e.result)} for e in self.estimates],
            "snapshots": [
                {"key": s.key, "request": dict(s.request), "source_query": s.source_query,
                 "fingerprint": s.fingerprint, "data_ref": s.data_ref, "error": s.error}
                for s in self.snapshots
            ],
            "blobs": dict(self.blobs), "events": [dict(e) for e in self.events],
            "overflow": {k: dict(v) for k, v in self.overflow.items()},
            "runtime_trace": dict(self.runtime_trace), "report": None if self.report is None else dict(self.report),
        }

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> RunRecord:
        if raw.get("schema_version") != RECORD_SCHEMA_VERSION:
            raise ValueError(f"run record schema {raw.get('schema_version')!r}, this engine reads {RECORD_SCHEMA_VERSION}")
        return cls(
            investigation_id=str(raw["investigation_id"]), created_at=datetime.fromisoformat(raw["created_at"]),
            runner=str(raw["runner"]), request=dict(raw["request"]), versions=dict(raw["versions"]),
            config=dict(raw["config"]), seed=int(raw["seed"]), subgraph=dict(raw.get("subgraph") or {}),
            estimates=tuple(EstimateSnapshot(e["key"], dict(e["result"])) for e in raw.get("estimates") or []),
            snapshots=tuple(
                SeriesSnapshot(s["key"], dict(s["request"]), s["source_query"], s["fingerprint"], s["data_ref"], s.get("error"))
                for s in raw["snapshots"]
            ),
            blobs=dict(raw["blobs"]), events=tuple(dict(e) for e in raw["events"]),
            overflow={k: dict(v) for k, v in dict(raw["overflow"]).items()},
            runtime_trace=dict(raw["runtime_trace"]), report=None if raw["report"] is None else dict(raw["report"]),
        )

    @classmethod
    def from_json(cls, text: str) -> RunRecord:
        return cls.from_dict(json.loads(text))
