"""Governed multi-target forecasting (Chronos-2 + feature framework).

Live path: ``agent/forecast_plan.compile_forecast_plan`` → ``pipeline.run_forecast``.
Offline: ``python -m seleric_swarm.forecasting`` (dry-run / explain).

Imports are lazy so ``forecasting.horizon`` / ``policies`` can load without
pulling the pipeline (which imports ``agent.forecast_plan``).
"""

from __future__ import annotations

from typing import Any

__all__ = ["ForecastOutcome", "run_forecast"]


def __getattr__(name: str) -> Any:
    if name in {"ForecastOutcome", "run_forecast"}:
        from seleric_swarm.forecasting.pipeline import ForecastOutcome, run_forecast

        return ForecastOutcome if name == "ForecastOutcome" else run_forecast
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
