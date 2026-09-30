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


def rolling_errors(
    backend: Forecaster,
    values: list[float],
    horizon_days: int,
    *,
    season_length: int | None,
    max_origins: int,
) -> list[float]:
    """Absolute errors of ``backend`` at ``horizon_days`` over the latest origins.

    Each origin trains on ``values[:cut]`` only, so nothing from the target
    period leaks into its own forecast. Origins the backend refuses are skipped.
    """
    from seleric_swarm.models.service import ForecastUnavailable

    last_cut = len(values) - horizon_days  # origin whose target is the final point
    first_cut = max(MIN_TRAIN, last_cut - (max_origins - 1))
    errors: list[float] = []
    for cut in range(first_cut, last_cut + 1):
        try:
            f = backend.forecast(values[:cut], horizon_days, season_length=season_length)
        except ForecastUnavailable:
            continue
        errors.append(abs(f.point - values[cut + horizon_days - 1]))
    return errors


def conformal_half_width(errors: list[float]) -> float:
    """Finite-sample-corrected ``COVERAGE`` quantile of absolute errors."""
    ordered = sorted(errors)
    rank = min(len(ordered), math.ceil((len(ordered) + 1) * COVERAGE))
    return ordered[rank - 1]


class ConformalBackend:
    def __init__(self, inner: Forecaster) -> None:
        self._inner = inner
        self.name = f"{inner.name}+conformal"

    def forecast(
        self, values: list[float], horizon_days: int, *, season_length: int | None = None
    ) -> BackendForecast:
        base = self._inner.forecast(values, horizon_days, season_length=season_length)

        errors = rolling_errors(
            self._inner, values, horizon_days, season_length=season_length, max_origins=MAX_ORIGINS
        )

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

        q = conformal_half_width(errors)
        return BackendForecast(
            point=base.point,
            lower=base.point - q,
            upper=base.point + q,
            method=f"{base.method}+conformal",
            warnings=list(base.warnings),
        )
