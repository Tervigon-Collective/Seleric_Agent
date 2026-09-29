"""Seasonal-naive backend: repeat the value from one season (default 7d) earlier.

The benchmark every other backend has to beat (MASE is defined against it).
Interval from the spread of in-sample seasonal differences ``y[t] - y[t-m]``,
widened by sqrt(cycles ahead). Deterministic, no dependencies.
"""

from __future__ import annotations

import math
import statistics

from seleric_swarm.models.backends.base import BackendForecast
from seleric_swarm.toolsets import policy_config as policy

_Z_95 = 1.96


class SeasonalNaiveBackend:
    name = "seasonal_naive"

    def forecast(
        self, values: list[float], horizon_days: int, *, season_length: int | None = None
    ) -> BackendForecast:
        from seleric_swarm.models.service import ForecastUnavailable

        m = season_length or 7
        if len(values) < m + 2:
            raise ForecastUnavailable(
                f"seasonal naive needs at least {m + 2} points, got {len(values)}",
                warning=policy.WARN_THIN_HISTORY,
            )
        point = values[-m + ((horizon_days - 1) % m)]
        diffs = [values[i] - values[i - m] for i in range(m, len(values))]
        sigma = statistics.pstdev(diffs) if len(diffs) > 1 else 0.0
        cycles = math.ceil(horizon_days / m)
        half = _Z_95 * sigma * math.sqrt(cycles)
        return BackendForecast(
            point=point, lower=point - half, upper=point + half, method=f"seasonal_naive_{m}"
        )
