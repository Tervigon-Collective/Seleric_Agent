"""Backend contract for numeric forecasting.

A backend does the math only. Everything policy-shaped (horizon validation,
null handling, minimum rows, collapsed-interval warning, rounding) stays in
``models.service.forecast_series`` so every backend is held to the same guards.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class BackendForecast:
    """Raw (unrounded) output of a backend for the point ``horizon_days`` ahead."""

    point: float
    lower: float
    upper: float
    method: str
    warnings: list[str] = field(default_factory=list)


class Forecaster(Protocol):
    name: str

    def forecast(
        self, values: list[float], horizon_days: int, *, season_length: int | None = None
    ) -> BackendForecast:
        """Forecast ``horizon_days`` past ``values`` (ascending, one point per day).

        Must raise ``ForecastUnavailable`` rather than return a non-finite or
        interval-less result.
        """
        ...
