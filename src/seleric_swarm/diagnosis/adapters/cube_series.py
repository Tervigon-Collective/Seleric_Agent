"""``SeriesPort`` over the live semantic layer, through the existing diagnosis fetch path.

A wrapper, not a rewrite: it calls the batching, caching, brand-scoping and filter-mapping code of
``toolsets/diagnosis.py`` unchanged, so the numbers are exactly the ones the current engine reads. It depends on
that module's private helpers on purpose; this is the one place allowed to, and the only one to change when the
fetch path moves to Cube v3."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, Any

from seleric_swarm.diagnosis.domain.canonical import (
    canonical_json,
    segments_fingerprint,
    series_fingerprint,
)
from seleric_swarm.diagnosis.domain.types import Scope, SeriesRequest, SeriesResult, Window
from seleric_swarm.toolsets import diagnosis as _fetch

if TYPE_CHECKING:
    from pydantic_ai import RunContext

    from seleric_swarm.agent.dependencies import SelericDeps

Guard = Callable[[Awaitable[Any]], Awaitable[Any]]
_EMPTY = series_fingerprint({})


class CubeSeriesAdapter:
    def __init__(self, ctx: RunContext[SelericDeps], *, guarded: Guard | None = None) -> None:
        self._ctx = ctx
        self._guarded = guarded

    async def fetch(self, requests: Sequence[SeriesRequest]) -> list[SeriesResult]:
        # One batched fetch per (window, scope): requests sharing both are answered by combined queries.
        groups: dict[tuple[Window, Scope], list[int]] = {}
        for index, request in enumerate(requests):
            groups.setdefault((request.window, request.scope), []).append(index)
        results: list[SeriesResult | None] = [None] * len(requests)
        for (window, scope), indexes in groups.items():
            jobs = [(requests[i].metric_id, requests[i].dimension) for i in indexes]
            filters: dict[str, Any] = {dim: list(values) for dim, values in scope.filters}
            fetched = await _fetch._fetch_all(
                self._ctx, jobs, window.start, window.end, filters, guarded=self._guarded
            )
            for index, (payload, _args) in zip(indexes, fetched, strict=True):
                results[index] = self._result(requests[index], payload)
        return [r for r in results if r is not None]

    @staticmethod
    def _result(request: SeriesRequest, payload: dict[str, Any]) -> SeriesResult:
        source = canonical_json(json.loads(json.dumps(payload.get("_args") or {}, default=str)))
        dropped = [str(d) for d in payload.get("_dropped_filters") or []]
        if payload.get("error"):
            return SeriesResult(request, {}, {}, source, _EMPTY, error=str(payload["error"]))
        if dropped:
            # A filter the metric cannot take would return the unscoped series as if it were the scoped one.
            return SeriesResult(
                request, {}, {}, source, _EMPTY, error=f"scope not supported by the metric: {sorted(dropped)}"
            )
        if request.dimension:
            segments = _fetch._parse_segments(payload, request.metric_id, request.dimension)
            return SeriesResult(request, {}, segments, source, segments_fingerprint(segments))
        values = _fetch._parse_series(payload, request.metric_id)
        return SeriesResult(request, values, {}, source, series_fingerprint(values))
