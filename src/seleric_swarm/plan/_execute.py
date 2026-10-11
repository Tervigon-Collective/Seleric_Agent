"""QueryStep executor (M2.2): run steps and return ResultSets.

``execute(steps, ...)`` runs a plan's query steps against the ``SemanticPort``
and returns one ``ResultSet`` per step, in `steps` order. Independent steps run
in parallel (``asyncio.Semaphore(6)``); a step whose ``entities_from_step``
draws its entity values from another step's rows runs after that step. A step
that fails records a ``StageError`` on its own ResultSet and the rest continue.

Rules carried over from the v2 planner/executor:

- filters come only from the step (no `_with_named_values`); ``in`` maps to Cube
  ``equals`` and ``not_in`` to ``notEquals``;
- ``entities_from_step`` feeds the entity values present in the referenced
  step's rows (an ``equals`` filter on each of the referenced step's breakdown
  dimensions);
- ``elapsed_only`` follows ``services/elapsed.py``: the mission day is queried
  hourly and each day of the window is counted only through the hours already
  elapsed today, every day cut for a plain window and the last day only for a
  period to date; rows carry the ``elapsed_through`` dimension;
- every ResultSet row cites an evidence id — a deterministic digest of the row's
  EvidenceArtifact ``payload``, so re-running the same step reuses the ids. When
  an ``evidence_store`` is wired in, the evidence artifact is persisted under
  that id (immutable — a re-put of the same id is a no-op).

This module is L2: it may import ``semantic`` and ``contracts.pipeline``, never
``re``, an I/O SDK, ``pydantic_ai``, or ``seleric_swarm.adapters``.
"""

from __future__ import annotations

import asyncio
import calendar
import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from datetime import time as day_time
from typing import Any, Protocol

from seleric_swarm.agent.artifacts import EvidenceArtifact
from seleric_swarm.contracts.pipeline import (
    FilterSpec,
    QueryStep,
    RankingSpec,
    Ref,
    ResultRow,
    ResultSet,
    StageError,
    WindowSpec,
)
from seleric_swarm.semantic import (
    CatalogueError,
    MetricsQueryArgs,
    QueryResponse,
    SemanticPort,
    SemanticReadModel,
)
from seleric_swarm.services.elapsed import ELAPSED_KEY, completed_hours, in_progress_day

__all__ = ["execute"]


_DEFAULT_WINDOW = WindowSpec(role="event", start=date(1970, 1, 1), end=date(1970, 1, 1))
_EMPTY_LABELS = ("", "None", "null", "none", "NULL")
_EVIDENCE_IDENTITY_FIELDS = (
    "metric_id",
    "dimensions",
    "grain",
    "period_start",
    "period_end",
    "value",
    "unit",
    "source_query",
)
_GRANULARITY_SUFFIXES = ("second", "minute", "hour", "day", "week", "month", "quarter", "year")


# Row-dimension key carrying a grain bucket's start date (ISO) on per-period ResultSets.
PERIOD_KEY = "period_start"


class EvidenceStore(Protocol):
    """Persistence seam for EvidenceArtifact payloads (wired by the pipeline)."""

    def put_evidence(self, payload: dict[str, Any], evidence_id: str) -> None: ...


async def execute(
    steps: Sequence[QueryStep],
    *,
    windows: Mapping[str, WindowSpec],
    port: SemanticPort | None,
    read_model: SemanticReadModel,
    as_of: datetime,
    evidence_store: EvidenceStore | None = None,
    concurrency: int = 6,
) -> tuple[ResultSet, ...]:
    """Run ``steps`` to ``ResultSet``s, in order.

    ``windows`` maps a window role (``event``/``baseline``/``context``) to the
    ``WindowSpec`` it resolves to. ``port`` is the only data path — a ``None``
    port produces an error ResultSet per step (fail-open, no network).
    Evidence is written only when an ``evidence_store`` is provided.
    """
    gate = asyncio.Semaphore(concurrency)
    done: dict[str, ResultSet] = {}

    async def _run(step: QueryStep) -> ResultSet:
        async with gate:
            return await _run_step(
                step,
                windows=windows,
                port=port,
                read_model=read_model,
                evidence_store=evidence_store,
                as_of=as_of,
                referents=done,
            )

    while True:
        wave = [
            step
            for step in steps
            if step.step_id not in done
            and (step.entities_from_step is None or step.entities_from_step in done)
        ]
        if not wave:
            break
        outcomes = await asyncio.gather(*(_run(step) for step in wave))
        for step, outcome in zip(wave, outcomes, strict=True):
            done[step.step_id] = outcome

    results: list[ResultSet] = []
    for step in steps:
        if step.step_id in done:
            results.append(done[step.step_id])
        else:
            results.append(
                _error(
                    step,
                    windows=windows,
                    read_model=read_model,
                    code="step_dependency_cycle",
                    message=f"step {step.step_id!r} depends on a step that never completed",
                )
            )
    return tuple(results)


