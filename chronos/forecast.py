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
DEVICE_MAP = os.getenv("DEVICE_MAP", "cpu")
TORCH_DTYPE = os.getenv("TORCH_DTYPE", "float32")
DEFAULT_BATCH_SIZE = int(os.getenv("BATCH_SIZE", "1"))
QUANTILE_LEVELS = (0.1, 0.5, 0.9)

_lock = threading.Lock()
_pipeline = None
_load_meta: dict[str, Any] = {
    "loaded": False,
    "model_id": MODEL_ID,
    "device_map": DEVICE_MAP,
    "load_seconds": None,
    "load_count": 0,
    "predict_count": 0,
}


def _configure_cpu_threads() -> None:
    threads = int(os.getenv("TORCH_NUM_THREADS", "2"))
    torch.set_num_threads(threads)
    torch.set_num_interop_threads(1)


def get_load_meta() -> dict[str, Any]:
    return dict(_load_meta)


def get_pipeline():
    """Load Chronos-2 once and reuse for all subsequent requests."""
    global _pipeline
    if _pipeline is not None:
        return _pipeline

    with _lock:
        if _pipeline is not None:
            return _pipeline

        _configure_cpu_threads()
        from chronos import Chronos2Pipeline

        dtype = torch.float32 if TORCH_DTYPE == "float32" else "auto"
        started = time.perf_counter()
        pipeline = Chronos2Pipeline.from_pretrained(
            MODEL_ID,
            device_map=DEVICE_MAP,
            torch_dtype=dtype,
        )
        # Warm a tiny forward so first /predict is not dominated by lazy init.
        # Chronos-2 tensor path requires shape (n_series, n_variates, history_length).
        warm_ctx = torch.randn(1, 1, 32, dtype=torch.float32)
        _ = pipeline.predict(warm_ctx, prediction_length=1, batch_size=1)
        elapsed = time.perf_counter() - started

        _pipeline = pipeline
        _load_meta.update(
            {
                "loaded": True,
                "model_id": MODEL_ID,
                "device_map": DEVICE_MAP,
                "load_seconds": round(elapsed, 3),
                "load_count": _load_meta["load_count"] + 1,
            }
        )
        return _pipeline


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
