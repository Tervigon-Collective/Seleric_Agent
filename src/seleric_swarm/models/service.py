"""Forecasting over already-fetched evidence (Profile C, Sprint 3).

Rule 5 applies here exactly as it does to Analytics: this module **calculates,
it never fetches**. History arrives as ``EvidenceArtifact``s the Semantic
toolset already retrieved, using the same series convention
``detect_anomalies`` uses — sorted by ``period_start``, one point per period.

Method
------
Exponential smoothing (``statsmodels.tsa.holtwinters``), with an additive trend
once there is enough history to estimate one. Chosen because the series this
system forecasts are short daily business metrics where ARIMA order selection
would be over-fitting theatre, and because it is deterministic — the same
evidence produces the same number every time, which a forecast that has to be
reproducible from stored provenance needs.

The interval is derived from in-sample residual spread, widened by
``sqrt(step)`` across the horizon (uncertainty compounds the further out you
go). It is **not optional**: ``agents/skeptic/validators/forecast_validator.py``
treats a point forecast with no interval as an evidence gap, and the V3
validator's ``check_prediction`` does the same. A number with no stated
uncertainty is not a forecast.

What this module deliberately does NOT do
-----------------------------------------
No LLM ever produces a number here (``forecast_validator.py`` makes an
LLM-generated numeric forecast a *blocking* failure, and
``config/skeptic_policies.yaml`` sets ``allow_llm_numeric_fallback: false``).
If the evidence cannot support a forecast, this refuses — it does not guess.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from seleric_swarm.paths import repo_root
from seleric_swarm.toolsets import policy_config as policy

if TYPE_CHECKING:
    from seleric_swarm.agent.artifacts import EvidenceArtifact

MODEL_VERSION = "1"
# Trend estimation needs materially more than a couple of points before it
# stops being noise amplification; below this we smooth the level only.
_MIN_POINTS_FOR_TREND = 10
# ~95% two-sided normal interval.
_Z_95 = 1.96


@dataclass
class ForecastResult:
    model_id: str
    model_version: str
    value: float
    interval: tuple[float, float]
    horizon_days: int
    history_points: int
    method: str
    warnings: list[str] = field(default_factory=list)


class ForecastUnavailable(Exception):
    """Raised when the evidence cannot support a forecast. Carries the named
    policy warning so the toolset can surface *which* gate declined."""

    def __init__(self, message: str, *, warning: str) -> None:
        super().__init__(message)
        self.warning = warning


def forecast_series(
    evidence: list[EvidenceArtifact],
    *,
    horizon_days: int,
    model_id: str,
) -> ForecastResult:
    """Point forecast + interval ``horizon_days`` ahead of the last observation.

    Raises ``ForecastUnavailable`` rather than returning a fabricated number.
    """
    if horizon_days < 1:
        raise ForecastUnavailable(
            f"horizon_days must be >= 1, got {horizon_days}", warning=policy.WARN_INVALID_HORIZON
        )

    ordered = sorted(
        (e for e in evidence if e.value is not None), key=lambda e: e.period_start
    )
    values = [float(e.value) for e in ordered]  # type: ignore[arg-type]

    if len(values) < policy.MIN_OBSERVATION_ROWS:
        raise ForecastUnavailable(
            f"{len(values)} usable point(s); forecasting needs at least "
            f"{policy.MIN_OBSERVATION_ROWS}",
            warning=policy.WARN_THIN_HISTORY,
        )

    point, fitted, method = _fit_and_forecast(values, horizon_days)

    residuals = [actual - pred for actual, pred in zip(values, fitted, strict=True)]
    # Population stdev: these are the model's own in-sample errors, not a sample
    # drawn from a wider set.
    sigma = statistics.pstdev(residuals) if len(residuals) > 1 else 0.0
    half_width = _Z_95 * sigma * math.sqrt(horizon_days)

    low, high = round(point - half_width, 6), round(point + half_width, 6)

    warnings: list[str] = []
    if low == high:
        # An interval that collapses to a point reads as certainty. It happens
        # when the model fits the history almost exactly — a perfectly linear
        # series, most often synthetic data. Checked on the *emitted* bounds
        # rather than on sigma, because rounding can close a hair-width
        # interval that sigma alone would call non-zero, and the reader only
        # ever sees the bounds.
        warnings.append(
            "residual spread is ~zero; the interval collapsed to a point and reflects "
            "no observed variation, not certainty"
        )

    return ForecastResult(
        model_id=model_id,
        model_version=MODEL_VERSION,
        value=round(point, 6),
        interval=(low, high),
        horizon_days=horizon_days,
        history_points=len(values),
        method=method,
        warnings=warnings,
    )


def _fit_and_forecast(values: list[float], horizon_days: int) -> tuple[float, list[float], str]:
    """Returns (point forecast, in-sample fitted values, method name)."""
    try:
        from statsmodels.tsa.holtwinters import ExponentialSmoothing
    except ImportError as exc:  # pragma: no cover - declared dependency
        raise ForecastUnavailable(
            f"statsmodels unavailable: {exc}", warning=policy.WARN_MODEL_UNAVAILABLE
        ) from exc

    use_trend = len(values) >= _MIN_POINTS_FOR_TREND
    method = "holt_linear_trend" if use_trend else "simple_exponential_smoothing"
    try:
        model = ExponentialSmoothing(
            values,
            trend="add" if use_trend else None,
            seasonal=None,
            initialization_method="estimated",
        )
        fit = model.fit(optimized=True)
        point = float(fit.forecast(horizon_days)[-1])
        fitted = [float(v) for v in fit.fittedvalues]
    except Exception as exc:
        raise ForecastUnavailable(
            f"exponential smoothing failed to fit: {exc}", warning=policy.WARN_MODEL_FIT_FAILED
        ) from exc

    if not math.isfinite(point):
        raise ForecastUnavailable(
            "model produced a non-finite forecast", warning=policy.WARN_MODEL_FIT_FAILED
        )
    return point, fitted, method


# --------------------------------------------------------------------------- #
# Model registry — ported from agents/skeptic/registries.py +
# agents/skeptic/services/model_registry.py (Sprint 5: V3-owned, no longer a
# skeptic import). DriftMonitor/DriftReport stayed behind: nothing under
# toolsets/ reads them.
# --------------------------------------------------------------------------- #


@dataclass
class ModelRecord:
    model_id: str
    version: str = "1"
    status: str = "candidate"  # candidate | approved | deprecated
    target: str = ""
    model_type: str = "forecast"
    minimum_history_days: int = 0
    supports_seasonality: bool = False
    last_validated_at: str | None = None
    backtest_available: bool = False


class InMemoryModelRegistry:
    def __init__(self, models: dict[str, ModelRecord] | None = None) -> None:
        self._m = dict(models or {})

    def add(self, rec: ModelRecord) -> None:
        self._m[rec.model_id] = rec

    def get(self, model_id: str) -> ModelRecord | None:
        return self._m.get(model_id)

    def ids(self) -> list[str]:
        return list(self._m)

    def for_target(self, target: str) -> list[ModelRecord]:
        return [r for r in self._m.values() if r.target == target]


_MODEL_REGISTRY_CANDIDATE_PATHS = ("config/model_registry.yaml", "config/model_registry.example.yaml")


class YamlModelRegistry(InMemoryModelRegistry):
    """Same interface as InMemoryModelRegistry; seeded from YAML."""

    def __init__(self, path: str | Path | None = None) -> None:
        super().__init__()
        p = _resolve_model_registry_path(path)
        if p is None:
            return
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        for item in data.get("models", []):
            applic = item.get("applicability", {}) or {}
            self.add(
                ModelRecord(
                    model_id=str(item["id"]),
                    version=str(item.get("version", "1")),
                    status=str(item.get("status", "candidate")),
                    target=str(item.get("target", "")),
                    model_type=str(item.get("type", "forecast")),
                    minimum_history_days=int(applic.get("minimum_history_days", 0)),
                    supports_seasonality=bool(applic.get("supports_seasonality", False)),
                    last_validated_at=item.get("last_validated_at"),
                    backtest_available=bool(
                        item.get("backtest_available")
                        or (item.get("validation") or {}).get("metric")
                        or item.get("backtest_metrics")
                    ),
                )
            )


def model_registry_from_yaml(path: str | Path | None = None) -> YamlModelRegistry:
    return YamlModelRegistry(path)


def _resolve_model_registry_path(path: str | Path | None) -> Path | None:
    if path is not None:
        p = Path(path)
        return p if p.exists() else None
    root = repo_root()
    for candidate in _MODEL_REGISTRY_CANDIDATE_PATHS:
        p = root / candidate
        if p.exists():
            return p
    return None
