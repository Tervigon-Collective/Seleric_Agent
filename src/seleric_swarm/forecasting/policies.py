"""Forecast policy registry — load and validate ``config/forecast_policies.yaml``."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from seleric_swarm.paths import repo_root

EligibilityStatus = Literal["validated", "provisional", "refused"]


class ProvisionalDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    folds: int = 4
    engine: str = "chronos-2-small"


class PolicyDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grain: Literal["day"] = "day"
    # Use as much history as the warehouse holds (up to this), but never fewer
    # than min_history_days observed days.
    context_days: int = 1095
    min_history_days: int = 270
    # Context ends this many days before the forecast date (data through
    # as_of - lag). Overrides per-target maturity_days when set.
    cutoff_lag_days: int | None = 2
    max_horizon_days: int = 90
    quantiles: list[float] = Field(default_factory=lambda: [0.1, 0.5, 0.9])
    provisional: ProvisionalDefaults = Field(default_factory=ProvisionalDefaults)


class PromotionRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_skill_vs_seasonal_naive: float = 0.0
    min_wql_gain_vs_ets: float = 0.05
    min_fold_win_rate: float = 0.6
    coverage_80_band: tuple[float, float] = (0.70, 0.90)
    min_feature_gain: float = 0.02


class Eligibility(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: EligibilityStatus
    maturity_days: int = 0
    nonnegative: bool = True
    date_basis: str | None = None


class EntityPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_horizon_days: int = 30
    max_entities: int = 6
    min_volume_share: float = 0.05


class BundleBacktest(BaseModel):
    model_config = ConfigDict(extra="allow")

    report: str | None = None
    wql: float | None = None
    mase: float | None = None
    coverage_80: float | None = None
    total_error_quantiles: dict[str, list[float]] = Field(default_factory=dict)


class FeatureBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    status: Literal["approved", "candidate", "rejected"] = "candidate"
    engine: str = "chronos-2"
    co_targets: list[str] = Field(default_factory=list)
    past_covariates: list[str] = Field(default_factory=list)
    known_future: list[str] = Field(default_factory=list)
    backtest: BundleBacktest | None = None
    approved_by: str | None = None
    approved_at: str | None = None

    @field_validator("known_future")
    @classmethod
    def only_calendar_known_future(cls, values: list[str]) -> list[str]:
        for name in values:
            if not str(name).startswith("calendar."):
                raise ValueError(
                    f"known_future may only contain calendar.* features, got {name!r}"
                )
        return values


class TargetPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eligibility: Eligibility
    # Observed (not known-future) drivers whose recent run-rate conditions the
    # level of the forecast. Fresher than the target (no maturity lag).
    drivers: list[str] = Field(default_factory=list)
    entities: dict[str, EntityPolicy] = Field(default_factory=dict)
    bundles: list[FeatureBundle] = Field(default_factory=list)

    def approved_bundle(self) -> FeatureBundle | None:
        for bundle in self.bundles:
            if bundle.status == "approved":
                return bundle
        return None

    def scored_bundle(self) -> FeatureBundle | None:
        """Approved bundle, else a candidate that carries backtest error quantiles."""
        approved = self.approved_bundle()
        if approved is not None:
            return approved
        for bundle in self.bundles:
            if (
                bundle.status == "candidate"
                and bundle.backtest
                and bundle.backtest.total_error_quantiles
            ):
                return bundle
        return None


class DerivedSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal["ratio"] = "ratio"
    numerator: str
    denominator: str
    direct_ok: bool = False


class ForecastPolicies(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    defaults: PolicyDefaults = Field(default_factory=PolicyDefaults)
    promotion: PromotionRules = Field(default_factory=PromotionRules)
    targets: dict[str, TargetPolicy] = Field(default_factory=dict)
    derived: dict[str, DerivedSpec] = Field(default_factory=dict)
    blocked_features: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_known_future_globally(self) -> ForecastPolicies:
        for mid, target in self.targets.items():
            for bundle in target.bundles:
                for name in bundle.known_future:
                    if not name.startswith("calendar."):
                        raise ValueError(
                            f"target {mid} bundle {bundle.id}: known_future {name!r} "
                            "must be calendar.*"
                        )
        return self

    def eligibility(
        self,
        target: str,
        entity: str | None = None,
        *,
        catalogue_forecast: dict[str, Any] | None = None,
    ) -> tuple[EligibilityStatus, list[str]]:
        """Return (status, reason_codes) for a catalogue metric id.

        A ``forecast`` block on the catalogue metric (Core, when present) wins
        over the Agent YAML registry. YAML remains the fallback.
        """
        block = catalogue_forecast if isinstance(catalogue_forecast, dict) else None
        if block and block.get("status") in {"validated", "provisional", "refused"}:
            status = block["status"]
            reasons = ["T_CATALOGUE"]
            entities = block.get("entities") or {}
            if entity and entities and entity not in entities:
                return "refused", ["T_ENTITY_UNSUPPORTED", "T_CATALOGUE"]
            if status == "refused":
                return "refused", [*reasons, "T_POLICY_BLOCKED"]
            return status, reasons  # type: ignore[return-value]
        policy = self.targets.get(target)
        if policy is None:
            # Eligible-but-unapproved: provisional if it looks forecastable later;
            # P0 only auto-admits targets listed in the file.
            return "refused", ["T_POLICY_MISSING"]
        status = policy.eligibility.status
        reasons: list[str] = []
        if entity and entity not in policy.entities and policy.entities:
            reasons.append("T_ENTITY_UNSUPPORTED")
            return "refused", reasons
        return status, reasons

    def target(self, metric_id: str) -> TargetPolicy | None:
        return self.targets.get(metric_id)

    def is_blocked(self, metric_id: str) -> str | None:
        return self.blocked_features.get(metric_id)


def load_forecast_policies(path: str | Path | None = None) -> ForecastPolicies:
    p = Path(path) if path is not None else repo_root() / "config" / "forecast_policies.yaml"
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return ForecastPolicies.model_validate(data)


@lru_cache(maxsize=1)
def default_policies() -> ForecastPolicies:
    return load_forecast_policies()


def clear_policy_cache() -> None:
    default_policies.cache_clear()


def catalogue_forecast_block(catalogue: Any, metric_id: str) -> dict[str, Any] | None:
    """The metric's ``raw.forecast`` eligibility block, if the catalogue carries one."""
    for meta in getattr(catalogue, "metrics", ()) or ():
        if getattr(meta, "id", None) != metric_id:
            continue
        block = (getattr(meta, "raw", None) or {}).get("forecast")
        if isinstance(block, dict) and block.get("status"):
            return block
    return None


def assert_catalogue_ids(policies: ForecastPolicies, metric_ids: set[str]) -> list[str]:
    """Return unknown metric ids referenced by the policy (for warm-up checks)."""
    unknown: list[str] = []
    for mid in policies.targets:
        if mid not in metric_ids:
            unknown.append(mid)
    for spec in policies.derived.values():
        for mid in (spec.numerator, spec.denominator):
            if mid not in metric_ids and mid not in policies.targets:
                # derived components may exist only in the catalogue
                if mid not in metric_ids:
                    unknown.append(mid)
    for mid in policies.blocked_features:
        if mid not in metric_ids:
            unknown.append(mid)
    return sorted(set(unknown))
