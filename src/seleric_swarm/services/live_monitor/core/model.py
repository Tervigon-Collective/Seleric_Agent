"""Platform-neutral entity model. Adapters normalise their platform into these types; rules read only these."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

Level = Literal["account", "campaign", "adset", "ad"]
LEVELS: tuple[str, ...] = ("account", "campaign", "adset", "ad")
SEVERITIES: tuple[str, ...] = ("critical", "warning", "info")
SEVERITY_RANK = {"critical": 0, "warning": 1, "info": 2}
CADENCES: dict[str, int] = {"5m": 300, "1h": 3600, "daily": 86400}
# Metric windows an adapter fills: today so far, the trailing 3 and 7 days including today, and the 7 days before the
# last 3 (the reference for trend rules).
WINDOWS: tuple[str, ...] = ("today", "3d", "7d", "prev7d")
ADDITIVE = ("spend", "impressions", "link_clicks", "lpv", "purchases", "purchase_value", "atc", "ic")


def derive(m: dict[str, float | None]) -> dict[str, float | None]:
    """Ratio metrics from additive parts. A ratio with an empty denominator is None, never 0."""
    def div(a: str, b: str, scale: float = 1.0) -> float | None:
        num, den = m.get(a), m.get(b)
        return None if num is None or not den else num / den * scale

    out = dict(m)
    out["cpm"] = div("spend", "impressions", 1000.0)
    out["ctr_link"] = div("link_clicks", "impressions", 100.0)
    out["cpa"] = div("spend", "purchases")
    out["roas"] = div("purchase_value", "spend")
    out["aov"] = div("purchase_value", "purchases")
    out["lpv_per_click"] = div("lpv", "link_clicks")
    out["cvr_lpv"] = div("purchases", "lpv", 100.0)
    return out


def sum_metrics(rows: list[dict[str, float | None]]) -> dict[str, float | None]:
    total: dict[str, float | None] = {k: 0.0 for k in ADDITIVE}
    for r in rows:
        for k in ADDITIVE:
            total[k] = (total[k] or 0.0) + float(r.get(k) or 0.0)
    return derive(total)


@dataclass
class Entity:
    platform: str
    tenant_id: str
    level: str
    id: str
    name: str
    parent_id: str | None = None
    campaign_id: str | None = None
    adset_id: str | None = None
    status: str = ""
    effective_status: str = ""
    created_at: datetime | None = None
    attrs: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, dict[str, float | None]] = field(default_factory=dict)

    @property
    def active(self) -> bool:
        return self.effective_status == "ACTIVE"

    def metric(self, window: str, key: str) -> float | None:
        return (self.metrics.get(window) or {}).get(key)

    def get(self, path: str) -> Any:
        """Dotted lookup: ``status``, ``attrs.targeting.instagram_positions``, ``metrics.today.spend``."""
        head, _, rest = path.partition(".")
        if head == "attrs":
            node: Any = self.attrs
        elif head == "metrics":
            node = self.metrics
        else:
            return getattr(self, head, None) if not rest else None
        for part in rest.split(".") if rest else []:
            if not isinstance(node, dict):
                return None
            node = node.get(part)
        return node

    def to_json(self) -> dict[str, Any]:
        return {
            "platform": self.platform, "tenant_id": self.tenant_id, "level": self.level, "id": self.id, "name": self.name,
            "parent_id": self.parent_id, "campaign_id": self.campaign_id, "adset_id": self.adset_id, "status": self.status,
            "effective_status": self.effective_status, "created_at": self.created_at.isoformat() if self.created_at else None,
            "attrs": self.attrs, "metrics": self.metrics,
        }

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> Entity:
        created = d.get("created_at")
        return cls(**{**d, "created_at": datetime.fromisoformat(created) if created else None})


@dataclass
class Snapshot:
    tenant_id: str
    platform: str
    fetched_at: datetime
    entities: list[Entity]
    # entity id -> hour of day (account time zone) -> additive metrics, today only
    hourly: dict[str, dict[int, dict[str, float]]] = field(default_factory=dict)
    partial_errors: list[str] = field(default_factory=list)
    _by_id: dict[str, Entity] = field(default_factory=dict, init=False, repr=False)
    _deliverable: set[str] = field(default_factory=set, init=False, repr=False)

    def __post_init__(self) -> None:
        self._by_id = {e.id: e for e in self.entities}
        # Bottom-up: an ACTIVE campaign or ad set whose ads are all paused cannot spend (brand 20 keeps ~180 of these).
        live_parents: set[str] = set()
        for level in ("ad", "adset", "campaign"):
            for e in self.at(level):
                if e.active and (level == "ad" or e.id in live_parents):
                    self._deliverable.add(e.id)
            live_parents = {e.parent_id for e in self.at(level) if e.id in self._deliverable and e.parent_id}

    def deliverable(self, e: Entity) -> bool:
        """Can ``e`` spend right now: it is active and has an active ad beneath it (the account always can)."""
        return e.level == "account" or e.id in self._deliverable

    def get(self, entity_id: str | None) -> Entity | None:
        return self._by_id.get(entity_id) if entity_id else None

    def account(self) -> Entity | None:
        return next((e for e in self.entities if e.level == "account"), None)

    def at(self, level: str) -> list[Entity]:
        return [e for e in self.entities if e.level == level]

    def lineage(self, e: Entity) -> list[Entity]:
        """The entity and its ancestors, nearest first, ending at the account."""
        out, cur, seen = [e], e, {e.id}
        while cur.parent_id and cur.parent_id not in seen:
            nxt = self.get(cur.parent_id)
            if nxt is None:
                break
            out.append(nxt)
            seen.add(nxt.id)
            cur = nxt
        return out

    def path_names(self, e: Entity) -> list[str]:
        return [x.name for x in reversed(self.lineage(e)) if x.level != "account"]

    def to_json(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id, "platform": self.platform, "fetched_at": self.fetched_at.isoformat(),
            "entities": [e.to_json() for e in self.entities],
            "hourly": {k: {str(h): v for h, v in hours.items()} for k, hours in self.hourly.items()},
            "partial_errors": self.partial_errors,
        }

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> Snapshot:
        return cls(
            tenant_id=d["tenant_id"], platform=d["platform"], fetched_at=datetime.fromisoformat(d["fetched_at"]),
            entities=[Entity.from_json(e) for e in d["entities"]],
            hourly={k: {int(h): v for h, v in hours.items()} for k, hours in (d.get("hourly") or {}).items()},
            partial_errors=list(d.get("partial_errors") or []),
        )


@dataclass(frozen=True)
class FieldDef:
    """A field custom rules may test. ``type`` drives operator choice and value checking."""

    path: str
    type: Literal["number", "string", "string_list", "bool"]
    levels: tuple[str, ...]
    label: str
    unit: str = ""


@dataclass(frozen=True)
class Hit:
    """What one rule found on one entity."""

    severity: str
    summary: str
    evidence: dict[str, Any] = field(default_factory=dict)
    impact_inr: float | None = None
    confidence: float | None = None
    actions: tuple[str, ...] = ()


@dataclass(frozen=True)
class Finding:
    finding_id: str
    tenant_id: str
    brand_id: int
    platform: str
    rule_id: str
    title: str
    tier: str
    playbook_ref: str
    entity: Entity
    path: tuple[str, ...]
    hit: Hit

    @property
    def detector(self) -> str:
        return f"live.{self.platform}.{self.rule_id}"
