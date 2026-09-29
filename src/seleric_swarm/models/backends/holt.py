"""Holt exponential smoothing backend (the original, baseline forecaster).

Additive trend once there is enough history to estimate one; interval from
in-sample residual spread widened by sqrt(horizon). Deterministic.
"""

from __future__ import annotations

import math
import statistics

from seleric_swarm.models.backends.base import BackendForecast
from seleric_swarm.toolsets import policy_config as policy

# Trend estimation needs materially more than a couple of points before it
# stops being noise amplification; below this we smooth the level only.
_MIN_POINTS_FOR_TREND = 10
# ~95% two-sided normal interval.
_Z_95 = 1.96


class HoltBackend:
    name = "holt"

    def forecast(
        self, values: list[float], horizon_days: int, *, season_length: int | None = None
    ) -> BackendForecast:
        from seleric_swarm.models.service import ForecastUnavailable

        point, fitted, method = _fit_and_forecast(values, horizon_days)

        residuals = [actual - pred for actual, pred in zip(values, fitted, strict=True)]
        # Population stdev: these are the model's own in-sample errors, not a sample
        # drawn from a wider set.
        sigma = statistics.pstdev(residuals) if len(residuals) > 1 else 0.0
        half_width = _Z_95 * sigma * math.sqrt(horizon_days)

        if not math.isfinite(point):  # pragma: no cover - guarded in _fit_and_forecast
            raise ForecastUnavailable(
                "model produced a non-finite forecast", warning=policy.WARN_MODEL_FIT_FAILED
            )
        return BackendForecast(
            point=point, lower=point - half_width, upper=point + half_width, method=method
        )


def _fit_and_forecast(values: list[float], horizon_days: int) -> tuple[float, list[float], str]:
    """Returns (point forecast, in-sample fitted values, method name)."""
    from seleric_swarm.models.service import ForecastUnavailable

    try:
        from statsmodels.tsa.holtwinters import ExponentialSmoothing
    except ImportError as exc:  # pragma: no cover - declared dependency
        raise ForecastUnavailable(
            f"statsmodels unavailable: {exc}", warning=policy.WARN_MODEL_UNAVAILABLE
        ) from exc

    use_trend = len(values) >= _MIN_POINTS_FOR_TREND
    method = "holt_linear_trend" if use_trend else "simple_exponential_smoothing"
    try:
        model = ExponentialSmoothing(
            values,
            trend="add" if use_trend else None,
            seasonal=None,
            initialization_method="estimated",
        )
        fit = model.fit(optimized=True)
        point = float(fit.forecast(horizon_days)[-1])
        fitted = [float(v) for v in fit.fittedvalues]
    except Exception as exc:
        raise ForecastUnavailable(
            f"exponential smoothing failed to fit: {exc}", warning=policy.WARN_MODEL_FIT_FAILED
        ) from exc

    if not math.isfinite(point):
        raise ForecastUnavailable(
            "model produced a non-finite forecast", warning=policy.WARN_MODEL_FIT_FAILED
        )
    return point, fitted, method
