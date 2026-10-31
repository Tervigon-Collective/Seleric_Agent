"""Run-time calibration of horizon-total bands from the series' own rolling-origin errors.

Daily ETS bands are fine, but the band of a *sum* depends on how errors persist across days,
which differs per series and horizon. Instead of a fixed constant, refit the forecaster at past
origins, measure how far its window total missed, and size the band from those misses.
"""

from __future__ import annotations

import statistics

from seleric_swarm.models.service import ForecastUnavailable, forecast_path

MIN_ORIGINS = 12   # fewer realised windows than this and the quantiles are noise
MAX_ORIGINS = 40
STEP_DAYS = 7


def empirical_total_error(
    clean: list[float],
    *,
    lead: int,
    first: int,
    n: int,
    nonnegative: bool,
    quantiles: tuple[float, float] = (0.1, 0.9),
) -> tuple[float, float, int] | None:
    """Relative error quantiles (q_lo, q_hi, windows) of the sum over forecast steps first..first+n-1.

    ``clean`` is the imputed history, ``lead`` the unobserved days between its end and the
    horizon start. Interval = point total x (1 + q). None when too few windows were realised.
    """
    span = lead + first + n
    errors: list[float] = []
    origin = len(clean) - span
    while origin >= 120 and len(errors) < MAX_ORIGINS:  # origins need >= 120 days of history
        try:
            res = forecast_path(
                clean[:origin], horizon_days=span, model_id="calibration", nonnegative=nonnegative
            )
        except ForecastUnavailable:
            break
        predicted = sum(res.points[lead + first : span])
        actual = sum(clean[origin + lead + first : origin + span])
        if predicted > 0:
            errors.append(actual / predicted - 1.0)
        origin -= STEP_DAYS
    if len(errors) < MIN_ORIGINS:
        return None
    cuts = statistics.quantiles(errors, n=20, method="inclusive")  # 5% steps
    lo = cuts[round(quantiles[0] * 20) - 1]
    hi = cuts[round(quantiles[1] * 20) - 1]
    return lo, hi, len(errors)
