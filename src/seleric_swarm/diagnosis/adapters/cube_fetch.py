"""Fetching daily series from the semantic layer for a diagnosis: the Cube path ``metrics_query`` uses, with the
catalogue's scope mapping, one batched prefetch per view, the mission query cache and the investigation trace.

Moved out of ``toolsets/diagnosis.py`` (plan P2.6) so the tool reaches the data only through ``SeriesPort``
(``adapters/cube_series.py``); the tool, the exploration toolset and the composition toolset import the same names from
here, so there is one fetch path and the numbers are the ones the engine has always read."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from datetime import date
from typing import Any

from pydantic_ai import RunContext

from seleric_swarm.agent.dependencies import SelericDeps
from seleric_swarm.diagnosis.adapters.legacy_trace import current_trace
from seleric_swarm.services.mcp_query import build_metrics_query_args, dimension_value, row_date
from seleric_swarm.toolsets import _data

UNSET_SEGMENT = "(not set)"


def _filters_for(ctx: RunContext[SelericDeps], metric_id: str, filters: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    supported = set(ctx.deps.catalogue.supported_dimensions_for(metric_id))
    family_members = getattr(ctx.deps.catalogue, "family_members", None)
    out, dropped = [], []
    for k, v in (filters or {}).items():
        if v in (None, "", []):
            continue
        if supported and k not in supported:
            # The same slice under the metric's own member of the conformed family (finance_channel = meta is
            # ad_platform / acquisition_platform = meta elsewhere), as metrics_query answers it. Dropped, the
            # driver series of a Meta diagnosis came back for every platform and the scope gate sent the answer
            # back twice (regression 2026-10-09 Q28, 288 s).
            sibling = next(
                (m for m in sorted(family_members(k)) if m in supported), None
            ) if family_members is not None else None
            if sibling is None:
                dropped.append(k)
                continue
            k = sibling
        out.append({"dimension": k, "operator": "equals", "values": list(v) if isinstance(v, list) else [str(v)]})
    return out, dropped


def _supports_brand(ctx: RunContext[SelericDeps], metric_id: str) -> bool:
    from seleric_swarm.exploration.space import scope_keys

    cat = ctx.deps.catalogue
    return (not cat.metrics) or bool({d.lower() for d in cat.supported_dimensions_for(metric_id)} & scope_keys(cat))


async def _fetch_daily(
    ctx: RunContext[SelericDeps], metric_id: str, start: date, end: date, filters: dict[str, Any],
    dimension: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    flt, dropped = _filters_for(ctx, metric_id, filters)
    args = build_metrics_query_args(
        measure=metric_id, start=start.isoformat(), end=end.isoformat(), grain="day",
        dimensions=[dimension] if dimension else None, filters=flt or None,
        inject_default_brand=_supports_brand(ctx, metric_id),
    )
    hit = ctx.deps.query_cache.peek(_data.cache_key("seleric.metrics_query", args)) is not None
    result = await _data.cached_metrics_query(ctx, args)
    if (trace := current_trace()) is not None:
        trace.query(metric_id, cache_hit=hit)
    result = dict(result)
    result["_args"] = args
    result["_dropped_filters"] = dropped
    return result, (None if result.get("error") else args)


# One multi-measure query serves every metric of a catalogue view that is asked for with the same window, scope and
# dimension: a diagnosis reads ~100 candidate series, and one query per metric (182 calls, 74 s of server time in the
# live profile of 2026-10-13) dominated its wall time and used the mission's whole query budget. Each metric's slice is
# stored under the key its own single-measure query would have used, so the fetch below finds it and nothing else
# changes. Any failure leaves the metrics to the individual path.
_BATCH_MAX_MEASURES = 24
_BATCH_CONCURRENCY = 6


def _view_of(ctx: RunContext[SelericDeps]) -> dict[str, str]:
    return {m.id: m.view for m in ctx.deps.catalogue.metrics if m.view}


def _slice_for(payload: dict[str, Any], measures: list[str], metric_id: str) -> dict[str, Any] | None:
    """The single-measure result a combined query implies for ``metric_id`` (None when the server did not return it)."""
    columns = [c for c in payload.get("columns") or [] if isinstance(c, str)]
    if metric_id not in columns:
        return None
    others = set(measures) - {metric_id}
    provenance = dict(payload.get("provenance") or {})
    provenance["metric_ids"] = [metric_id]
    return {
        **payload,
        "columns": [c for c in columns if c not in others],
        "rows": [{k: v for k, v in row.items() if k not in others} for row in payload.get("rows") or [] if isinstance(row, dict)],
        "provenance": provenance,
    }


async def _prefetch_daily(
    ctx: RunContext[SelericDeps], jobs: Sequence[tuple[str, str | None]], start: date, end: date, filters: dict[str, Any],
) -> None:
    views = _view_of(ctx)
    groups: dict[str, list[tuple[str, str]]] = {}  # group key -> [(metric_id, its single-measure cache key)]
    group_args: dict[str, dict[str, Any]] = {}
    for metric_id, dimension in dict.fromkeys(jobs):
        flt, _ = _filters_for(ctx, metric_id, filters)
        args = build_metrics_query_args(
            measure=metric_id, start=start.isoformat(), end=end.isoformat(), grain="day",
            dimensions=[dimension] if dimension else None, filters=flt or None,
            inject_default_brand=_supports_brand(ctx, metric_id),
        )
        key = _data.cache_key("seleric.metrics_query", args)
        view = views.get(metric_id)
        if not view or ctx.deps.query_cache.peek(key) is not None:
            continue
        rest = {k: v for k, v in args.items() if k != "measures"}
        gk = json.dumps([view, rest], sort_keys=True, default=str)
        groups.setdefault(gk, []).append((metric_id, key))
        group_args.setdefault(gk, rest)
    chunks = [
        (gk, members[i : i + _BATCH_MAX_MEASURES])
        for gk, members in groups.items() if len(members) > 1
        for i in range(0, len(members), _BATCH_MAX_MEASURES)
    ]
    sem = asyncio.Semaphore(_BATCH_CONCURRENCY)

    async def one(gk: str, members: list[tuple[str, str]]) -> None:
        measures = [m for m, _ in members]
        async with sem:
            payload = await _data.cached_metrics_query(ctx, {"measures": measures, **group_args[gk]})
        if payload.get("error"):
            return
        # Measures on different time axes come back composed: one part per axis, each a plain result of its own.
        parts = payload["parts"] if isinstance(payload.get("parts"), list) else [payload]
        for part in parts:
            if not isinstance(part, dict) or part.get("error") or not isinstance(part.get("rows"), list):
                continue
            in_part = [m for m in measures if m in (part.get("columns") or [])]
            for metric_id, key in members:
                if metric_id in in_part:
                    piece = _slice_for(part, in_part, metric_id)
                    if piece is not None:
                        ctx.deps.query_cache.set(key, piece)

    if len(chunks) > 0:
        await asyncio.gather(*(one(gk, members) for gk, members in chunks))


async def _fetch_all(
    ctx: RunContext[SelericDeps], jobs: Sequence[tuple[str, str | None]], start: date, end: date, filters: dict[str, Any],
    guarded: Any = None,
) -> list[tuple[dict[str, Any], dict[str, Any] | None]]:
    """``_fetch_daily`` for every (metric, dimension) job, after one batched prefetch per view."""
    await _prefetch_daily(ctx, jobs, start, end, filters)
    run = guarded or (lambda coro: coro)
    return list(await asyncio.gather(*(run(_fetch_daily(ctx, m, start, end, filters, dimension=d)) for m, d in jobs)))


def _parse_series(result: dict[str, Any], metric_id: str) -> dict[date, float]:
    out: dict[date, float] = {}
    for row in result.get("rows") or []:
        ts, raw = row_date(row), row.get(metric_id)
        if ts is None or raw is None:
            continue
        try:
            out[date.fromisoformat(ts)] = float(raw)
        except (TypeError, ValueError):
            continue
    return out


def _parse_segments(result: dict[str, Any], metric_id: str, dimension: str) -> dict[str, dict[date, float]]:
    """metric per segment per day. Rows with no value for the dimension are kept as
    one explicit segment: dropping them made a split stop adding up to the total
    (unattributed sales vanished from a sales-per-spend split by campaign)."""
    out: dict[str, dict[date, float]] = {}
    for row in result.get("rows") or []:
        ts, raw = row_date(row), row.get(metric_id)
        seg = dimension_value(row, dimension)
        if ts is None or raw is None:
            continue
        if seg in (None, "", "None", "null"):
            seg = UNSET_SEGMENT
        try:
            day = date.fromisoformat(ts)
            bucket = out.setdefault(str(seg), {})
            bucket[day] = bucket.get(day, 0.0) + float(raw)
        except (TypeError, ValueError):
            continue
    return out
