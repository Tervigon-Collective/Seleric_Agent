from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from seleric_swarm.services.catalogue_bootstrap import CatalogueBootstrap, CatalogueMetricMeta

log = logging.getLogger(__name__)

# Deterministic map: catalogue category → swarm domain (then ``{domain}_agent``).
# Not a metric list — MCP is the metric repository.
_CATEGORY_DOMAIN: dict[str, str] = {
    "commerce": "commerce",
    "attribution": "attribution",
    "web_analytics": "funnel",
    "webanalytics": "funnel",
    "product": "product",
    "paid_media": "performance",
    "paidmedia": "performance",
    "finance": "finance",
    "customer": "customer",
    "operations": "operations",
}

# Catalogue sibling ids share a stem (`meta_ctr` / `meta_ctr_hourly`). Grain
# itself comes from catalogue grain/view; this is only for grouping stems.
_CADENCE_SUFFIXES = ("_hourly", "_intraday", "_daily")
_INTRADAY_SUFFIXES = ("_hourly", "_intraday")


def cadence_stem(metric_id: str) -> str:
    low = metric_id.lower()
    for suffix in _CADENCE_SUFFIXES:
        if low.endswith(suffix):
            return metric_id[: -len(suffix)]
    return metric_id


def is_intraday_id(metric_id: str) -> bool:
    low = metric_id.lower()
    return any(low.endswith(suffix) for suffix in _INTRADAY_SUFFIXES)


class MetricDefinition:
    """One catalogue metric as the agent needs it. Built from the live catalogue entry; nothing else defines a metric."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.id: str = payload["id"]
        self.version: str = str(payload.get("version", "1"))
        self.owner: str = payload.get("owner", "")
        self.description: str = payload.get("description", "")
        self.formula: str = payload.get("formula", "")
        self.unit: str | None = payload.get("unit")
        self.grain: str = payload.get("grain", "day")
        self.timezone: str = payload.get("timezone", "Asia/Kolkata")
        self.domain: str = payload.get("domain", self.owner)
        self.catalogue_metric: str = self.id
        self.aliases: list[str] = [str(a).lower() for a in (payload.get("aliases") or [])]  # only the LLM test double sets these
        direction_bad = payload.get("direction_bad", "up")
        self.direction_bad: Literal["up", "down"] = (
            direction_bad if direction_bad in {"up", "down"} else "up"
        )
        self.raw = payload


class MetricRegistry:
    """The agent's view of the metric catalogue. There is no second list: identity, unit, grain, ``direction_bad`` and
    the owning domain come from the MCP catalogue (``catalogue_v3``; policy in Agent Core ``catalogue_v3_src/agent_policy.yaml``).
    Empty until the catalogue has been warmed."""

    def __init__(self) -> None:
        self._live: CatalogueBootstrap | None = None
        self._defs: dict[str, MetricDefinition] = {}

    @classmethod
    def from_definitions(cls, payloads: list[dict[str, Any]]) -> MetricRegistry:
        """A registry over fixed definitions, for test doubles that have no MCP."""
        registry = cls()
        registry._defs = {p["id"]: MetricDefinition(p) for p in payloads}
        return registry

    def bind_catalogue(self, bootstrap: CatalogueBootstrap | None) -> None:
        """Use the warmed MCP catalogue as the metric repository."""
        self._live = bootstrap
        self._defs = {}
        if bootstrap is None or not bootstrap.is_warm():
            return
        self._defs = {meta.id: self._from_live(meta) for meta in bootstrap.entries()}

    @staticmethod
    def _from_live(meta: CatalogueMetricMeta) -> MetricDefinition:
        raw = dict(meta.raw or {})
        if meta.supported_dimensions and "supported_dimensions" not in raw:
            raw["supported_dimensions"] = list(meta.supported_dimensions)
        category = str(raw.get("category") or "").lower()
        domain = str(raw.get("agent_domain") or "") or _CATEGORY_DOMAIN.get(category) or ""
        formula = raw.get("formula")
        payload: dict[str, Any] = {
            "id": meta.id,
            "version": raw.get("version") or "1",
            "description": meta.label or str(raw.get("description") or meta.id),
            "domain": domain,
            "owner": domain,
            "formula": str(formula.get("human_readable") if isinstance(formula, dict) else formula or meta.id),
            "unit": raw.get("unit"),
            "grain": str(raw.get("grain") or "day"),
            "timezone": "Asia/Kolkata",
            "direction_bad": raw.get("direction_bad", "up"),
            "raw": raw,
        }
        definition = MetricDefinition(payload)
        definition.raw = raw
        return definition

    def all(self) -> list[MetricDefinition]:
        return list(self._defs.values())

    def catalog_prompt(self) -> str:
        """Classifier context: the live catalogue ids."""
        if not self._defs:
            return "The metric catalogue is not loaded yet."
        lines = ["The live MCP catalogue is the metric repository. Use catalogue ids exactly as listed."]
        for metric in sorted(self._defs.values(), key=lambda m: m.id):
            dims = list((metric.raw or {}).get("supported_dimensions") or [])
            dim_bit = f" [dims: {', '.join(dims)}]" if dims else ""
            lines.append(f"- {metric.id} (domain={metric.domain}): {metric.description}{dim_bit}")
        return "\n".join(lines)

    def get(self, metric_id: str) -> MetricDefinition | None:
        return self._defs.get(metric_id)

    def ids_for_domain(self, domain: str) -> list[str]:
        return [m.id for m in self.all() if m.domain == domain]

    def owner_agent_for(self, metric_id: str) -> str | None:
        """Domain agent id that owns this metric (``{domain}_agent``)."""
        m = self.get(metric_id)
        if not m or not m.domain:
            return None
        return f"{m.domain}_agent"

    def resolve_hint(self, hint: str) -> str | None:
        """Resolve a classifier hint to a known catalogue id: an exact id, else a metric whose id or description
        contains the hint's words. Returns None when nothing matches."""
        if hint in self._defs:
            return hint
        slug = hint.replace("_", " ").strip().lower()
        if not slug:
            return None
        for metric in self._defs.values():
            haystack = f"{metric.id} {metric.description}".lower().replace("_", " ")
            if slug in haystack:
                return metric.id
        return None

    def require(self, metric_id: str) -> MetricDefinition:
        metric = self.get(metric_id)
        if metric is None:
            raise KeyError(f"Unknown metric id: {metric_id}")
        return metric


