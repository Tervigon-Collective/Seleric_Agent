"""Forecast engines and router ladder (Chronos /predict, ETS path, seasonal-naive)."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Literal

import httpx

from seleric_swarm.forecasting.types import DailyPoint, FeatureFrame
from seleric_swarm.models.service import Z_80, ForecastUnavailable, forecast_path

_log = logging.getLogger("seleric.forecasting.engines")

EngineName = Literal["chronos-2", "chronos-2-small", "ets", "seasonal-naive"]


@dataclass
class EngineForecast:
    engine: str
    model_id: str
    model_version: str
    revision: str | None
    days: list[DailyPoint]
    warnings: list[str] = field(default_factory=list)
    inference_seconds: float | None = None
    fallback_reason: str | None = None


@dataclass
class RouterDecision:
    engine: str
    reason: str
    tried: list[str] = field(default_factory=list)


class ChronosEngine:
    """httpx client to Chronos ``POST /v2/forecast`` (preferred) or ``/predict``."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 45.0,
        model_label: str = "chronos-2",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.model_label = model_label

    async def forecast(
        self,
        frame: FeatureFrame,
        *,
        target_id: str,
        quantiles: list[float] | None = None,
        use_v2: bool = True,
        features: Any = None,
    ) -> EngineForecast:
        q = quantiles or [0.1, 0.5, 0.9]
        horizon = (frame.horizon_end - frame.horizon_start).days + 1
        if use_v2:
            # Prefer /v2 (nulls + covariates); fall back to /predict when unavailable.
            try:
                return await self._forecast_v2(
                    frame, target_id=target_id, quantiles=q, horizon=horizon, features=features
                )
            except ForecastUnavailable as exc:
                if "chronos_v2" not in (exc.warning or ""):
                    raise
                _log.info("forecast_chronos_v2_fallback reason=%s", exc.warning)
        return await self._forecast_v1(frame, target_id=target_id, quantiles=q, horizon=horizon)

    async def _forecast_v2(
        self,
        frame: FeatureFrame,
        *,
        target_id: str,
        quantiles: list[float],
        horizon: int,
        features: Any = None,
    ) -> EngineForecast:
        values = frame.targets.get(target_id) or []
        if sum(1 for v in values if v is not None) < 8:
            raise ForecastUnavailable(
                f"chronos needs >=8 observed points, got {sum(1 for v in values if v is not None)}",
                warning="chronos_thin_history",
            )
        ctx_len = len(values)
        lo = max(0, ctx_len - 730)
        targets = {target_id: list(values)[lo:]}
        if features is None:
            # Unvalidated default: every assembled covariate.
            for mid, series in frame.targets.items():
                if mid != target_id:
                    targets[mid] = list(series)[lo:]
            past_names = list(frame.past_covariates)
            known_names = list(frame.future_covariates)
        else:
            past_names = [c for c in features.past if c in frame.past_covariates]
            known_names = [k for k in features.known if k in frame.future_covariates]
        past_cov = {k: list(frame.past_covariates[k])[lo:ctx_len] for k in past_names}
        future_cov = {
            k: {
                "past": list((frame.future_covariates[k].get("past") or []))[lo:ctx_len],
                "future": list(frame.future_covariates[k].get("future") or []),
            }
            for k in known_names
        }
        body = {
            "tasks": [
                {
                    "task_id": target_id,
                    "start": (frame.context_start + timedelta(days=lo)).isoformat(),
                    "freq": "D",
                    "targets": targets,
                    "past_covariates": past_cov,
                    "future_covariates": future_cov,
                }
            ],
            "prediction_length": horizon,
            "quantile_levels": quantiles,
            "model": self.model_label if self.model_label in {"chronos-2", "chronos-2-small"} else "chronos-2",
        }
        payload = await self._post("/v2/forecast", body, fallback_warning="chronos_v2_unavailable")
        task = (payload.get("tasks") or [{}])[0]
        target_block = (task.get("targets") or {}).get(target_id) or {}
        steps = target_block.get("forecast") or []
        days = _steps_to_days(steps, frame.horizon_start, quantiles=quantiles)
        if len(days) != horizon:
            raise ForecastUnavailable(
                f"chronos v2 returned {len(days)} steps, expected {horizon}",
                warning="chronos_bad_response",
            )
        return EngineForecast(
            engine=self.model_label,
            model_id=str(payload.get("model_id") or self.model_label),
            model_version=str(payload.get("revision") or payload.get("model_version") or "1"),
            revision=payload.get("revision"),
            days=days,
            inference_seconds=payload.get("inference_seconds"),
        )

    async def _forecast_v1(
        self,
        frame: FeatureFrame,
        *,
        target_id: str,
        quantiles: list[float],
        horizon: int,
    ) -> EngineForecast:
        values = frame.targets.get(target_id) or []
        # Chronos v1 /predict rejects nulls — drop leading nulls, refuse interior gaps
        cleaned, _lead = impute_series(values)
        first_i = next((i for i, v in enumerate(values) if v is not None), 0)
        start = frame.context_start + timedelta(days=first_i)
        if len(cleaned) < 8:
            raise ForecastUnavailable(
                f"chronos needs >=8 finite points, got {len(cleaned)}",
                warning="chronos_thin_history",
            )
        # Days between the last usable observation and the horizon start are
        # forecast through (then dropped) so the dates line up.
        last_obs_date = start + timedelta(days=len(cleaned) - 1)
        extra = max(0, (frame.horizon_start - last_obs_date).days - 1)
        body = {
            "series": [
                {
                    "series_id": target_id,
                    "values": cleaned,
                    "start": start.isoformat(),
                    "freq": "D",
                }
            ],
            "prediction_length": horizon + extra,
            "quantile_levels": quantiles,
            "batch_size": 1,
        }
        payload = await self._post("/predict", body, fallback_warning="chronos_unavailable")
        forecasts = (payload.get("forecasts") or [{}])[0]
        steps = (forecasts.get("forecast") or [])[extra:]
        days = _steps_to_days(steps, frame.horizon_start, quantiles=quantiles)
        if len(days) != horizon:
            raise ForecastUnavailable(
                f"chronos returned {len(days)} steps, expected {horizon}",
                warning="chronos_bad_response",
            )
        return EngineForecast(
            engine=self.model_label,
            model_id=str(payload.get("model_id") or self.model_label),
            model_version=str(payload.get("revision") or payload.get("model_version") or "1"),
            revision=payload.get("revision"),
            days=days,
            inference_seconds=payload.get("inference_seconds"),
        )

    async def _post(self, path: str, body: dict[str, Any], *, fallback_warning: str) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(url, json=body)
                if resp.status_code == 429:
                    import asyncio

                    await asyncio.sleep(1.5)
                    resp = await client.post(url, json=body)
                if resp.status_code == 404:
                    raise ForecastUnavailable(
                        f"chronos {path} not found", warning="chronos_v2_unavailable"
                    )
                resp.raise_for_status()
                return resp.json()
        except ForecastUnavailable:
            raise
        except httpx.HTTPError as exc:
            raise ForecastUnavailable(
                f"chronos http error: {exc}", warning=fallback_warning
            ) from exc