async def _run_step(
    step: QueryStep,
    *,
    windows: Mapping[str, WindowSpec],
    port: SemanticPort | None,
    read_model: SemanticReadModel,
    evidence_store: EvidenceStore | None,
    as_of: datetime,
    referents: Mapping[str, ResultSet],
) -> ResultSet:
    window = windows.get(step.window_role)
    if window is None:
        return _error(
            step,
            windows=windows,
            read_model=read_model,
            code="unknown_window_role",
            message=f"no {step.window_role!r} window in the question",
        )

    query_filters: list[dict[str, Any]] = [_filter_arg(f) for f in step.filters]
    if step.entities_from_step is not None:
        source = referents.get(step.entities_from_step)
        if source is None or source.error is not None or not source.rows:
            return _error(
                step,
                windows=windows,
                read_model=read_model,
                code="step_dependency_failed",
                message=(
                    f"step {step.step_id!r} needs entities from step {step.entities_from_step!r}, "
                    "which produced no usable rows"
                ),
            )
        for dim, values in _entity_values(source).items():
            query_filters.append({"dimension": dim, "operator": "equals", "values": values})

    elapsed_through: int | None = None
    # A day that has already finished needs no elapsed-hours cut: read it whole (replays, evals,
    # and plans made just before midnight).
    if step.elapsed_only and in_progress_day(as_of) is not None:
        elapsed_through, error = _elapsed_error(step, window, as_of, read_model)
        if error is not None:
            return _error(step, windows=windows, read_model=read_model, code=error[0], message=error[1])

    granularity: str | None = None
    if elapsed_through is not None:
        granularity = "hour"
    elif step.grain not in (None, "none"):
        granularity = step.grain

    args = MetricsQueryArgs(
        measures=(step.metric_id,),
        time_range={"start": window.start.isoformat(), "end": window.end.isoformat()},
        dimensions=step.breakdowns,
        filters=tuple(query_filters),
        granularity=granularity,
        limit=step.ranking.limit if step.ranking else None,
        sort=_sort_arg(step.ranking),
    )

    if port is None:
        response = QueryResponse(
            error=CatalogueError(
                code="CATALOGUE_UNAVAILABLE",
                message="no semantic port in this deployment",
                retryable=True,
            )
        )
    else:
        response = await port.query(args)
    if response.error is not None:
        return _error(
            step,
            windows=windows,
            read_model=read_model,
            code=response.error.code or "query_failed",
            message=response.error.message or "metrics query failed",
            retryable=response.error.retryable,
        )

    evidence_index: dict[str, str] = {}
    source_query: dict[str, Any] = args.as_query()
    if elapsed_through is not None:
        rows = _elapsed_rows(
            step,
            response.rows,
            window=window,
            as_of=as_of,
            cutoff=elapsed_through,
            source_query=source_query,
            evidence_index=evidence_index,
            evidence_store=evidence_store,
            read_model=read_model,
        )
    else:
        rows = _plain_rows(
            step,
            response.rows,
            window=window,
            as_of=as_of,
            source_query=source_query,
            evidence_index=evidence_index,
            evidence_store=evidence_store,
            read_model=read_model,
        )

    return ResultSet(
        ref=Ref(kind="result_set", id=step.step_id),
        step_id=step.step_id,
        metric_id=step.metric_id,
        unit=read_model.unit_for(step.metric_id),
        window=window,
        breakdowns=step.breakdowns,
        filters=step.filters,
        rows=rows,
    )


def _error(
    step: QueryStep,
    *,
    windows: Mapping[str, WindowSpec],
    read_model: SemanticReadModel,
    code: str,
    message: str,
    retryable: bool = False,
) -> ResultSet:
    return ResultSet(
        ref=Ref(kind="result_set", id=step.step_id),
        step_id=step.step_id,
        metric_id=step.metric_id,
        unit=read_model.unit_for(step.metric_id),
        window=windows.get(step.window_role) or _DEFAULT_WINDOW,
        breakdowns=step.breakdowns,
        filters=step.filters,
        rows=(),
        error=StageError(stage="plan", code=code, message=message, retryable=retryable),
    )


