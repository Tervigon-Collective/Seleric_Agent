"""What a rule can see besides its entity: baselines, finance guardrails, tenant config, stats settings, the clock."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from seleric_swarm.services.live_monitor.core.model import Entity, Snapshot

# Size-dependent metrics: a parent's daily spend says nothing about a child's, so these never fall back up the tree.
VOLUME_METRICS = frozenset({"spend", "impressions", "purchases", "link_clicks", "lpv", "purchase_value"})


@dataclass(frozen=True)
class Band:
    p10: float | None
    p50: float | None
    p90: float | None
    mean: float | None = None  # pooled value over the window (sum of parts / sum of parts)
    n: int = 0
    source: str = "default"


@dataclass
class BaselineBook:
    """Per-entity baselines with fallback to the parent, the account, then shipped defaults per level."""

    rows: dict[tuple[str, str, str, int | None], Band] = field(default_factory=dict)
    defaults: dict[str, dict[str, Band]] = field(default_factory=dict)
    curves: dict[str, list[float]] = field(default_factory=dict)  # metric -> 24 cumulative shares by end of hour
    min_days: int = 5

    def get(self, snapshot: Snapshot | None, e: Entity, metric: str, weekday: int | None = None) -> Band | None:
        volume = metric in VOLUME_METRICS
        chain = [e] if volume or snapshot is None else snapshot.lineage(e)
        for node in chain:
            if weekday is not None:
                wd = self.rows.get((node.level, node.id, metric, weekday))
                if wd is not None and wd.n >= 2:
                    return wd
            row = self.rows.get((node.level, node.id, metric, None))
            if row is not None and row.n >= self.min_days:
                return row
        for level in (e.level,) if volume else (e.level, "account"):
            d = (self.defaults.get(level) or {}).get(metric)
            if d is not None:
                return d
        return None

    def cum_share(self, metric: str, hour: int, minute: int = 0) -> float | None:
        """Expected share of the day's ``metric`` done by ``hour:minute``, linear inside the hour."""
        curve = self.curves.get(metric)
        if not curve or len(curve) != 24:
            return None
        before = curve[hour - 1] if hour > 0 else 0.0
        return before + (curve[hour] - before) * (minute / 60.0)

    def hour_share(self, metric: str, hour: int) -> float | None:
        curve = self.curves.get(metric)
        if not curve or len(curve) != 24:
            return None
        return curve[hour] - (curve[hour - 1] if hour > 0 else 0.0)


@dataclass(frozen=True)
class Guardrails:
    be_roas_platform: float
    target_roas: float
    allowable_cpa: float
    be_roas_net: float | None = None
    contribution_margin: float | None = None
    net_aov: float | None = None
    meta_aov: float | None = None
    working: str = ""
    source: str = "default"
    computed_at: str | None = None
    campaign_overrides: dict[str, dict[str, float]] = field(default_factory=dict)

    def for_campaign(self, campaign_id: str | None) -> Guardrails:
        o = self.campaign_overrides.get(campaign_id or "")
        if not o:
            return self
        return replace(self, source=f"{self.source}+campaign override", campaign_overrides={},
                       **{k: float(v) for k, v in o.items()})

    def to_json(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in ("be_roas_platform", "target_roas", "allowable_cpa", "be_roas_net",
                                               "contribution_margin", "net_aov", "meta_aov", "working", "source",
                                               "computed_at")}


@dataclass
class RuleContext:
    snapshot: Snapshot
    baselines: BaselineBook
    guardrails: Guardrails
    tenant: Any
    stats: Any
    now_local: datetime
    tracking: dict[str, Any] = field(default_factory=dict)

    def guard(self, e: Entity) -> Guardrails:
        return self.guardrails.for_campaign(e.campaign_id or (e.id if e.level == "campaign" else None))

    def band(self, e: Entity, metric: str, weekday: bool = False) -> Band | None:
        return self.baselines.get(self.snapshot, e, metric, self.now_local.weekday() if weekday else None)

    def prior_rate(self, e: Entity) -> float:
        """Purchases per rupee expected from history (entity, then parents), else from the allowable CPA."""
        b = self.band(e, "purchases_per_inr")
        if b is not None and b.mean:
            return float(b.mean)
        return 1.0 / self.guard(e).allowable_cpa

    def prior_aov(self, e: Entity) -> float:
        b = self.band(e, "aov")
        if b is not None and (b.mean or b.p50):
            return float(b.mean or b.p50)
        return float(self.guardrails.meta_aov or 2500.0)

    def learning(self, e: Entity) -> bool:
        """The entity (or the ad set it delivers through) is in Meta's learning phase or younger than the guard age."""
        for node in self.snapshot.lineage(e):
            if node.level == "adset" and str((node.attrs.get("learning_stage_info") or {}).get("status", "")).upper() == "LEARNING":
                return True
            if node.level in ("campaign", "adset", "ad") and node.created_at is not None:
                age_days = (self.now_local - node.created_at.astimezone(self.now_local.tzinfo)).total_seconds() / 86400
                if age_days < float(self.stats.learning_min_age_days):
                    return True
        return False
