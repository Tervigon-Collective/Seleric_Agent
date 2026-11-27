"""Live monitor configuration: shipped YAML defaults plus one user-saved document (versioned, validated).

The saved document is the whole configuration, stored with the domain-health overrides (same volume, same atomic
replace and optimistic-concurrency version), so a save in Seleric Agent reaches the cron within one tick.
Validation happens here, at the trust boundary: unknown keys, rule ids, params, fields and actions are rejected.
"""

from __future__ import annotations

import re
from datetime import time as dtime
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from seleric_swarm.paths import repo_root
from seleric_swarm.services.domain_health.monitor_config import (
    ConfigError,
    OverrideStore,
    VersionConflict,
)
from seleric_swarm.services.live_monitor.core import conditions
from seleric_swarm.services.live_monitor.core.registry import (
    ACTIONS,
    ADAPTERS,
    RULES,
    ensure_loaded,
)

DOMAIN = "live_monitor"
FILENAME = "live_monitor.json"
DEFAULTS_PATH = "config/live_monitor.yaml"
_ID = r"^[a-z][a-z0-9_\-]{1,40}$"
MAX_CUSTOM_RULES = 50

__all__ = ["ConfigError", "LiveMonitorConfig", "VersionConflict", "load", "reset", "save", "validate"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _regex(v: str) -> str:
    if len(v) > 200:
        raise ValueError("pattern is longer than 200 characters")
    try:
        re.compile(v)
    except re.error as exc:
        raise ValueError(f"not a valid pattern: {exc}") from exc
    return v


class AttributionWindow(_Strict):
    event_type: Literal["CLICK_THROUGH", "VIEW_THROUGH", "ENGAGED_VIDEO_VIEW"]
    window_days: int = Field(ge=1, le=28)


class Expectations(_Strict):
    pixel_id: str | None = Field(default=None, pattern=r"^\d{5,20}$")
    conversion_event: str = Field(default="PURCHASE", pattern=r"^[A-Z_]{3,40}$")
    display_domain: str | None = Field(default=None, pattern=r"^[A-Za-z0-9.\-]{3,100}$")
    attribution: list[AttributionWindow] = Field(default_factory=list, max_length=4)
    compliance_name_pattern: str = ".*"
    label_from_name: str = r"^(.+)$"

    _p1 = field_validator("compliance_name_pattern")(_regex)

    @field_validator("label_from_name")
    @classmethod
    def _one_group(cls, v: str) -> str:
        _regex(v)
        if re.compile(v).groups < 1:
            raise ValueError("label_from_name needs one capture group, e.g. ^(TH-\\d+-[A-Z0-9]+)")
        return v


class TenantActions(_Strict):
    budget_step_pct: int = Field(default=20, ge=1, le=20)
    max_daily_budget_minor: int = Field(default=100000, ge=100, le=100_000_000)
    min_daily_budget_minor: int = Field(default=10000, ge=100, le=100_000_000)


class Tenant(_Strict):
    tenant_id: str = Field(pattern=_ID)
    name: str = Field(default="", max_length=80)
    brand_id: int = Field(ge=1)
    platform: str
    account_id: str = Field(pattern=r"^[A-Za-z0-9_\-]{3,40}$")
    timezone: str = "Asia/Kolkata"
    enabled: bool = True
    structure_cache_ttl_min: int = Field(default=30, ge=1, le=240)
    exclude_entity_ids: list[str] = Field(default_factory=list, max_length=500)
    expectations: Expectations = Field(default_factory=Expectations)
    actions: TenantActions = Field(default_factory=TenantActions)

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown time zone {v}") from exc
        return v


class ManualGuardrails(_Strict):
    be_roas_platform: float = Field(gt=0, le=50)
    target_roas: float = Field(gt=0, le=50)
    allowable_cpa: float = Field(gt=0, le=1_000_000)


class CampaignOverride(_Strict):
    campaign_id: str = Field(pattern=r"^\d{3,30}$")
    be_roas_platform: float | None = Field(default=None, gt=0, le=50)
    target_roas: float | None = Field(default=None, gt=0, le=50)
    allowable_cpa: float | None = Field(default=None, gt=0, le=1_000_000)


class GuardrailConfig(_Strict):
    mode: Literal["auto", "manual"] = "auto"
    target_roas_multiple: float = Field(default=1.3, ge=1.0, le=5.0)
    manual: ManualGuardrails = Field(default_factory=lambda: ManualGuardrails(be_roas_platform=2.2, target_roas=2.9, allowable_cpa=1200))
    campaign_overrides: list[CampaignOverride] = Field(default_factory=list, max_length=200)


class BandConfig(_Strict):
    p10: float | None = None
    p50: float | None = None
    p90: float | None = None
    mean: float | None = None


class BaselineOverride(BandConfig):
    level: Literal["account", "campaign", "adset", "ad"]
    entity_id: str = Field(pattern=r"^[A-Za-z0-9_\-]{1,40}$")
    metric: str = Field(pattern=r"^[a-z_]{2,40}$")


class BaselineConfig(_Strict):
    window_days: int = Field(default=14, ge=7, le=28)
    curve_days: int = Field(default=28, ge=7, le=56)
    weekday_aware: bool = True
    min_days: int = Field(default=5, ge=2, le=28)
    overrides: list[BaselineOverride] = Field(default_factory=list, max_length=500)
    defaults: dict[Literal["account", "campaign", "adset", "ad"], dict[str, BandConfig]] = Field(default_factory=dict)


class StatsConfig(_Strict):
    confidence: float = Field(default=0.8, ge=0.5, le=0.99)
    prior_events: float = Field(default=2.0, ge=0.5, le=20)
    prior_orders: float = Field(default=3.0, ge=0.5, le=20)
    prior_trials_ctr: float = Field(default=2000, ge=100, le=100_000)
    prior_trials_cvr: float = Field(default=300, ge=20, le=20_000)
    learning_min_age_days: float = Field(default=3, ge=0, le=14)


class RuleSetting(_Strict):
    enabled: bool = True
    # Forces every hit of the rule to this severity; None keeps the rule's own (warning / critical) split.
    severity: Literal["critical", "warning", "info"] | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class CustomRule(_Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,40}$")
    title: str = Field(min_length=3, max_length=120)
    platform: str
    level: Literal["account", "campaign", "adset", "ad"]
    match: Literal["all", "any"] = "all"
    conditions: list[dict[str, Any]]
    severity: Literal["critical", "warning", "info"] = "warning"
    actions: list[str] = Field(default_factory=lambda: ["open_agent"], max_length=5)
    explain_hint: str = Field(default="", max_length=300)
    enabled: bool = True


class QuietHours(_Strict):
    start: str = Field(pattern=r"^\d{2}:\d{2}$")
    end: str = Field(pattern=r"^\d{2}:\d{2}$")

    def contains(self, t: dtime) -> bool:
        s, e = dtime.fromisoformat(self.start), dtime.fromisoformat(self.end)
        return (s <= t < e) if s <= e else (t >= s or t < e)


class NotifyConfig(_Strict):
    quiet_hours: QuietHours | None = None
    cooldown_ticks: int = Field(default=2, ge=0, le=12)
    max_narrations_per_tick: int = Field(default=20, ge=0, le=100)


class LiveMonitorConfig(_Strict):
    tenants: list[Tenant] = Field(min_length=1, max_length=20)
    guardrails: GuardrailConfig = Field(default_factory=GuardrailConfig)
    baselines: BaselineConfig = Field(default_factory=BaselineConfig)
    stats: StatsConfig = Field(default_factory=StatsConfig)
    rules: dict[str, RuleSetting] = Field(default_factory=dict)
    custom_rules: list[CustomRule] = Field(default_factory=list, max_length=MAX_CUSTOM_RULES)
    notify: NotifyConfig = Field(default_factory=NotifyConfig)

    @model_validator(mode="after")
    def _registries(self) -> LiveMonitorConfig:
        ensure_loaded()
        ids = [t.tenant_id for t in self.tenants]
        if len(ids) != len(set(ids)):
            raise ValueError("tenant ids must be unique")
        for t in self.tenants:
            if t.platform not in ADAPTERS:
                raise ValueError(f"tenant {t.tenant_id}: no adapter for platform {t.platform} (have {', '.join(sorted(ADAPTERS))})")
        for rid, setting in self.rules.items():
            rule = RULES.get(rid)
            if rule is None:
                raise ValueError(f"rules.{rid}: no such rule")
            try:
                rule.params(**setting.params)
            except ValidationError as exc:
                raise ValueError(f"rules.{rid}.params: {_first_error(exc)}") from exc
        seen: set[str] = set()
        for i, cr in enumerate(self.custom_rules):
            where = f"custom_rules[{i}] ({cr.id})"
            if cr.id in seen or cr.id in RULES:
                raise ValueError(f"{where}: id is already used")
            seen.add(cr.id)
            adapter = ADAPTERS.get(cr.platform)
            if adapter is None:
                raise ValueError(f"{where}: unknown platform {cr.platform}")
            try:
                cr.conditions = conditions.validate_conditions(cr.conditions, cr.level, _catalogue(cr.platform))
            except conditions.ConditionError as exc:
                raise ValueError(f"{where}: {exc}") from exc
            for a in cr.actions:
                act = ACTIONS.get((cr.platform, a))
                if act is None or cr.level not in act.levels:
                    raise ValueError(f"{where}: action {a} is not available at {cr.level} level")
        return self

    def tenant(self, tenant_id: str) -> Tenant | None:
        return next((t for t in self.tenants if t.tenant_id == tenant_id), None)

    def rule_setting(self, rule_id: str) -> RuleSetting:
        return self.rules.get(rule_id) or RuleSetting()

    def params_for(self, rule_id: str) -> Any:
        return RULES[rule_id].params(**self.rule_setting(rule_id).params)


def _catalogue(platform: str) -> list[Any]:
    from seleric_swarm.services.live_monitor.adapters import meta

    return meta.field_catalogue() if platform == "meta" else []


def _first_error(exc: ValidationError) -> str:
    e = exc.errors()[0]
    loc = ".".join(str(x) for x in e.get("loc", ()))
    return f"{loc}: {e.get('msg')}" if loc else str(e.get("msg"))


def defaults() -> dict[str, Any]:
    return yaml.safe_load((repo_root() / DEFAULTS_PATH).read_text(encoding="utf-8"))


def _base_dir() -> Path:
    from seleric_swarm.services.domain_health.snapshot_store import SnapshotStore

    return Path(SnapshotStore().base_dir)


def store(base_dir: str | Path | None = None) -> OverrideStore:
    return OverrideStore(base_dir or _base_dir(), FILENAME)


def validate(payload: Any) -> LiveMonitorConfig:
    if not isinstance(payload, dict):
        raise ConfigError("the configuration must be a JSON object")
    try:
        return LiveMonitorConfig.model_validate(payload)
    except ValidationError as exc:
        raise ConfigError(_first_error(exc)) from exc


def load(base_dir: str | Path | None = None) -> tuple[LiveMonitorConfig, dict[str, Any]]:
    """(effective config, meta). A saved document that no longer validates (a rule was removed) falls back to the
    shipped defaults and says so in ``meta.error``; monitoring keeps running."""
    entry = store(base_dir).get(DOMAIN)
    meta: dict[str, Any] = {"custom": False, "version": 0, "updated_at": None, "updated_by": None, "error": None}
    if entry:
        meta.update(custom=True, version=entry.get("version", 0), updated_at=entry.get("updated_at"), updated_by=entry.get("updated_by"))
        try:
            return validate(entry.get("config")), meta
        except ConfigError as exc:
            meta["error"] = f"saved configuration is invalid ({exc}); using defaults"
    return validate(defaults()), meta


def save(payload: Any, *, expected_version: int | None, updated_by: str, base_dir: str | Path | None = None) -> dict[str, Any]:
    cfg = validate(payload)
    return store(base_dir).save(DOMAIN, {"config": cfg.model_dump(mode="json")}, expected_version=expected_version, updated_by=updated_by)


def reset(base_dir: str | Path | None = None) -> bool:
    return store(base_dir).reset(DOMAIN)
