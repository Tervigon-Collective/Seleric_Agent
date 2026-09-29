"""Forecast backends, selected per model-registry entry (``backend:`` field)."""

from __future__ import annotations

from seleric_swarm.models.backends.base import BackendForecast, Forecaster
from seleric_swarm.models.backends.conformal import ConformalBackend
from seleric_swarm.models.backends.holt import HoltBackend
from seleric_swarm.models.backends.seasonal_naive import SeasonalNaiveBackend
from seleric_swarm.models.backends.statsforecast_backend import StatsforecastBackend

_BACKENDS: dict[str, Forecaster] = {
    "holt": HoltBackend(),
    "statsforecast": StatsforecastBackend(),
    "seasonal_naive": SeasonalNaiveBackend(),
}


def get_backend(name: str) -> Forecaster:
    """Resolve a backend by registry name; unknown names raise ``ForecastUnavailable``."""
    backend = _BACKENDS.get(name)
    if backend is None and name.endswith("+conformal"):
        inner = _BACKENDS.get(name.removesuffix("+conformal"))
        if inner is not None:
            backend = ConformalBackend(inner)
    if backend is None:
        from seleric_swarm.models.service import ForecastUnavailable
        from seleric_swarm.toolsets import policy_config as policy

        raise ForecastUnavailable(
            f"unknown forecast backend {name!r}; known: {sorted(_BACKENDS)} (each also as <name>+conformal)",
            warning=policy.WARN_MODEL_UNAVAILABLE,
        )
    return backend


def register_backend(backend: Forecaster) -> None:
    """Register a backend (foundation-model backends add themselves here)."""
    _BACKENDS[backend.name] = backend


__all__ = ["BackendForecast", "Forecaster", "get_backend", "register_backend"]
