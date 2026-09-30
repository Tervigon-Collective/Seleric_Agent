"""``auto`` backend: choose the method per series by backtesting on that series.

For metrics with no dedicated approved model. Candidates are scored by rolling-
origin MAE at the requested horizon on the supplied history only (deterministic,
no leakage); the lowest MAE wins, and seasonal-naive is always a candidate, so
"nothing beats repeating last week" resolves to seasonal-naive rather than to a
fancier model that lost. The interval is conformal from the winner's own errors.

Refuses (rather than guessing) when the history is too short to score at least
``MIN_ERRORS`` origins for the benchmark.
"""

from __future__ import annotations

import statistics

from seleric_swarm.models.backends.base import BackendForecast, Forecaster
from seleric_swarm.models.backends.conformal import (
    MIN_ERRORS,
    conformal_half_width,
    rolling_errors,
)
from seleric_swarm.toolsets import policy_config as policy

MAX_ORIGINS = 10
# A candidate must beat the benchmark by this margin to displace it (ties -> benchmark).
_MIN_GAIN = 0.02
_BENCHMARK = "seasonal_naive"
_CANDIDATES = ("holt", "statsforecast", _BENCHMARK)


class AutoBackend:
    name = "auto"

    def __init__(self, candidates: dict[str, Forecaster]) -> None:
        self._candidates = candidates

    def forecast(
        self, values: list[float], horizon_days: int, *, season_length: int | None = None
    ) -> BackendForecast:
        from seleric_swarm.models.service import ForecastUnavailable

        scores: dict[str, float] = {}
        errors_by: dict[str, list[float]] = {}
        skipped: list[str] = []
        for name in _CANDIDATES:
            backend = self._candidates.get(name)
            if backend is None:
                continue
            try:
                errs = rolling_errors(
                    backend, values, horizon_days, season_length=season_length,
                    max_origins=MAX_ORIGINS,
                )
                if len(errs) >= MIN_ERRORS:
                    # One live forecast also proves the dependency is present.
                    backend.forecast(values, horizon_days, season_length=season_length)
            except ForecastUnavailable as exc:
                skipped.append(f"{name}: {exc}")
                continue
            if len(errs) >= MIN_ERRORS:
                scores[name] = statistics.fmean(errs)
                errors_by[name] = errs

        if _BENCHMARK not in scores:
            raise ForecastUnavailable(
                f"history too short to backtest at h={horizon_days}: need enough data for "
                f"{MIN_ERRORS} scoreable origins",
                warning=policy.WARN_THIN_HISTORY,
            )

        winner = _BENCHMARK
        best = scores[_BENCHMARK] * (1 - _MIN_GAIN)
        for name, mae in scores.items():
            if name != _BENCHMARK and mae < best:
                winner, best = name, mae

        chosen = self._candidates[winner]
        base = chosen.forecast(values, horizon_days, season_length=season_length)
        q = conformal_half_width(errors_by[winner])
        return BackendForecast(
            point=base.point,
            lower=base.point - q,
            upper=base.point + q,
            method=f"auto:{base.method}+conformal",
            warnings=[*base.warnings, *[f"auto: candidate skipped ({s})" for s in skipped]],
            diagnostics={
                "selected": winner,
                "backtest_mae": {k: round(v, 6) for k, v in scores.items()},
                "origins": len(errors_by[winner]),
                "horizon_days": horizon_days,
            },
        )
