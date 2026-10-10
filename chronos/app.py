"""Lightweight FastAPI service for standalone Chronos-2 CPU inference."""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator

from forecast import (
    QUANTILE_LEVELS,
    SeriesInput,
    V2Task,
    get_load_meta,
    get_pipeline,
    predict_series,
    predict_v2,
)

MAX_SERIES = int(os.getenv("MAX_SERIES_PER_REQUEST", "10"))
QUEUE_WAIT_SECONDS = float(os.getenv("QUEUE_WAIT_SECONDS", "15"))
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


class FutureCovariatePayload(BaseModel):
    past: list[float | None]
    future: list[float | None]


class V2TaskPayload(BaseModel):
    task_id: str = Field(..., min_length=1, max_length=128)
    start: str = "2024-01-01"
    freq: str = "D"
    targets: dict[str, list[float | None]] = Field(..., min_length=1)
    past_covariates: dict[str, list[float | None]] = Field(default_factory=dict)
    future_covariates: dict[str, FutureCovariatePayload] = Field(default_factory=dict)

    @field_validator("targets", "past_covariates")
    @classmethod
    def finite_or_null(cls, series: dict[str, list[float | None]]) -> dict[str, list[float | None]]:
        for name, values in series.items():
            for v in values:
                if v is None:
                    continue
                if v != v or v in (float("inf"), float("-inf")):
                    raise ValueError(f"{name}: values must be finite floats or null")
        return series


class V2ForecastRequest(BaseModel):
    tasks: list[V2TaskPayload] = Field(..., min_length=1)
    prediction_length: int = Field(..., ge=1, le=365)
    quantile_levels: list[float] = Field(default_factory=lambda: list(QUANTILE_LEVELS))
    model: str = Field(default="chronos-2", pattern=r"^(chronos-2|chronos-2-small)$")

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

    @model_validator(mode="after")
    def future_lengths(self) -> V2ForecastRequest:
        for task in self.tasks:
            for name, cov in task.future_covariates.items():
                if len(cov.future) != self.prediction_length:
                    raise ValueError(
                        f"task {task.task_id}: future covariate {name} length "
                        f"{len(cov.future)} != prediction_length {self.prediction_length}"
                    )
        return self


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Load default model once at startup so /ready reflects real readiness.
    await asyncio.to_thread(get_pipeline)
    yield


app = FastAPI(
    title="Seleric Chronos-2 Standalone",
    description="CPU-only Amazon Chronos-2 forecasting service for Seleric Agent.",
    version="0.2.0",
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


async def _acquire_infer_lock() -> None:
    """Bounded wait for the single inference slot (replaces immediate 429)."""
    try:
        await asyncio.wait_for(_infer_lock.acquire(), timeout=QUEUE_WAIT_SECONDS)
    except TimeoutError as exc:
        raise HTTPException(
            status_code=429,
            detail=f"inference busy; waited {QUEUE_WAIT_SECONDS}s",
        ) from exc


@app.post("/predict")
async def predict(body: PredictRequest) -> dict[str, Any]:
    await _acquire_infer_lock()
    try:
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
    finally:
        _infer_lock.release()


@app.post("/v2/forecast")
async def forecast_v2(body: V2ForecastRequest) -> dict[str, Any]:
    await _acquire_infer_lock()
    try:
        # Ensure requested model is loaded (small is lazy).
        await asyncio.to_thread(get_pipeline, body.model)
        tasks = [
            V2Task(
                task_id=item.task_id,
                start=item.start,
                freq=item.freq,
                targets=item.targets,
                past_covariates=item.past_covariates,
                future_covariates={
                    name: {"past": cov.past, "future": cov.future}
                    for name, cov in item.future_covariates.items()
                },
            )
            for item in body.tasks
        ]
        try:
            result = await asyncio.to_thread(
                predict_v2,
                tasks,
                body.prediction_length,
                body.quantile_levels,
                body.model,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return result
    finally:
        _infer_lock.release()
