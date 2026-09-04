"""Ports + in-memory implementations for the Prediction subsystem.

Reuses the Skeptic's ``ModelRegistry`` / ``DriftMonitor`` and adds:

* ``ForecastModelService`` - fits / calls a registered model, returns a raw
  ``ForecastRun`` (or None if it cannot serve the target).
* ``FeatureStore`` - resolves a feature set id/version + freshness for a model.

The deterministic ``StatisticalBaselineForecaster`` is the approved fallback: a
drift / last-value / linear-trend projection with a residual-std interval. It is
NOT an LLM.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from seleric_swarm.agents.prediction.contracts import ForecastRun

# reuse the Skeptic's model + drift infra so there is one implementation.
from seleric_swarm.agents.skeptic.registries import (  # noqa: F401
    ArtifactRepository,
    DriftMonitor,
    DriftReport,
    EvidenceRepository,
    InMemoryArtifactRepository,
    InMemoryEvidenceRepository,
    InMemoryModelRegistry,
    ModelRecord,
    ModelRegistry,
    NullDriftMonitor,
    repositories_from_blackboard,
)

# --------------------------------------------------------------------------- #
# Feature store
# --------------------------------------------------------------------------- #


@dataclass
class FeatureSetRef:
    feature_set_id: str
    version: str = "1"
    fresh: bool = True
    missing_features: list[str] = field(default_factory=list)


@runtime_checkable
class FeatureStore(Protocol):
    def resolve(self, model_id: str) -> FeatureSetRef | None: ...


class InMemoryFeatureStore:
    def __init__(self, mapping: dict[str, FeatureSetRef] | None = None) -> None:
        self._m = dict(mapping or {})

    def add(self, model_id: str, ref: FeatureSetRef) -> None:
        self._m[model_id] = ref

    def resolve(self, model_id: str) -> FeatureSetRef | None:
        return self._m.get(model_id)


# --------------------------------------------------------------------------- #
# Forecast model service
# --------------------------------------------------------------------------- #


@dataclass
class ForecastModelQuery:
    target_metric: str
    horizon: str
    model_id: str
    model_version: str
    features: dict[str, Any] = field(default_factory=dict)
    history: list[float] | None = None
    observations: Any = None


@runtime_checkable
class ForecastModelService(Protocol):
    async def forecast(self, query: ForecastModelQuery) -> ForecastRun | None: ...


class TemplateForecastModelService:
    """Deterministic, offline. Returns a scenario ``forecast_truth`` dict as a
    ``ForecastRun`` when the target matches. Mirrors the swarm's
    ``TemplateForecaster`` so fixture runs need no real model."""

    def __init__(self, forecast_truth: dict[str, Any] | None = None) -> None:
        self._truth = forecast_truth or {}

    async def forecast(self, query: ForecastModelQuery) -> ForecastRun | None:
        t = self._truth
        if t.get("target") != query.target_metric:
            return None
        model = t.get("model", {}) or {}
        return ForecastRun(
            source="registered_model",
            target_metric=query.target_metric,
            horizon=t.get("horizon", query.horizon),
            prediction=_as_float(t.get("prediction")),
            interval=[float(x) for x in t.get("interval", [])],
            model_id=model.get("id") or query.model_id,
            model_version=model.get("version") or query.model_version,
            feature_set_id=model.get("feature_set"),
            feature_set_version=model.get("feature_set_version"),
            training_window=t.get("training_window", {}),
            backtest_metrics=_backtest(model),
            drift_status=model.get("drift_status"),
            method="registered_model:template",
            synthetic=True,
        )


# --------------------------------------------------------------------------- #
# Statistical baseline forecaster (the approved fallback)
# --------------------------------------------------------------------------- #


class StatisticalBaselineForecaster:
    """Deterministic. method in {drift_projection, last_value, linear_trend}.
    Interval is +/- z * residual_std of the fitted method over the history."""

    def __init__(self, *, method: str = "drift_projection", interval_z: float = 1.28, min_history: int = 8) -> None:
        self._method = method
        self._z = interval_z
        self._min_history = min_history

    def forecast(
        self, *, target_metric: str, horizon: str, history: list[float]
    ) -> ForecastRun | None:
        n = len(history)
        if n < self._min_history:
            return None
        steps = _horizon_steps(horizon)

        if self._method == "last_value":
            point = history[-1]
            fitted = history[:-1]
            resid = [history[i + 1] - history[i] for i in range(n - 1)]
        elif self._method == "linear_trend":
            slope, intercept = _ols(history)
            point = intercept + slope * (n - 1 + steps)
            fitted = [intercept + slope * i for i in range(n)]
            resid = [history[i] - fitted[i] for i in range(n)]
        else:  # drift_projection
            drift = (history[-1] - history[0]) / max(1, n - 1)
            point = history[-1] + drift * steps
            fitted = [history[0] + drift * i for i in range(n)]
            resid = [history[i] - fitted[i] for i in range(n)]

        sd = statistics.pstdev(resid) if len(resid) >= 2 else abs(point) * 0.1
        half = self._z * sd * (steps**0.5)
        return ForecastRun(
            source="statistical_baseline",
            target_metric=target_metric,
            horizon=horizon,
            prediction=round(point, 4),
            interval=[round(point - half, 4), round(point + half, 4)],
            model_id=f"baseline.{self._method}",
            model_version="1",
            backtest_metrics={"in_sample_mape": _mape(history, fitted)},
            drift_status="n/a",
            method=f"statistical_baseline:{self._method}",
            synthetic=False,
            notes=[f"{self._method} over {n} points, horizon {steps} step(s)"],
        )


# --------------------------------------------------------------------------- #
# blackboard adapter
# --------------------------------------------------------------------------- #


def anomaly_rows_from_blackboard(blackboard: Any) -> list[dict[str, Any]]:
    return [a for a in blackboard._store.all() if a.get("artifact_type") == "anomaly"]


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _as_float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _backtest(model: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    bt = model.get("backtest_metric")
    if bt:
        out["reported"] = bt
    if model.get("mape") is not None:
        out["mape"] = model["mape"]
    return out


def _horizon_steps(horizon: str) -> int:
    h = (horizon or "7d").strip().lower()
    num = "".join(ch for ch in h if ch.isdigit()) or "7"
    return max(1, int(num))


def _ols(y: list[float]) -> tuple[float, float]:
    n = len(y)
    xs = list(range(n))
    mx = sum(xs) / n
    my = sum(y) / n
    denom = sum((x - mx) ** 2 for x in xs) or 1.0
    slope = sum((xs[i] - mx) * (y[i] - my) for i in range(n)) / denom
    return slope, my - slope * mx


def _mape(actual: list[float], fitted: list[float]) -> float:
    pairs = [(a, f) for a, f in zip(actual, fitted, strict=False) if a]
    if not pairs:
        return 0.0
    return round(sum(abs((a - f) / a) for a, f in pairs) / len(pairs), 4)
