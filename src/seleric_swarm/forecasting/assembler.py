"""Point-in-time feature frame via ``semantic.raw_query_metric``."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import date, timedelta
from typing import Any

from seleric_swarm.forecasting import calendar as cal
from seleric_swarm.forecasting.gates import apply_temporal_cutoff, cutoff_for
from seleric_swarm.forecasting.quality import assess_series, dropped_features, is_blocking
from seleric_swarm.forecasting.types import FeatureFrame, GateDecision, QualityVerdict
from seleric_swarm.services.mcp_query import row_date
from seleric_swarm.toolsets.semantic import raw_query_metric

_log = logging.getLogger("seleric.forecasting.assembler")

_AGENT_ID = "v3_agent"


async def assemble_feature_frame(
    *,
    mcp_client: Any,
    target_ids: list[str],
    past_covariate_ids: list[str],
    known_future_names: list[str],
    cutoff: date,
    context_days: int,
    horizon_start: date,
    horizon_end: date,
    maturity_days: int = 0,
    nonnegative_targets: set[str] | None = None,
    min_history_days: int = 120,
    agent_id: str = _AGENT_ID,
    dimensions: list[str] | None = None,
    filters: list[dict[str, Any]] | None = None,
) -> FeatureFrame:
    """Fetch daily series, reindex with nulls (never dropna), attach calendar future."""
    context_start = cutoff - timedelta(days=max(1, context_days) - 1)
    context_index = _daterange(context_start, cutoff)
    horizon_index = _daterange(horizon_start, horizon_end)
    full_index = context_index + horizon_index

    metric_ids = list(dict.fromkeys([*target_ids, *past_covariate_ids]))
    fetched = await _fetch_many(
        mcp_client,
        metric_ids=metric_ids,
        start=context_start,
        end=cutoff,
        agent_id=agent_id,
        dimensions=dimensions,
        filters=filters,
    )

    gate_decisions: list[GateDecision] = []
    quality_verdicts: list[QualityVerdict] = []
    source_queries: dict[str, dict[str, Any]] = {}
    targets: dict[str, list[float | None]] = {}
    past_covariates: dict[str, list[float | None]] = {}
    masks: dict[str, list[bool]] = {}

    expected_last = cutoff
    nn = nonnegative_targets or set()

    for mid in target_ids:
        series, query = fetched.get(mid, ({}, {}))
        source_queries[mid] = query
        values = [series.get(d.isoformat()) for d in context_index]
        values, g = apply_temporal_cutoff(values, context_index, cutoff=cutoff, series=mid)
        gate_decisions.extend(g)
        values, q = assess_series(
            mid,
            values,
            context_index,
            role="target",
            min_history_days=min_history_days,
            expected_last_day=expected_last,
            nonnegative=mid in nn,
        )
        quality_verdicts.extend(q)
        targets[mid] = values
        masks[mid] = [v is None for v in values]

    drop = dropped_features(quality_verdicts)
    for mid in past_covariate_ids:
        if mid in drop:
            continue
        series, query = fetched.get(mid, ({}, {}))
        source_queries[mid] = query
        values = [series.get(d.isoformat()) for d in context_index]
        # Feature maturity: mask immature tail relative to its own maturity if any;
        # P0 uses the target cutoff for all past covariates.
        values, g = apply_temporal_cutoff(values, context_index, cutoff=cutoff, series=mid)
        gate_decisions.extend(g)
        values, q = assess_series(
            mid,
            values,
            context_index,
            role="feature",
            min_history_days=min(min_history_days, 60),
            expected_last_day=expected_last,
        )
        quality_verdicts.extend(q)
        if any(v.action == "drop" for v in q if v.series == mid):
            continue
        past_covariates[mid] = values
        masks[mid] = [v is None for v in values]

    # Trim leading nulls that only exist because the requested context_days
    # predate warehouse retention — engines see the real observed span.
    trim_i = _first_any_observation(targets)
    if trim_i is None:
        trim_i = 0
    elif trim_i > 0:
        gate_decisions.append(
            GateDecision(
                code="T_TRIM_LEADING",
                series=",".join(target_ids),
                action="mask",
                detail=(
                    f"trimmed {trim_i} leading empty day(s) before first observation "
                    f"(requested context_start={context_start.isoformat()})"
                ),
            )
        )
        context_start = context_index[trim_i]
        context_index = context_index[trim_i:]
        targets = {k: v[trim_i:] for k, v in targets.items()}
        past_covariates = {k: v[trim_i:] for k, v in past_covariates.items()}
        masks = {k: v[trim_i:] for k, v in masks.items()}
        full_index = context_index + horizon_index

    # The maturity cutoff can sit well before the horizon. Keep the timeline
    # continuous: the immature days between them are present but unobserved
    # (null), so engines forecast across them and calendar covariates line up
    # with the real dates (weekday, festival) instead of jumping.
    gap_days = _daterange(context_index[-1] + timedelta(days=1), horizon_start - timedelta(days=1)) if context_index else []
    if gap_days:
        pad = [None] * len(gap_days)
        context_index = context_index + gap_days
        targets = {k: [*v, *pad] for k, v in targets.items()}
        past_covariates = {k: [*v, *pad] for k, v in past_covariates.items()}
        masks = {k: [*v, *([True] * len(gap_days))] for k, v in masks.items()}
        full_index = context_index + horizon_index

    # Calendar known-future over full context+horizon index
    cal_names = cal.feature_names_from_policy(known_future_names)
    cal_full = cal.generate_calendar_features(full_index, names=cal_names)
    future_covariates = cal.split_past_future(
        cal_full, context_len=len(context_index), horizon_len=len(horizon_index)
    )

    frame = FeatureFrame(
        cutoff=cutoff,
        context_start=context_start,
        horizon_start=horizon_start,
        horizon_end=horizon_end,
        index=[d.isoformat() for d in full_index],
        targets=targets,
        past_covariates=past_covariates,
        future_covariates=future_covariates,
        masks=masks,
        source_queries=source_queries,
        gate_decisions=gate_decisions,
        quality_verdicts=quality_verdicts,
    )
    frame.content_hash = content_hash(frame)
    if is_blocking(quality_verdicts):
        _log.info(
            "forecast_frame_blocked hash=%s verdicts=%s",
            frame.content_hash,
            [v.model_dump() for v in quality_verdicts if v.action in {"block", "refuse"}],
        )
    return frame


def content_hash(frame: FeatureFrame) -> str:
    payload = {
        "cutoff": frame.cutoff.isoformat(),
        "context_start": frame.context_start.isoformat(),
        "horizon_start": frame.horizon_start.isoformat(),
        "horizon_end": frame.horizon_end.isoformat(),
        "targets": frame.targets,
        "past_covariates": frame.past_covariates,
        "future_covariates": frame.future_covariates,
    }
    blob = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


def compute_cutoff(*, as_of: date, maturity_days: int) -> date:
    return cutoff_for(as_of=as_of, maturity_days=maturity_days)


def _daterange(start: date, end: date) -> list[date]:
    if end < start:
        return []
    out: list[date] = []
    d = start
    while d <= end:
        out.append(d)
        d += timedelta(days=1)
    return out


def _first_any_observation(targets: dict[str, list[float | None]]) -> int | None:
    """Earliest index where any target has a non-null value; None if all empty."""
    if not targets:
        return None
    length = len(next(iter(targets.values())))
    for i in range(length):
        if any((vals[i] is not None) for vals in targets.values() if i < len(vals)):
            return i
    return None


async def _fetch_many(
    mcp_client: Any,
    *,
    metric_ids: list[str],
    start: date,
    end: date,
    agent_id: str,
    dimensions: list[str] | None,
    filters: list[dict[str, Any]] | None,
    concurrency: int = 6,
) -> dict[str, tuple[dict[str, float], dict[str, Any]]]:
    sem = asyncio.Semaphore(concurrency)

    async def _one(mid: str) -> tuple[str, dict[str, float], dict[str, Any]]:
        query = {
            "metric_id": mid,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "grain": "day",
            "dimensions": dimensions or [],
            "filters": filters or [],
        }
        async with sem:
            try:
                result = await raw_query_metric(
                    mcp_client,
                    agent_id=agent_id,
                    metric_id=mid,
                    start=start.isoformat(),
                    end=end.isoformat(),
                    grain="day",
                    dimensions=dimensions,
                    filters=filters,
                )
            except Exception as exc:  # noqa: BLE001
                _log.warning("forecast_fetch_failed metric=%s err=%s", mid, exc)
                return mid, {}, {**query, "error": type(exc).__name__}
        day_values: dict[str, float] = {}
        if result.get("error"):
            return mid, {}, {**query, "error": result.get("error")}
        for row in result.get("rows") or []:
            if not isinstance(row, dict):
                continue
            ts = row_date(row)
            raw = row.get(mid)
            if ts is None or raw is None:
                continue
            try:
                day_values[ts] = float(raw)
            except (TypeError, ValueError):
                continue
        return mid, day_values, query

    gathered = await asyncio.gather(*[_one(mid) for mid in metric_ids]) if metric_ids else []
    return {mid: (vals, q) for mid, vals, q in gathered}