def lead_agent_for_hints(hints: list[str], metrics: MetricRegistry | None = None) -> str:
    """Lead agent from the owning domain of the first hinted metric (``domain`` is the catalogue category, or
    the catalogue's ``agent_domain`` where policy overrides it, e.g. CAC -> performance)."""
    if metrics is not None:
        for hint in hints:
            owner = metrics.owner_agent_for(hint)
            if owner:
                return owner
    return "coordinator_agent"


# Share-like ratios the semantic layer stores as 0–1 (Cube ``format: percent`` /
# catalogue ``unit: ratio`` for CTR, CVR, bounce rate, …). Multiples (ROAS, MER,
# LTV:CAC) stay as plain numbers — never ×100.
_PERCENT_UNITS = frozenset({"pct", "percent", "%"})
_MULTIPLE_HINT = re.compile(r"(?:^|_)(?:roas|mer|ltv_?cac|frequency)(?:_|$)", re.IGNORECASE)
_SHARE_HINT = re.compile(
    r"(?:^|_)(?:ctr|cvr|cvr_rate|hook_rate|hold_rate|bounce_rate|repeat_rate|"
    r"conversion_rate|session_conversion_rate|checkout_rate|checkout_to_purchase_rate|"
    r"product_view_rate|video_completion_rate|completion_rate)(?:_|$)"
    r"|(?:^|_).+_rate(?:_|$)",
    re.IGNORECASE,
)


def is_percent_share_metric(metric_id: str, unit: str | None = None) -> bool:
    """True when ``metric_id`` is a 0–1 share that readers expect as a percent.

    ROAS / MER / LTV:CAC stay False even when the catalogue unit is ``ratio``.
    """
    mid = (metric_id or "").split(".")[-1]
    u = (unit or "").strip().lower()
    if _MULTIPLE_HINT.search(mid):
        return False
    if u in _PERCENT_UNITS:
        return True
    if u in {"ratio", "rate"} and _SHARE_HINT.search(mid):
        return True
    return bool(not u and _SHARE_HINT.search(mid))


def percent_points(value: float) -> float:
    """Canonical 0–1 share → percent points; values already on a 0–100 scale stay.

    Cube v2 returns CTR as ``0.0238``; gold / some Meta extracts already store
    ``2.38``. Multiplying the latter again is the live double-percent bug
    (thread_c8b3c93d / MS3 campaign tables). Threshold: only scale when
    ``|value| ≤ 1``.
    """
    if abs(value) <= 1.0:
        return value * 100.0
    return value


def format_metric_value(value: float, *, metric_id: str = "", unit: str | None = None) -> str:
    """Compact display for tool summaries and merged tables.

    Share metrics print as ``2.38%`` (never raw ``0.0238``, never double-scaled).
    Other values keep the compact numeric form the model copies verbatim.
    """
    number = float(value)
    if is_percent_share_metric(metric_id, unit):
        pct = percent_points(number)
        text = f"{pct:.2f}".rstrip("0").rstrip(".")
        return f"{text}%"
    if number.is_integer():
        return str(int(number))
    if abs(number) >= 1:
        return f"{number:.2f}"
    return f"{number:.4g}"
