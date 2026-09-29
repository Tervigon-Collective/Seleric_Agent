"""statsforecast AutoETS backend — seasonal-aware, principled intervals.

Optional dependency (``forecast-stats`` extra). Weekly seasonality is enabled
only when there are at least three full weeks of history; below that a
seasonal fit would be estimating noise.
"""

from __future__ import annotations

import math

from seleric_swarm.models.backends.base import BackendForecast
from seleric_swarm.toolsets import policy_config as policy

_MIN_SEASONAL_CYCLES = 3


class StatsforecastBackend:
    name = "statsforecast"

    def forecast(
        self, values: list[float], horizon_days: int, *, season_length: int | None = None
    ) -> BackendForecast:
        from seleric_swarm.models.service import ForecastUnavailable

        try:
            import numpy as np
            from statsforecast.models import AutoETS
        except ImportError as exc:
            raise ForecastUnavailable(
                f"statsforecast unavailable: {exc}", warning=policy.WARN_MODEL_UNAVAILABLE
            ) from exc

        season = season_length or 7
        seasonal = len(values) >= season * _MIN_SEASONAL_CYCLES
        try:
            model = AutoETS(season_length=season if seasonal else 1)
            out = model.forecast(
                y=np.asarray(values, dtype=float), h=horizon_days, level=[95]
            )
            point = float(out["mean"][-1])
            lower = float(out["lo-95"][-1])
            upper = float(out["hi-95"][-1])
        except Exception as exc:
            raise ForecastUnavailable(
                f"AutoETS failed to fit: {exc}", warning=policy.WARN_MODEL_FIT_FAILED
            ) from exc

        if not all(math.isfinite(x) for x in (point, lower, upper)):
            raise ForecastUnavailable(
                "model produced a non-finite forecast", warning=policy.WARN_MODEL_FIT_FAILED
            )
        return BackendForecast(
            point=point,
            lower=lower,
            upper=upper,
            method=f"auto_ets_s{season}" if seasonal else "auto_ets",
        )
