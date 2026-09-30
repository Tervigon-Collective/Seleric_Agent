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

from seleric_swarm.models.backends import get_backend
from seleric_swarm.paths import repo_root
from seleric_swarm.toolsets import policy_config as policy

if TYPE_CHECKING:
    from seleric_swarm.agent.artifacts import EvidenceArtifact

MODEL_VERSION = "1"
# Points further than this many robust sigmas (1.4826 * MAD) from the series median
# are treated as data errors, not signal: a -1M "net sales" day in the source would
# otherwise be fitted straight into the trend (seen on real data, 2026-09-28).
OUTLIER_ROBUST_SIGMAS = 8.0
# Intermittent demand needs a different method family; refuse rather than mis-forecast.
MAX_ZERO_SHARE = 0.4


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
    diagnostics: dict[str, object] = field(default_factory=dict)


class ForecastUnavailable(Exception):
    """Raised when the evidence cannot support a forecast. Carries the named
    policy warning so the toolset can surface *which* gate declined."""

    def __init__(self, message: str, *, warning: str) -> None:
        super().__init__(message)
        self.warning = warning


def _replace_outliers(values: list[float]) -> tuple[list[float], int]:
    """Replace points beyond ``OUTLIER_ROBUST_SIGMAS`` robust sigmas of the median.

    Replacement is the (already cleaned) value 7 days earlier -- the same weekday --
    or the median when there is no such point. Clamping to the limit is not enough:
    on a wide series the limit can itself be far from any real observation.
    """
    if len(values) < 8:
        return values, 0
    med = statistics.median(values)
    mad = statistics.median(abs(v - med) for v in values)
    if mad == 0:
        return values, 0
    limit = OUTLIER_ROBUST_SIGMAS * 1.4826 * mad
    out = list(values)
    replaced = 0
    for i, v in enumerate(values):
        if abs(v - med) > limit:
            out[i] = out[i - 7] if i >= 7 else med
            replaced += 1
    return out, replaced


def forecast_series(
    evidence: list[EvidenceArtifact],
    *,
    horizon_days: int,
    model_id: str,
    backend: str = "holt",
) -> ForecastResult:
    """Point forecast + interval ``horizon_days`` ahead of the last observation.

    Raises ``ForecastUnavailable`` rather than returning a fabricated number.
    """
    if horizon_days < 1:
        raise ForecastUnavailable(
            f"horizon_days must be >= 1, got {horizon_days}", warning=policy.WARN_INVALID_HORIZON
        )

    warnings: list[str] = []
    usable = [e for e in evidence if e.value is not None]
    # A period that has not finished yet (its day is the evidence's as_of day or
    # later) is a partial total, not an observation: forecasting from it biases the
    # level low. Drop it and say so.
    complete = [e for e in usable if e.period_start.date() < e.as_of.date()]
    if len(complete) < len(usable):
        warnings.append(
            f"excluded {len(usable) - len(complete)} in-progress period(s) at or after the "
            "as-of date"
        )
    ordered = sorted(complete, key=lambda e: e.period_start)
    values = [float(e.value) for e in ordered]  # type: ignore[arg-type]

    if len(values) < policy.MIN_OBSERVATION_ROWS:
        raise ForecastUnavailable(
            f"{len(values)} usable point(s); forecasting needs at least "
            f"{policy.MIN_OBSERVATION_ROWS}",
            warning=policy.WARN_THIN_HISTORY,
        )

    values, clipped = _replace_outliers(values)
    if clipped:
        warnings.append(
            f"{clipped} extreme point(s) (> {OUTLIER_ROBUST_SIGMAS:g} robust sigma from the "
            "median) were replaced by the prior same-weekday value before fitting; the source "
            "history likely contains data errors"
        )

    if len(set(values)) < 2:
        raise ForecastUnavailable(
            "history is constant; there is no variation to forecast from",
            warning=policy.WARN_THIN_HISTORY,
        )
    zero_share = sum(1 for v in values if v == 0) / len(values)
    if backend == "auto" and zero_share > MAX_ZERO_SHARE:
        raise ForecastUnavailable(
            f"{zero_share:.0%} of history points are zero; intermittent series are not "
            "supported by the generic forecaster",
            warning=policy.WARN_THIN_HISTORY,
        )

    raw = get_backend(backend).forecast(values, horizon_days, season_length=7)
    if not all(math.isfinite(x) for x in (raw.point, raw.lower, raw.upper)):
        raise ForecastUnavailable(
            "model produced a non-finite forecast", warning=policy.WARN_MODEL_FIT_FAILED
        )
    point = raw.point
    method = raw.method
    low, high = raw.lower, raw.upper

    non_negative = min(values) >= 0
    if non_negative and (low < 0 or point < 0):
        # Counts, sums and rates that have never been negative cannot be negative.
        point, low, high = max(point, 0.0), max(low, 0.0), max(high, 0.0)
        warnings.append("lower bound clamped at 0: the metric has never been negative")

    warnings.extend(raw.warnings)
    low, high = round(low, 6), round(high, 6)
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
        diagnostics=dict(raw.diagnostics),
    )


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
    backend: str = "holt"
    fallback: str | None = None


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
                    backend=str(item.get("backend") or "holt"),
                    fallback=(str(item["fallback"]) if item.get("fallback") else None),
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
