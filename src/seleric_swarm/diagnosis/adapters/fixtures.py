"""File-backed ``SeriesPort``: replay and offline runs read the same series format the eval harness writes.

The format is the world fixture of ``eval/diag`` (``series``: metric -> day -> value; ``segments``: metric ->
dimension -> segment -> day -> value). A day with no entry has no row; it is never read as zero. Like the eval
world, a request may carry one equality filter or one split dimension, not both, and only dimensions the fixture
holds; anything else comes back as an error result, not an invented number."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from seleric_swarm.diagnosis.domain.errors import InvalidRequest
from seleric_swarm.diagnosis.domain.types import SeriesRequest, SeriesResult, result_fingerprint

DaySeries = Mapping[date, float]


@dataclass(frozen=True)
class SeriesData:
    """Everything a fixture holds about values: totals per metric and per-segment series."""

    series: Mapping[str, DaySeries]
    segments: Mapping[str, Mapping[str, Mapping[str, DaySeries]]]
    sha256: str = ""


def _days(raw: Mapping[str, float]) -> dict[date, float]:
    return {date.fromisoformat(d): float(v) for d, v in raw.items()}


def load_series_data(path: Path) -> SeriesData:
    """Read a fixture file and refuse it if its data no longer matches the digest recorded inside it."""
    raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    data = {k: raw[k] for k in ("series", "segments", "definitions")}
    # The digest scheme the eval harness wrote the file with (plain JSON, sorted keys): not the normalising
    # ``canonical`` one, so a fixture eval accepts is never refused here.
    digest = hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if digest != raw.get("sha256"):
        raise InvalidRequest(f"{path.name}: data does not match its recorded sha256 (file edited or corrupt)")
    return SeriesData(
        series={m: _days(s) for m, s in raw["series"].items()},
        segments={
            m: {dim: {seg: _days(s) for seg, s in segs.items()} for dim, segs in dims.items()}
            for m, dims in raw["segments"].items()
        },
        sha256=str(raw["sha256"]),
    )


class FixtureSeriesAdapter:
    def __init__(self, data: SeriesData, *, label: str = "fixture") -> None:
        self._data = data
        self._label = label

    async def fetch(self, requests: Sequence[SeriesRequest]) -> list[SeriesResult]:
        return [self._one(r) for r in requests]

    def _one(self, request: SeriesRequest) -> SeriesResult:
        source = f"{self._label}:{self._data.sha256[:12]}:{request.metric_id}"
        filters = request.scope.as_mapping()
        if len(filters) > 1 or (filters and request.dimension):
            return self._error(request, source, "a fixture serves one dimension or one filter per request")
        if request.metric_id not in self._data.series:
            return self._error(request, source, f"metric {request.metric_id!r} is not in the fixture")
        window = request.window
        by_dim = self._data.segments.get(request.metric_id, {})
        if request.dimension:
            segments = by_dim.get(request.dimension)
            if segments is None:
                return self._error(request, source, f"{request.metric_id} cannot be split by {request.dimension}")
            cut = {
                seg: {d: v for d, v in series.items() if window.contains(d)} for seg, series in segments.items()
            }
            cut = {seg: s for seg, s in cut.items() if s}
            return SeriesResult(request, {}, cut, source, result_fingerprint(request, {}, cut))
        if filters:
            (dimension, accepted), = filters.items()
            segments = by_dim.get(dimension)
            if segments is None:
                return self._error(request, source, f"{request.metric_id} cannot be filtered by {dimension}")
            pooled: dict[date, float] = {}
            for value in accepted:
                for day, number in segments.get(value, {}).items():
                    if window.contains(day):
                        pooled[day] = pooled.get(day, 0.0) + number
            return SeriesResult(request, pooled, {}, source, result_fingerprint(request, pooled, {}))
        values = {d: v for d, v in self._data.series[request.metric_id].items() if window.contains(d)}
        return SeriesResult(request, values, {}, source, result_fingerprint(request, values, {}))

    @staticmethod
    def _error(request: SeriesRequest, source: str, message: str) -> SeriesResult:
        return SeriesResult(request, {}, {}, source, result_fingerprint(request, {}, {}), error=message)
