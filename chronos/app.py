"""Lightweight FastAPI service for standalone Chronos-2 CPU inference."""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator

from forecast import (
    QUANTILE_LEVELS,
    SeriesInput,
    get_load_meta,
    get_pipeline,
    predict_series,
)

MAX_SERIES = int(os.getenv("MAX_SERIES_PER_REQUEST", "10"))
_infer_lock = asyncio.Lock()


class SeriesPayload(BaseModel):
    series_id: str = Field(..., min_length=1, max_length=128)
    values: list[float] = Field(..., min_length=2)
    start: str | None = "2024-01-01"
    freq: str = "D"

    @field_validator("values")
    @classmethod
    def finite_values(cls, values: list[float]) -> list[float]:
        for v in values:
            if v != v or v in (float("inf"), float("-inf")):
                raise ValueError("values must be finite floats")
        return values


class PredictRequest(BaseModel):
    series: list[SeriesPayload] = Field(..., min_length=1)
    prediction_length: int = Field(..., ge=1, le=365)
    quantile_levels: list[float] = Field(default_factory=lambda: list(QUANTILE_LEVELS))
    batch_size: int = Field(default=1, ge=1, le=10)

    @field_validator("series")
    @classmethod
    def limit_series(cls, series: list[SeriesPayload]) -> list[SeriesPayload]:
        if len(series) > MAX_SERIES:
            raise ValueError(f"at most {MAX_SERIES} series per request")
        return series

    @field_validator("quantile_levels")
    @classmethod
    def check_quantiles(cls, levels: list[float]) -> list[float]:
        if not levels:
            raise ValueError("quantile_levels required")
        if levels != sorted(levels):
            raise ValueError("quantile_levels must be ascending")
        for q in levels:
            if not 0.0 < q < 1.0:
                raise ValueError("quantile_levels must be in (0, 1)")
        return levels


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Load once at startup so /ready reflects real readiness.
    await asyncio.to_thread(get_pipeline)
    yield


app = FastAPI(
    title="Seleric Chronos-2 Standalone",
    description="CPU-only Amazon Chronos-2 forecasting smoke service (not integrated with Seleric Agent).",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "service": "chronos-2"}


@app.get("/ready")
async def ready() -> dict[str, Any]:
    meta = get_load_meta()
    if not meta.get("loaded"):
        raise HTTPException(status_code=503, detail={"status": "not_ready", **meta})
    return {"status": "ready", **meta}


@app.post("/predict")
async def predict(body: PredictRequest) -> dict[str, Any]:
    # Enforce single concurrent inference under the 2-vCPU / 4-GiB budget.
    if _infer_lock.locked():
        raise HTTPException(status_code=429, detail="inference already in progress")

    async with _infer_lock:
        series = [
            SeriesInput(
                series_id=item.series_id,
                values=item.values,
                start=item.start,
                freq=item.freq,
            )
            for item in body.series
        ]
        try:
            result = await asyncio.to_thread(
                predict_series,
                series,
                body.prediction_length,
                body.quantile_levels,
                body.batch_size,
            )
        except Exception as exc:  # noqa: BLE001 - surface inference errors cleanly
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return result
