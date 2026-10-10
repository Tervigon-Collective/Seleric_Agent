"""Chronos-2 CPU inference helpers (singleton model load)."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
import torch

MODEL_ID = os.getenv("MODEL_ID", "amazon/chronos-2")
MODEL_ID_SMALL = os.getenv("MODEL_ID_SMALL", "autogluon/chronos-2-small")
MODEL_REVISION = os.getenv("MODEL_REVISION", "") or None
DEVICE_MAP = os.getenv("DEVICE_MAP", "cpu")
TORCH_DTYPE = os.getenv("TORCH_DTYPE", "float32")
DEFAULT_BATCH_SIZE = int(os.getenv("BATCH_SIZE", "1"))
QUANTILE_LEVELS = (0.1, 0.5, 0.9)
MAX_TARGETS_PER_TASK = int(os.getenv("MAX_TARGETS_PER_TASK", "4"))
MAX_COVARIATES_PER_TASK = int(os.getenv("MAX_COVARIATES_PER_TASK", "8"))
MAX_TASKS_PER_REQUEST = int(os.getenv("MAX_TASKS_PER_REQUEST", "8"))

_lock = threading.Lock()
_pipelines: dict[str, Any] = {}
_pipeline = None  # back-compat alias for default MODEL_ID
_load_meta: dict[str, Any] = {
    "loaded": False,
    "model_id": MODEL_ID,
    "revision": MODEL_REVISION,
    "device_map": DEVICE_MAP,
    "load_seconds": None,
    "load_count": 0,
    "predict_count": 0,
    "loaded_models": [],
}


def _configure_cpu_threads() -> None:
    threads = int(os.getenv("TORCH_NUM_THREADS", "2"))
    torch.set_num_threads(threads)
    # Interop threads can be set only once per process. The base model loads
    # first; a later small-model load must not crash the request.
    try:
        torch.set_num_interop_threads(1)
    except RuntimeError:
        pass


def get_load_meta() -> dict[str, Any]:
    return dict(_load_meta)


def _model_id_for(label: str | None) -> str:
    if label in {"chronos-2-small", "small"}:
        return MODEL_ID_SMALL
    return MODEL_ID


def get_pipeline(model: str | None = None):
    """Load Chronos-2 once per model label and reuse for subsequent requests."""
    global _pipeline
    model_id = _model_id_for(model)
    cached = _pipelines.get(model_id)
    if cached is not None:
        return cached

    with _lock:
        cached = _pipelines.get(model_id)
        if cached is not None:
            return cached

        _configure_cpu_threads()
        from chronos import Chronos2Pipeline

        dtype = torch.float32 if TORCH_DTYPE == "float32" else "auto"
        started = time.perf_counter()
        kwargs: dict[str, Any] = {"device_map": DEVICE_MAP, "torch_dtype": dtype}
        if MODEL_REVISION:
            kwargs["revision"] = MODEL_REVISION
        pipeline = Chronos2Pipeline.from_pretrained(model_id, **kwargs)
        # Warm a tiny forward so first /predict is not dominated by lazy init.
        # Chronos-2 tensor path requires shape (n_series, n_variates, history_length).
        warm_ctx = torch.randn(1, 1, 32, dtype=torch.float32)
        _ = pipeline.predict(warm_ctx, prediction_length=1, batch_size=1)
        elapsed = time.perf_counter() - started

        _pipelines[model_id] = pipeline
        if model_id == MODEL_ID:
            _pipeline = pipeline
        loaded = list(_load_meta.get("loaded_models") or [])
        if model_id not in loaded:
            loaded.append(model_id)
        _load_meta.update(
            {
                "loaded": True,
                "model_id": model_id,
                "revision": MODEL_REVISION,
                "device_map": DEVICE_MAP,
                "load_seconds": round(elapsed, 3),
                "load_count": int(_load_meta["load_count"]) + 1,
                "loaded_models": loaded,
            }
        )
        return pipeline


@dataclass
class SeriesInput:
    series_id: str
    values: list[float]
    start: str | None = None
    freq: str = "D"


@dataclass
class ForecastRequest:
    series: list[SeriesInput]
    prediction_length: int
    quantile_levels: list[float] = field(default_factory=lambda: list(QUANTILE_LEVELS))
    batch_size: int = DEFAULT_BATCH_SIZE


def _extract_quantiles(row: pd.Series, q_levels: list[float]) -> dict[str, float]:
    """Map Chronos predict_df quantile columns to p10/p50/p90-style keys."""
    quantiles: dict[str, float] = {}
    for q in q_levels:
        key_candidates = (q, str(q), f"{q:.1f}", f"{q:.2f}", f"q{q}", f"p{int(round(q * 100))}")
        value = None
        for key in key_candidates:
            if key in row.index:
                value = float(row[key])
                break
        if value is None:
            for col in row.index:
                if str(col) in {str(q), f"{q:.1f}", f"{q:.2f}"}:
                    value = float(row[col])
                    break
        if value is None:
            raise KeyError(f"missing quantile {q} in prediction columns: {list(row.index)}")
        quantiles[f"p{int(round(q * 100))}"] = value
    return quantiles


def _to_context_df(series: list[SeriesInput]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for item in series:
        n = len(item.values)
        if n < 2:
            raise ValueError(f"series {item.series_id!r} needs at least 2 observations")
        start = pd.Timestamp(item.start or "2024-01-01")
        timestamps = pd.date_range(start=start, periods=n, freq=item.freq)
        frames.append(
            pd.DataFrame(
                {
                    "id": item.series_id,
                    "timestamp": timestamps,
                    "target": np.asarray(item.values, dtype=np.float64),
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


def predict_series(
    series: list[SeriesInput],
    prediction_length: int,
    quantile_levels: list[float] | None = None,
    batch_size: int | None = None,
) -> dict[str, Any]:
    """Run Chronos-2 forecast; batches series for memory safety."""
    if prediction_length < 1:
        raise ValueError("prediction_length must be >= 1")
    if not series:
        raise ValueError("series must be non-empty")

    q_levels = quantile_levels or list(QUANTILE_LEVELS)
    if sorted(q_levels) != q_levels:
        raise ValueError("quantile_levels must be ascending")
    bs = max(1, batch_size or DEFAULT_BATCH_SIZE)

    pipeline = get_pipeline()
    started = time.perf_counter()
    forecasts: list[dict[str, Any]] = []

    # Memory-safe batching across series ids.
    for offset in range(0, len(series), bs):
        chunk = series[offset : offset + bs]
        context_df = _to_context_df(chunk)
        pred_df = pipeline.predict_df(
            context_df,
            prediction_length=prediction_length,
            quantile_levels=q_levels,
            id_column="id",
            timestamp_column="timestamp",
            target="target",
            batch_size=bs,
        )

        for series_id, group in pred_df.groupby("id", sort=False):
            group = group.sort_values("timestamp")
            points: list[dict[str, Any]] = []
            for _, row in group.iterrows():
                quantiles = _extract_quantiles(row, q_levels)
                mean_val = float(row["predictions"]) if "predictions" in row.index else quantiles["p50"]
                points.append(
                    {
                        "timestamp": pd.Timestamp(row["timestamp"]).isoformat(),
                        "mean": mean_val,
                        **quantiles,
                    }
                )

            # Validate quantile ordering per timestep
            for point in points:
                if not (point["p10"] <= point["p50"] <= point["p90"]):
                    raise ValueError(
                        f"quantile ordering violated for {series_id}: "
                        f"p10={point['p10']} p50={point['p50']} p90={point['p90']}"
                    )

            forecasts.append(
                {
                    "series_id": str(series_id),
                    "history_length": len(next(s.values for s in chunk if s.series_id == series_id)),
                    "prediction_length": prediction_length,
                    "forecast": points,
                }
            )

    elapsed = time.perf_counter() - started
    with _lock:
        _load_meta["predict_count"] = int(_load_meta["predict_count"]) + 1
        predict_count = _load_meta["predict_count"]
        load_count = _load_meta["load_count"]

    return {
        "model_id": MODEL_ID,
        "device": DEVICE_MAP,
        "quantile_levels": q_levels,
        "inference_seconds": round(elapsed, 4),
        "model_load_count": load_count,
        "predict_count": predict_count,
        "series_count": len(series),
        "note": "Synthetic/test forecasts only — not real business predictions.",
        "forecasts": forecasts,
    }


@dataclass
class V2Task:
    """One multivariate / covariate forecast task for /v2/forecast."""

    task_id: str
    start: str
    freq: str
    targets: dict[str, list[float | None]]
    past_covariates: dict[str, list[float | None]] = field(default_factory=dict)
    future_covariates: dict[str, dict[str, list[float | None]]] = field(default_factory=dict)


def _as_float_or_nan(values: list[float | None]) -> np.ndarray:
    out = np.empty(len(values), dtype=np.float64)
    for i, v in enumerate(values):
        if v is None:
            out[i] = np.nan
        else:
            fv = float(v)
            if fv != fv or fv in (float("inf"), float("-inf")):
                raise ValueError("values must be finite floats or null")
            out[i] = fv
    return out


def validate_v2_tasks(
    tasks: list[V2Task],
    *,
    prediction_length: int,
) -> None:
    if not tasks:
        raise ValueError("tasks must be non-empty")
    if len(tasks) > MAX_TASKS_PER_REQUEST:
        raise ValueError(f"at most {MAX_TASKS_PER_REQUEST} tasks per request")
    for task in tasks:
        if not task.targets:
            raise ValueError(f"task {task.task_id!r}: targets required")
        if len(task.targets) > MAX_TARGETS_PER_TASK:
            raise ValueError(f"task {task.task_id!r}: at most {MAX_TARGETS_PER_TASK} targets")
        lengths = {len(v) for v in task.targets.values()}
        if len(lengths) != 1:
            raise ValueError(f"task {task.task_id!r}: target series must share equal length")
        ctx_len = next(iter(lengths))
        if ctx_len < 2:
            raise ValueError(f"task {task.task_id!r}: context length must be >= 2")
        n_cov = len(task.past_covariates) + len(task.future_covariates)
        if n_cov > MAX_COVARIATES_PER_TASK:
            raise ValueError(f"task {task.task_id!r}: at most {MAX_COVARIATES_PER_TASK} covariates")
        for name, vals in task.past_covariates.items():
            if len(vals) != ctx_len:
                raise ValueError(
                    f"task {task.task_id!r}: past covariate {name!r} length {len(vals)} != {ctx_len}"
                )
        for name, parts in task.future_covariates.items():
            past = parts.get("past") or []
            future = parts.get("future") or []
            if len(past) != ctx_len:
                raise ValueError(
                    f"task {task.task_id!r}: future covariate {name!r} past length mismatch"
                )
            if len(future) != prediction_length:
                raise ValueError(
                    f"task {task.task_id!r}: future covariate {name!r} future length "
                    f"{len(future)} != prediction_length {prediction_length}"
                )


def _task_to_frames(
    task: V2Task, prediction_length: int
) -> tuple[pd.DataFrame, pd.DataFrame | None, list[str]]:
    """Build Chronos context_df / future_df for one task group."""
    target_names = list(task.targets.keys())
    ctx_len = len(next(iter(task.targets.values())))
    start = pd.Timestamp(task.start or "2024-01-01")
    ctx_ts = pd.date_range(start=start, periods=ctx_len, freq=task.freq)
    # One Chronos group id per task — multivariate targets share the group.
    group_id = task.task_id
    row: dict[str, Any] = {"id": [group_id] * ctx_len, "timestamp": ctx_ts}
    for name, vals in task.targets.items():
        row[name] = _as_float_or_nan(vals)
    for name, vals in task.past_covariates.items():
        row[name] = _as_float_or_nan(vals)
    for name, parts in task.future_covariates.items():
        row[name] = _as_float_or_nan(list(parts.get("past") or []))
    context_df = pd.DataFrame(row)

    future_df: pd.DataFrame | None = None
    if task.future_covariates:
        fut_ts = pd.date_range(
            start=ctx_ts[-1] + pd.tseries.frequencies.to_offset(task.freq),
            periods=prediction_length,
            freq=task.freq,
        )
        fut: dict[str, Any] = {"id": [group_id] * prediction_length, "timestamp": fut_ts}
        for name, parts in task.future_covariates.items():
            fut[name] = _as_float_or_nan(list(parts.get("future") or []))
        future_df = pd.DataFrame(fut)
    return context_df, future_df, target_names


def predict_v2(
    tasks: list[V2Task],
    prediction_length: int,
    quantile_levels: list[float] | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Multivariate / covariate Chronos-2 forecast (nulls → NaN, masked natively)."""
    validate_v2_tasks(tasks, prediction_length=prediction_length)
    q_levels = quantile_levels or list(QUANTILE_LEVELS)
    if sorted(q_levels) != q_levels:
        raise ValueError("quantile_levels must be ascending")

    pipeline = get_pipeline(model)
    started = time.perf_counter()
    results: list[dict[str, Any]] = []

    for task in tasks:
        context_df, future_df, target_names = _task_to_frames(task, prediction_length)
        kwargs: dict[str, Any] = {
            "df": context_df,
            "prediction_length": prediction_length,
            "quantile_levels": q_levels,
            "id_column": "id",
            "timestamp_column": "timestamp",
            "target": target_names if len(target_names) > 1 else target_names[0],
            "batch_size": 1,
        }
        if future_df is not None:
            kwargs["future_df"] = future_df
        pred_df = pipeline.predict_df(**kwargs)

        # predict_df returns rows per target when multivariate; normalise.
        by_target: dict[str, list[dict[str, Any]]] = {n: [] for n in target_names}
        if "target_name" in pred_df.columns or "item_id" in pred_df.columns:
            key = "target_name" if "target_name" in pred_df.columns else "item_id"
            for tname, group in pred_df.groupby(key, sort=False):
                tname_s = str(tname)
                if tname_s not in by_target and len(target_names) == 1:
                    tname_s = target_names[0]
                group = group.sort_values("timestamp")
                for _, row in group.iterrows():
                    quantiles = _extract_quantiles(row, q_levels)
                    mean_val = (
                        float(row["predictions"]) if "predictions" in row.index else quantiles["p50"]
                    )
                    point = {
                        "timestamp": pd.Timestamp(row["timestamp"]).isoformat(),
                        "mean": mean_val,
                        **quantiles,
                    }
                    vals = [point[f"p{int(round(q*100))}"] for q in q_levels]
                    if vals != sorted(vals):
                        raise ValueError(
                            f"quantile ordering violated for {task.task_id}/{tname_s}: {vals}"
                        )
                    by_target.setdefault(tname_s, []).append(point)
        else:
            # Univariate-style output for a single target column.
            group = pred_df.sort_values("timestamp")
            tname_s = target_names[0]
            for _, row in group.iterrows():
                quantiles = _extract_quantiles(row, q_levels)
                mean_val = (
                    float(row["predictions"]) if "predictions" in row.index else quantiles["p50"]
                )
                point = {
                    "timestamp": pd.Timestamp(row["timestamp"]).isoformat(),
                    "mean": mean_val,
                    **quantiles,
                }
                vals = [point[f"p{int(round(q*100))}"] for q in q_levels]
                if vals != sorted(vals):
                    raise ValueError(f"quantile ordering violated for {task.task_id}/{tname_s}: {vals}")
                by_target[tname_s].append(point)

        results.append(
            {
                "task_id": task.task_id,
                "targets": {
                    name: {
                        "history_length": len(task.targets[name]),
                        "prediction_length": prediction_length,
                        "forecast": points,
                    }
                    for name, points in by_target.items()
                    if name in task.targets
                },
            }
        )

    elapsed = time.perf_counter() - started
    with _lock:
        _load_meta["predict_count"] = int(_load_meta["predict_count"]) + 1
        predict_count = _load_meta["predict_count"]
        load_count = _load_meta["load_count"]

    import hashlib
    import json

    input_hash = hashlib.sha256(
        json.dumps(
            [
                {
                    "task_id": t.task_id,
                    "targets": {k: v for k, v in t.targets.items()},
                    "past": t.past_covariates,
                    "future": t.future_covariates,
                }
                for t in tasks
            ],
            sort_keys=True,
            default=str,
        ).encode()
    ).hexdigest()[:16]

    return {
        "model_id": _model_id_for(model),
        "revision": MODEL_REVISION,
        "device": DEVICE_MAP,
        "quantile_levels": q_levels,
        "inference_seconds": round(elapsed, 4),
        "model_load_count": load_count,
        "predict_count": predict_count,
        "task_count": len(tasks),
        "input_hash": input_hash,
        "note": "Synthetic/test forecasts only — not real business predictions.",
        "tasks": results,
    }


def make_synthetic_series(
    n_obs: int,
    series_id: str = "revenue",
    start: str = "2024-01-01",
    seed: int = 42,
    base: float = 1000.0,
    trend: float = 1.5,
    season_amp: float = 120.0,
) -> SeriesInput:
    rng = np.random.default_rng(seed)
    t = np.arange(n_obs, dtype=np.float64)
    values = (
        base
        + trend * t
        + season_amp * np.sin(2 * np.pi * t / 7.0)
        + rng.normal(0.0, 40.0, size=n_obs)
    )
    values = np.clip(values, 1.0, None)
    return SeriesInput(
        series_id=series_id,
        values=[float(v) for v in values],
        start=start,
        freq="D",
    )