def _filter_arg(f: FilterSpec) -> dict[str, Any]:
    operator = "notEquals" if f.operator == "not_in" else "equals"
    return {"dimension": f.dimension, "operator": operator, "values": list(f.values)}


def _sort_arg(ranking: RankingSpec | None) -> tuple[dict[str, str], ...]:
    if ranking is None:
        return ()
    return ({"field": ranking.by_metric_id, "direction": ranking.order},)


def _entity_values(source: ResultSet) -> dict[str, list[str]]:
    dims = list(source.breakdowns) or list({k for row in source.rows for k in row.dimensions})
    values: dict[str, list[str]] = {}
    for dim in dims:
        present = sorted({row.dimensions[dim] for row in source.rows if dim in row.dimensions})
        if present:
            values[dim] = present
    return values


def _elapsed_error(
    step: QueryStep, window: WindowSpec, as_of: datetime, read_model: SemanticReadModel
) -> tuple[int, tuple[str, str] | None]:
    if in_progress_day(as_of) is None:
        return 0, (
            "unsupported_query",
            f"elapsed_only compares against today's elapsed hours, but {as_of.date()} is complete",
        )
    cutoff = completed_hours(as_of)
    if cutoff == 0:
        return 0, ("insufficient_evidence", "today has no complete hour yet")
    aggregation = read_model.aggregation_for(step.metric_id)
    if aggregation != "additive":
        return 0, (
            "unsupported_query",
            (
                f"elapsed_only needs an additive metric; {step.metric_id} is "
                f"{aggregation or 'not declared additive'}"
            ),
        )
    return cutoff, None


def _plain_rows(
    step: QueryStep,
    payload_rows: Sequence[Mapping[str, Any]],
    *,
    window: WindowSpec,
    as_of: datetime,
    source_query: dict[str, Any],
    evidence_index: dict[str, str],
    evidence_store: EvidenceStore | None,
    read_model: SemanticReadModel,
) -> tuple[ResultRow, ...]:
    grain = _evidence_grain(step.grain)
    unit = read_model.unit_for(step.metric_id)
    period_end = datetime.combine(window.end, day_time.min, as_of.tzinfo)
    rows: list[ResultRow] = []
    for payload_row in payload_rows:
        raw = dict(payload_row)
        value = _as_float(raw.get(step.metric_id))
        if value is None:
            continue
        row_dimensions = _row_dimensions(step.breakdowns, raw)
        if row_dimensions is None:
            continue
        bucket_date = _row_date(raw) if grain != "none" else None
        period_start = (
            datetime.fromisoformat(bucket_date).replace(tzinfo=as_of.tzinfo)
            if bucket_date
            else datetime.combine(window.start, day_time.min, as_of.tzinfo)
        )
        bucket_end = _bucket_end(period_start, grain) if bucket_date else period_end
        evidence_id = _put_evidence_row(
            step=step,
            dimensions=row_dimensions,
            grain=grain,
            as_of=as_of,
            period_start=period_start,
            period_end=bucket_end,
            value=value,
            unit=unit,
            source_query=source_query,
            evidence_index=evidence_index,
            evidence_store=evidence_store,
        )
        if bucket_date:
            # The bucket's first day travels with the row, so per-period answers keep their time axis.
            row_dimensions = {**row_dimensions, PERIOD_KEY: bucket_date}
        rows.append(ResultRow(evidence_id=evidence_id, dimensions=row_dimensions, value=value))
    return tuple(rows)


