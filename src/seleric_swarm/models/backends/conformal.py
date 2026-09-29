"""Conformal interval wrapper: ``<backend>+conformal``.

Keeps the inner backend's point forecast but replaces its interval with an
empirical one: rolling-origin absolute errors of the *same backend at the same
horizon* on the supplied history, taken at the finite-sample-corrected 95th
percentile. This makes the stated coverage an observed property of the series
rather than a normal-residual assumption (which the backtest showed over-covers
for Holt and under-covers for seasonal-naive/ETS).

If the history is too short to score enough origins, the inner interval is
returned unchanged with a warning -- never a fabricated calibration.
"""

from __future__ import annotations

import math

from seleric_swarm.models.backends.base import BackendForecast, Forecaster

MIN_TRAIN = 21
MAX_ORIGINS = 20
MIN_ERRORS = 8
COVERAGE = 0.95


class ConformalBackend:
    def __init__(self, inner: Forecaster) -> None:
        self._inner = inner
        self.name = f"{inner.name}+conformal"

    def forecast(
        self, values: list[float], horizon_days: int, *, season_length: int | None = None
    ) -> BackendForecast:
        from seleric_swarm.models.service import ForecastUnavailable

        base = self._inner.forecast(values, horizon_days, season_length=season_length)

        last_cut = len(values) - horizon_days  # origin whose target is the final point
        first_cut = max(MIN_TRAIN, last_cut - (MAX_ORIGINS - 1))
        errors: list[float] = []
        for cut in range(first_cut, last_cut + 1):
            try:
                f = self._inner.forecast(values[:cut], horizon_days, season_length=season_length)
            except ForecastUnavailable:
                continue
            errors.append(abs(f.point - values[cut + horizon_days - 1]))

        if len(errors) < MIN_ERRORS:
            return BackendForecast(
                point=base.point,
                lower=base.lower,
                upper=base.upper,
                method=base.method,
                warnings=[
                    *base.warnings,
                    (
                        f"conformal calibration skipped: only {len(errors)} scoreable "
                        f"origin(s) (need {MIN_ERRORS}); interval is the model's own, "
                        "not calibrated"
                    ),
                ],
            )

        errors.sort()
        rank = min(len(errors), math.ceil((len(errors) + 1) * COVERAGE))
        q = errors[rank - 1]
        return BackendForecast(
            point=base.point,
            lower=base.point - q,
            upper=base.point + q,
            method=f"{base.method}+conformal",
            warnings=list(base.warnings),
        )
