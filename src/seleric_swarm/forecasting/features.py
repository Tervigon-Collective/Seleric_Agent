"""Deterministic candidate feature generation for a forecast target."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from seleric_swarm.forecasting.policies import ForecastPolicies
from seleric_swarm.paths import repo_root
from seleric_swarm.services.catalogue_bootstrap import CatalogueSnapshot


class FeaturePriors(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    drivers: dict[str, list[str]] = Field(default_factory=dict)
    metric_hints: dict[str, list[str]] = Field(default_factory=dict)


@lru_cache(maxsize=1)
def load_feature_priors(path: str | None = None) -> FeaturePriors:
    p = Path(path) if path else repo_root() / "config" / "forecast_features.yaml"
    if not p.exists():
        return FeaturePriors()
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return FeaturePriors.model_validate(data)


def clear_feature_priors_cache() -> None:
    load_feature_priors.cache_clear()


def candidate_features(
    target_id: str,
    *,
    catalogue: CatalogueSnapshot,
    policies: ForecastPolicies,
    related_metrics: list[str] | None = None,
    priors: FeaturePriors | None = None,
) -> dict[str, list[str]]:
    """Return {co_targets, past_covariates, known_future} candidate id lists.

    Narrows the pool only; backtests (and approved bundles) make the choice.
    P0 default: univariate + calendar known-future; co-targets/covariates empty
    unless an approved bundle or metric_hints say otherwise.
    """
    priors = priors or load_feature_priors()
    known_future = ["calendar.dow", "calendar.festival", "calendar.payday"]

    target_policy = policies.target(target_id)
    bundle = target_policy.approved_bundle() if target_policy else None
    if bundle is not None:
        return {
            "co_targets": list(bundle.co_targets),
            "past_covariates": list(bundle.past_covariates),
            "known_future": list(bundle.known_future) or known_future,
        }

    hints = list(priors.metric_hints.get(target_id, []))
    related = list(related_metrics or [])
    pool = []
    for mid in hints + related:
        if mid == target_id:
            continue
        if mid in policies.blocked_features:
            continue
        if catalogue.has_metric(mid) or mid in policies.targets:
            pool.append(mid)
    # Dedup preserve order
    pool = list(dict.fromkeys(pool))

    # Without an approved bundle, P0 keeps covariates empty (univariate + calendar).
    # Candidates are still recorded for backtest search / provisional expansion later.
    return {
        "co_targets": [],
        "past_covariates": [],
        "known_future": known_future,
        "candidates": pool,  # type: ignore[dict-item]
    }


def module_of(metric_id: str, catalogue: CatalogueSnapshot) -> str | None:
    for meta in catalogue.metrics:
        if meta.id == metric_id:
            return (meta.view or str((meta.raw or {}).get("module") or "")).strip() or None
    return None