class EtsEngine:
    """ETS daily path with weekly seasonality when history >= 28 days."""

    async def forecast(
        self,
        frame: FeatureFrame,
        *,
        target_id: str,
        model_id: str = "forecast.ets.path",
        nonnegative: bool = False,
    ) -> EngineForecast:
        values, lead = impute_series(frame.targets.get(target_id) or [])
        horizon = (frame.horizon_end - frame.horizon_start).days + 1
        result = forecast_path(
            values,
            horizon_days=horizon + lead,
            model_id=model_id,
            nonnegative=nonnegative,
            interval_z=Z_80,  # the pipeline labels these P10/P90
        )
        days = [
            DailyPoint(
                date=(frame.horizon_start + timedelta(days=i)).isoformat(),
                mean=result.points[lead + i],
                p10=result.lows[lead + i],
                p50=result.points[lead + i],
                p90=result.highs[lead + i],
            )
            for i in range(horizon)
        ]
        return EngineForecast(
            engine="ets",
            model_id=result.model_id,
            model_version=result.model_version,
            revision=None,
            days=days,
            warnings=list(result.warnings),
        )


class SeasonalNaiveEngine:
    """m=7 seasonal naive — MASE scale and skill reference."""

    async def forecast(
        self,
        frame: FeatureFrame,
        *,
        target_id: str,
        season: int = 7,
    ) -> EngineForecast:
        values = [v for v in (frame.targets.get(target_id) or []) if v is not None]
        horizon = (frame.horizon_end - frame.horizon_start).days + 1
        if len(values) < season:
            raise ForecastUnavailable(
                f"seasonal-naive needs >= {season} points", warning="thin_history"
            )
        days: list[DailyPoint] = []
        for i in range(horizon):
            point = float(values[-(season - (i % season))])
            # crude interval from seasonal residual spread
            residuals = [
                values[j] - values[j - season]
                for j in range(season, len(values))
            ]
            sigma = (
                math.sqrt(sum(r * r for r in residuals) / len(residuals))
                if residuals
                else 0.0
            )
            half = 1.96 * sigma
            d = frame.horizon_start + timedelta(days=i)
            days.append(
                DailyPoint(
                    date=d.isoformat(),
                    mean=point,
                    p10=point - half,
                    p50=point,
                    p90=point + half,
                )
            )
        return EngineForecast(
            engine="seasonal-naive",
            model_id="forecast.seasonal_naive.m7",
            model_version="1",
            revision=None,
            days=days,
        )


