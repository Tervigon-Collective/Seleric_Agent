from __future__ import annotations

import statistics
from datetime import datetime
from typing import Any

from seleric_swarm.domain.models import FeatureValue, Freshness, QualityFlag, SeriesPoint

STRATEGY_VERSION = "v1"


def _window_values(values: list[float], window: str | None) -> list[float]:
    """``Nd`` window = last N daily points. Only defined for ``feature_class:
    daily_series`` metrics (config/business_state_profiles.yaml) -- a
    ``windowed_point`` metric like repeat_rate needs a different strategy,
    deferred to Sprint 4 (05_SPRINT_PLAN.md).
    """
    if not window or not window.endswith("d"):
        return values
    n = int(window[:-1])
    return values[-n:]


def compute_features(
    series: list[SeriesPoint], feature_specs: list[dict[str, Any]]
) -> tuple[dict[str, FeatureValue], list[QualityFlag]]:
    """Compute the Sprint 1 feature set (03 SS4 default_v1 profile).

    ``freshness_age`` is excluded here -- it comes from query provenance
    (see ``classify_freshness``), not the series values.
    """
    values = [p.value for p in series if p.value is not None]
    flags: list[QualityFlag] = []
    features: dict[str, FeatureValue] = {}
    for spec in feature_specs:
        feature_id = spec["id"]
        strategy = spec["strategy"]
        if strategy == "freshness_age":
            continue
        min_points = spec.get("min_points", 1)
        if len(values) < min_points:
            if "SPARSE_HISTORY" not in flags:
                flags.append("SPARSE_HISTORY")
            continue
        if strategy == "current_value":
            features[feature_id] = FeatureValue(value=values[-1], strategy_version=STRATEGY_VERSION)
        elif strategy == "period_delta_pct":
            if len(values) < 2 or values[-2] == 0:
                if "SPARSE_HISTORY" not in flags:
                    flags.append("SPARSE_HISTORY")
                continue
            pct = (values[-1] - values[-2]) / values[-2] * 100
            features[feature_id] = FeatureValue(
                value=pct, window=spec.get("window"), strategy_version=STRATEGY_VERSION
            )
        elif strategy == "rolling_mean":
            window_values = _window_values(values, spec.get("window"))
            features[feature_id] = FeatureValue(
                value=statistics.mean(window_values), window=spec.get("window"), strategy_version=STRATEGY_VERSION
            )
        elif strategy == "rolling_std":
            window_values = _window_values(values, spec.get("window"))
            features[feature_id] = FeatureValue(
                value=statistics.pstdev(window_values), window=spec.get("window"), strategy_version=STRATEGY_VERSION
            )
    return features, flags


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def classify_freshness(
    provenance: dict[str, Any], profile: dict[str, Any]
) -> tuple[Freshness, float | None]:
    """Threshold classification of ``generated_at - cube_last_refresh``.

    Both fields are already returned by every ``metrics_query`` call (live-
    validated, 06_DATA_VALIDATION_FINDINGS.md#1) -- this is not new plumbing,
    just the threshold comparison against the profile's
    ``stale_after_hours``/``late_after_hours``.
    """
    freshness_block = provenance.get("freshness") or {}
    generated_at = provenance.get("generated_at")
    cube_last_refresh = freshness_block.get("cube_last_refresh")
    if not generated_at or not cube_last_refresh:
        return "UNKNOWN", None
    age_hours = (_parse_ts(generated_at) - _parse_ts(cube_last_refresh)).total_seconds() / 3600
    thresholds = profile.get("freshness") or {}
    stale_after = thresholds.get("stale_after_hours", 36)
    late_after = thresholds.get("late_after_hours", 12)
    if age_hours >= stale_after:
        return "STALE", age_hours
    if age_hours >= late_after:
        return "LATE", age_hours
    return "CURRENT", age_hours
