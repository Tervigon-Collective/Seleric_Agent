"""Windows, scopes and series requests: the vocabulary every layer shares."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta

from seleric_swarm.diagnosis.domain.canonical import segments_fingerprint, series_fingerprint
from seleric_swarm.diagnosis.domain.errors import InvalidRequest


@dataclass(frozen=True)
class Window:
    """Inclusive range of calendar days."""

    start: date
    end: date

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise InvalidRequest(f"window starts after it ends: {self.start} > {self.end}")

    @property
    def length(self) -> int:
        return (self.end - self.start).days + 1

    def days(self) -> tuple[date, ...]:
        return tuple(self.start + timedelta(days=i) for i in range(self.length))

    def contains(self, day: date) -> bool:
        return self.start <= day <= self.end


@dataclass(frozen=True)
class Scope:
    """Equality filters, ``dimension -> accepted values``, held sorted so equal scopes compare and hash equal."""

    filters: tuple[tuple[str, tuple[str, ...]], ...] = ()

    @classmethod
    def of(cls, filters: Mapping[str, str | tuple[str, ...] | list[str]] | None = None) -> Scope:
        items: list[tuple[str, tuple[str, ...]]] = []
        for dimension, raw in sorted((filters or {}).items()):
            values = (raw,) if isinstance(raw, str) else tuple(raw)
            if not dimension or not values or any(v == "" for v in values):
                raise InvalidRequest(f"empty filter for dimension {dimension!r}")
            items.append((dimension, tuple(sorted(set(values)))))
        return cls(tuple(items))

    def as_mapping(self) -> dict[str, tuple[str, ...]]:
        return dict(self.filters)

    def is_empty(self) -> bool:
        return not self.filters


@dataclass(frozen=True)
class SeriesRequest:
    """One daily series: a metric over a window and scope, optionally split by one dimension."""

    metric_id: str
    window: Window
    scope: Scope = field(default_factory=Scope)
    dimension: str | None = None


@dataclass(frozen=True)
class SeriesResult:
    """What the data source returned for one request.

    ``values`` is set for an unsplit request, ``segments`` for a split one. A day the source has no row for is
    absent, never zero. ``error`` is set when the source could not answer; the result is then empty. The
    ``fingerprint`` covers the returned numbers so a replay can prove it read the same data.
    """

    request: SeriesRequest
    values: Mapping[date, float]
    segments: Mapping[str, Mapping[date, float]]
    source_query: str
    fingerprint: str
    error: str | None = None


def result_fingerprint(
    request: SeriesRequest, values: Mapping[date, float], segments: Mapping[str, Mapping[date, float]]
) -> str:
    """The one rule for the digest of an answer: a split request is digested as segments (even when it came back
    empty), an unsplit one as a plain series. Adapters, the recorder and the replay check all use this."""
    if request.dimension is not None or segments:
        return segments_fingerprint(segments)
    return series_fingerprint(values)
