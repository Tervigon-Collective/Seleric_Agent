from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from seleric_swarm.paths import repo_root

if TYPE_CHECKING:
    from seleric_swarm.services.catalogue_bootstrap import CatalogueBootstrap, CatalogueMetricMeta

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


class MetricDefinition:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.id: str = payload["id"]
        self.version: int = int(payload.get("version", 1))
        self.owner: str = payload.get("owner", "")
        self.description: str = payload.get("description", "")
        self.formula: str = payload.get("formula", "")
        self.unit: str | None = payload.get("unit")
        self.grain: str = payload.get("grain", "day")
        self.timezone: str = payload.get("timezone", "Asia/Kolkata")
        self.domain: str = payload.get("domain", self.owner)
        self.aliases: list[str] = [str(a).lower() for a in (payload.get("aliases") or [])]
        self.catalogue_metric: str | None = payload.get("catalogue_metric") or None
        self.seleric_module: str | None = payload.get("seleric_module")
        self.direction_bad: str = payload.get("direction_bad", "up")
        self.raw = payload


class MetricRegistry:
    """Metric identity comes from the live MCP catalogue when warm.

    ``config/metric_registry.yaml`` is a cold-start / test overlay (legacy
    ``metric.*`` ids, ``seleric_module`` exceptions, direction_bad). It is not
    the metric list in production once CatalogueBootstrap is warm.
    """

    def __init__(self, config_path: str | Path) -> None:
        root = repo_root()
        path = Path(config_path)
        if not path.is_absolute():
            path = root / path
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        self._metrics = {item["id"]: MetricDefinition(item) for item in data.get("metrics", [])}
        self._by_catalogue = {
            m.catalogue_metric: m.id for m in self._metrics.values() if m.catalogue_metric
        }
        self._live: CatalogueBootstrap | None = None
        self._live_defs: dict[str, MetricDefinition] = {}

    def bind_catalogue(self, bootstrap: CatalogueBootstrap | None) -> None:
        """Use the warmed MCP catalogue as the metric repository."""
        self._live = bootstrap
        self._live_defs = {}
        if bootstrap is None or not bootstrap.is_warm():
            return
        self._live_defs = {meta.id: self._from_live(meta) for meta in bootstrap.entries()}

    def _overlay_for(self, catalogue_id: str) -> MetricDefinition | None:
        yaml_id = self._by_catalogue.get(catalogue_id)
        return self._metrics.get(yaml_id) if yaml_id else None

    def _from_live(self, meta: CatalogueMetricMeta) -> MetricDefinition:
        overlay = self._overlay_for(meta.id)
        raw = dict(meta.raw or {})
        category = str(raw.get("category") or "").lower()
        domain = _CATEGORY_DOMAIN.get(category) or (overlay.domain if overlay else "")
        payload: dict[str, Any] = {
            "id": meta.id,
            "catalogue_metric": meta.id,
            "description": meta.label or str(raw.get("description") or meta.id),
            "domain": domain,
            "owner": domain,
            "formula": str(raw.get("formula") or meta.id),
            "grain": str(raw.get("grain") or "day"),
            "timezone": "Asia/Kolkata",
        }
        if overlay is not None:
            payload["direction_bad"] = overlay.direction_bad
            payload["seleric_module"] = overlay.seleric_module
            payload["aliases"] = overlay.aliases
            payload["unit"] = overlay.unit
            if "seleric_module" in overlay.raw:
                payload["seleric_module"] = overlay.seleric_module
                raw = {**overlay.raw, **raw}
        payload["raw"] = raw
        return MetricDefinition(payload)

    def yaml_all(self) -> list[MetricDefinition]:
        """Overlay rows only — used for YAML-vs-live staleness hints."""
        return list(self._metrics.values())

    def all(self) -> list[MetricDefinition]:
        if not self._live_defs:
            return list(self._metrics.values())
        # Merge, don't replace: binding the live catalogue must not make
        # ids_for_domain()/get() forget YAML metric.* ids that callers (e.g.
        # domain_mission_update's allowlist check) already resolved earlier
        # in the same mission — a prior full-replace here caused a live grain
        # lookup for one query to silently break domain routing for every
        # other query sharing this registry instance for the rest of the process.
        merged: dict[str, MetricDefinition] = dict(self._live_defs)
        for metric_id, definition in self._metrics.items():
            merged.setdefault(metric_id, definition)
        return list(merged.values())

    def catalog_prompt(self) -> str:
        """Classifier context. Live catalogue is authoritative; this is not a metric list."""
        if self._live_defs:
            lines = [
                "The live MCP catalogue is the only metric repository. Use catalogue ids.",
                "Domain lead is deterministic from catalogue category:",
            ]
            seen: set[str] = set()
            for category, domain in _CATEGORY_DOMAIN.items():
                if domain in seen:
                    continue
                seen.add(domain)
                lines.append(f"- {category} → {domain}_agent")
            return "\n".join(lines)
        lines = []
        for metric in self.yaml_all():
            cat = metric.catalogue_metric or "(unresolved — discovered via live catalogue)"
            lines.append(f"- {metric.id} (domain={metric.domain}, catalogue={cat}): {metric.description}")
        return "\n".join(lines)

    def id_for_catalogue(self, catalogue_id: str | None) -> str | None:
        if not catalogue_id:
            return None
        if self._live is not None and self._live.has(catalogue_id):
            return catalogue_id
        if catalogue_id in self._metrics:
            return catalogue_id
        return self._by_catalogue.get(catalogue_id)

    def get(self, metric_id: str) -> MetricDefinition | None:
        if metric_id in self._live_defs:
            return self._live_defs[metric_id]
        yaml_def = self._metrics.get(metric_id)
        if yaml_def is not None:
            return yaml_def
        mapped = self._by_catalogue.get(metric_id)
        if mapped:
            return self._metrics.get(mapped)
        return None

    def ids_for_domain(self, domain: str) -> list[str]:
        return [m.id for m in self.all() if m.domain == domain]

    def owner_agent_for(self, metric_id: str) -> str | None:
        """Domain agent id that owns this metric (``{domain}_agent``)."""
        m = self.get(metric_id)
        if not m or not m.domain:
            return None
        return f"{m.domain}_agent"

    def resolve_hint(self, hint: str) -> str | None:
        """Best-effort resolve a raw classifier hint (e.g. ``metric.roas``) to a
        known metric id, checking YAML aliases and the live catalogue before
        giving up. Returns None (never the original hint) so callers can tell
        an unresolved hint apart from an already-valid one.
        """
        if self.get(hint) is not None:
            return hint
        slug = hint.removeprefix("metric.").replace("_", " ").strip().lower()
        if not slug:
            return None
        for metric in self._metrics.values():
            if slug in metric.aliases:
                return metric.id
        # ponytail: substring match on live catalogue id/description, not token-ranked
        # like catalogue_grounding.hints_from_catalogue — upgrade if false positives show up.
        for metric in self._live_defs.values():
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
    """Lead from registered metric ownership — every metric's ``domain`` in
    metric_registry.yaml already decides this generically; no per-metric
    special case needed (CAC -> performance, sales -> commerce fall out of
    ``owner_agent_for`` for free, same as any other metric)."""
    if metrics is not None:
        for hint in hints:
            owner = metrics.owner_agent_for(hint)
            if owner:
                return owner
    return "coordinator_agent"
