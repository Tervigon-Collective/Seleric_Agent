"""Shared typed contracts for the forecasting package."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class HorizonWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: date
    end: date
    n_days: int
    period_word: str = "next_n"
    unit: Literal["day", "week", "month"] = "day"


class GateDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    series: str
    action: Literal["pass", "mask", "drop", "block", "warn", "refuse"]
    detail: str = ""


class QualityVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    series: str
    action: Literal["pass", "mask", "drop", "block", "warn", "refuse"]
    detail: str = ""
    dates: list[str] = Field(default_factory=list)


class FeatureRole(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric_id: str
    role: Literal["target", "co_target", "past_covariate", "known_future"]
    bundle_id: str | None = None
    ablation_gain: float | None = None


class DailyPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: str
    mean: float
    p10: float
    p50: float
    p90: float


class TargetForecast(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric_id: str
    label: str = ""
    unit: str | None = None
    date_basis: str | None = None
    status: Literal["validated", "provisional", "refused"] = "provisional"
    entity_dimension: str | None = None
    entity_value: str | None = None
    days: list[DailyPoint] = Field(default_factory=list)
    total_mean: float | None = None
    total_p10: float | None = None
    total_p90: float | None = None
    reason_codes: list[str] = Field(default_factory=list)


class FeatureFrame(BaseModel):
    """Point-in-time daily frame for one forecast task (account or entity)."""

    model_config = ConfigDict(extra="forbid")

    cutoff: date
    context_start: date
    horizon_start: date
    horizon_end: date
    index: list[str] = Field(default_factory=list)  # ISO dates context..horizon
    targets: dict[str, list[float | None]] = Field(default_factory=dict)
    past_covariates: dict[str, list[float | None]] = Field(default_factory=dict)
    future_covariates: dict[str, dict[str, list[float | None]]] = Field(default_factory=dict)
    # future_covariates[name] = {"past": [...len context], "future": [...len horizon]}
    masks: dict[str, list[bool]] = Field(default_factory=dict)
    source_queries: dict[str, dict[str, Any]] = Field(default_factory=dict)
    content_hash: str = ""
    gate_decisions: list[GateDecision] = Field(default_factory=list)
    quality_verdicts: list[QualityVerdict] = Field(default_factory=list)


class ForecastPlanTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric_id: str
    label: str = ""
    status: Literal["validated", "provisional", "refused"]
    reason_codes: list[str] = Field(default_factory=list)
    maturity_days: int = 0
    nonnegative: bool = True
    date_basis: str | None = None
    bundle_id: str | None = None
    engine: str | None = None
    co_targets: list[str] = Field(default_factory=list)
    past_covariates: list[str] = Field(default_factory=list)
    known_future: list[str] = Field(default_factory=list)
    drivers: list[str] = Field(default_factory=list)
    is_derived: bool = False
    derived_of: list[str] = Field(default_factory=list)


class ForecastPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    targets: list[ForecastPlanTarget] = Field(default_factory=list)
    entity_dimension: str | None = None
    entity_values: list[str] = Field(default_factory=list)
    horizon: HorizonWindow
    as_of: date
    cutoff: date
    policy_version: int = 1
    notes: list[str] = Field(default_factory=list)

    def active_targets(self) -> list[ForecastPlanTarget]:
        return [t for t in self.targets if t.status != "refused"]