def _elapsed_rows(
    step: QueryStep,
    payload_rows: Sequence[Mapping[str, Any]],
    *,
    window: WindowSpec,
    as_of: datetime,
    cutoff: int,
    source_query: dict[str, Any],
    evidence_index: dict[str, str],
    evidence_store: EvidenceStore | None,
    read_model: SemanticReadModel,
) -> tuple[ResultRow, ...]:
    by_day = step.grain == "day"
    today = in_progress_day(as_of)
    period_to_date = today is not None and window.end == today
    totals: dict[tuple[str | None, tuple[str, ...]], float] = {}
    for payload_row in payload_rows:
        raw = dict(payload_row)
        hour = _row_hour(raw)
        value = _as_float(raw.get(step.metric_id))
        if hour is None or value is None:
            continue
        if hour.hour >= cutoff and (not period_to_date or hour.date() == window.end):
            continue
        if not window.start <= hour.date() <= window.end:
            continue
        labels = tuple(str(_dimension_value(raw, dim)) for dim in step.breakdowns)
        if any(label in _EMPTY_LABELS for label in labels):
            continue
        key = (hour.date().isoformat() if by_day else None, labels)
        totals[key] = totals.get(key, 0.0) + value

    unit = read_model.unit_for(step.metric_id)
    through = f"{cutoff:02d}:00"
    rows: list[ResultRow] = []
    for (day, labels), total in totals.items():
        row_dimensions = dict(zip(step.breakdowns, labels, strict=True))
        row_dimensions[ELAPSED_KEY] = through
        period_start = (
            datetime.fromisoformat(day).replace(tzinfo=as_of.tzinfo)
            if day
            else datetime.combine(window.start, day_time.min, as_of.tzinfo)
        )
        period_end = period_start if day else datetime.combine(window.end, day_time.min, as_of.tzinfo)
        evidence_id = _put_evidence_row(
            step=step,
            dimensions=row_dimensions,
            grain="none",  # result rows are period aggregates in either case
            as_of=as_of,
            period_start=period_start,
            period_end=period_end,
            value=total,
            unit=unit,
            source_query=source_query,
            evidence_index=evidence_index,
            evidence_store=evidence_store,
        )
        rows.append(ResultRow(evidence_id=evidence_id, dimensions=row_dimensions, value=total))
    return tuple(rows)


def _put_evidence_row(
    *,
    step: QueryStep,
    dimensions: dict[str, str],
    grain: str,
    as_of: datetime,
    period_start: datetime,
    period_end: datetime,
    value: float,
    unit: str | None,
    source_query: dict[str, Any],
    evidence_index: dict[str, str],
    evidence_store: EvidenceStore | None,
) -> str:
    evidence = EvidenceArtifact(
        metric_id=step.metric_id,
        dimensions=dimensions,
        grain=grain,  # type: ignore[arg-type]
        as_of=as_of,
        period_start=period_start,
        period_end=period_end,
        value=value,
        unit=unit,
        source_query=source_query,
    )
    payload = evidence.model_dump(mode="json")
    identity = _evidence_identity(payload)
    if (existing := evidence_index.get(identity)) is not None:
        return existing
    evidence_id = "evidence-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    evidence_index[identity] = evidence_id
    if evidence_store is not None:
        evidence_store.put_evidence(payload, evidence_id)
    return evidence_id


def _evidence_identity(payload: dict[str, Any]) -> str:
    return json.dumps({k: payload.get(k) for k in _EVIDENCE_IDENTITY_FIELDS}, sort_keys=True, default=str)


def _evidence_grain(grain: str | None) -> str:
    return grain if grain in ("day", "week", "month", "none") else "none"


def _row_dimensions(breakdowns: Sequence[str], raw: dict[str, Any]) -> dict[str, str] | None:
    dims: dict[str, str] = {}
    for key in breakdowns:
        value = str(_dimension_value(raw, key))
        if value in _EMPTY_LABELS:
            return None
        dims[key] = value
    return dims


def _dimension_value(raw: dict[str, Any], dim_id: str) -> Any:
    if dim_id in raw:
        return raw[dim_id]
    suffix = f".{dim_id}"
    for key, value in raw.items():
        if str(key).endswith(suffix):
            return value
    return None


def _row_date(raw: dict[str, Any]) -> str | None:
    """Cube names a grain time dimension ``<view>.<dimension>.<granularity>``."""
    for key, value in raw.items():
        tail = str(key).rsplit(".", 1)[-1]
        if tail in _GRANULARITY_SUFFIXES and isinstance(value, str) and len(value) >= 10:
            return value[:10]
    return None


def _row_hour(raw: dict[str, Any]) -> datetime | None:
    for key, value in raw.items():
        if str(key).endswith(".hour") and isinstance(value, str):
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                continue
    return None


def _bucket_end(bucket_start: datetime, grain: str) -> datetime:
    if grain == "week":
        return bucket_start + timedelta(days=6)
    if grain == "month":
        last = calendar.monthrange(bucket_start.year, bucket_start.month)[1]
        return bucket_start.replace(day=last)
    return bucket_start


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None