async def route_and_forecast(
    frame: FeatureFrame,
    *,
    target_id: str,
    preferred_engine: str | None,
    chronos_url: str | None,
    ets_model_id: str | None,
    nonnegative: bool = False,
    provisional: bool = False,
    features: Any = None,
) -> tuple[EngineForecast, RouterDecision]:
    """Router ladder aligned with prediction_policies fallback_order.

    1. Approved bundle engine (preferred_engine)
    2. Provisional univariate Chronos (+ calendar assembled already)
    3. ETS when an approved ETS model id is provided
    4. Raise ForecastUnavailable with reason codes
    """
    tried: list[str] = []
    errors: list[str] = []

    async def _try_chronos(label: str) -> EngineForecast | None:
        if not chronos_url:
            return None
        tried.append(label)
        try:
            return await ChronosEngine(chronos_url, model_label=label).forecast(
                frame, target_id=target_id, features=features
            )
        except ForecastUnavailable as exc:
            errors.append(f"{label}:{exc.warning}")
            _log.info("forecast_engine_fallback engine=%s reason=%s", label, exc.warning)
            return None

    async def _try_ets() -> EngineForecast | None:
        tried.append("ets")
        try:
            return await EtsEngine().forecast(
                frame, target_id=target_id, model_id=ets_model_id or "forecast.ets.path", nonnegative=nonnegative
            )
        except ForecastUnavailable as exc:
            errors.append(f"ets:{exc.warning}")
            return None

    # 1 / 2 — Chronos (approved or provisional)
    want_chronos = preferred_engine in {None, "chronos-2", "chronos-2-small"} or provisional
    if want_chronos:
        label = preferred_engine if preferred_engine in {"chronos-2", "chronos-2-small"} else "chronos-2"
        result = await _try_chronos(label)
        if result is not None:
            return result, RouterDecision(engine=result.engine, reason="chronos", tried=tried)

    # 3 — ETS
    result = await _try_ets()
    if result is not None:
        return result, RouterDecision(
            engine="ets",
            reason="fallback_ets" if tried else "ets_approved",
            tried=tried,
        )

    # Last resort for backtests / offline: seasonal-naive is always available
    # but live refuse rather than guess when Chronos+ETS both fail.
    raise ForecastUnavailable(
        f"no engine available for {target_id}: {errors or tried or ['none']}",
        warning="forecast_refused",
    )


def impute_series(values: list[float | None]) -> tuple[list[float], int]:
    """Fill interior nulls and report trailing unobserved days.

    Masked days (outages, incidents) keep the weekly rhythm: they take the value
    one week earlier when known, else the previous value. Leading nulls are
    dropped. Returns (clean series, number of trailing null days).
    """
    first = next((i for i, v in enumerate(values) if v is not None), None)
    last = next((i for i in range(len(values) - 1, -1, -1) if values[i] is not None), None)
    if first is None or last is None:
        return [], 0
    out: list[float] = []
    for v in values[first : last + 1]:
        if v is not None and math.isfinite(float(v)):
            out.append(float(v))
        elif len(out) >= 7:
            out.append(out[-7])
        else:
            out.append(out[-1] if out else 0.0)
    return out, len(values) - 1 - last


def _finite_prefix(
    values: list[float | None], context_start: date
) -> tuple[list[float], date]:
    """Drop leading nulls; stop before first interior null after data starts."""
    start_i = 0
    while start_i < len(values) and values[start_i] is None:
        start_i += 1
    cleaned: list[float] = []
    for v in values[start_i:]:
        if v is None or not math.isfinite(float(v)):
            break
        cleaned.append(float(v))
    return cleaned, context_start + timedelta(days=start_i)


def _steps_to_days(
    steps: list[dict[str, Any]], horizon_start: date, *, quantiles: list[float]
) -> list[DailyPoint]:
    days: list[DailyPoint] = []
    for i, step in enumerate(steps):
        ts = step.get("timestamp") or (horizon_start + timedelta(days=i)).isoformat()
        mean = float(step.get("mean") if step.get("mean") is not None else step.get("p50") or 0.0)
        p10 = float(step.get("p10") if step.get("p10") is not None else mean)
        p50 = float(step.get("p50") if step.get("p50") is not None else mean)
        p90 = float(step.get("p90") if step.get("p90") is not None else mean)
        # map arbitrary quantile keys if present
        for q in quantiles:
            key = f"p{int(round(q * 100))}"
            if key in step and key == "p10":
                p10 = float(step[key])
            elif key in step and key == "p50":
                p50 = float(step[key])
            elif key in step and key == "p90":
                p90 = float(step[key])
        days.append(DailyPoint(date=str(ts)[:10], mean=mean, p10=p10, p50=p50, p90=p90))
    return days
