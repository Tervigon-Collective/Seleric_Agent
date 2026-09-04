from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from seleric_swarm.paths import repo_root


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
        # Glossary synonyms for this metric — not questions. Production classify
        # uses the live catalogue; the fake LLM adapter may use these as a
        # test double when MCP is not in the loop.
        self.aliases: list[str] = [str(a).lower() for a in (payload.get("aliases") or [])]
        # Live catalogue measure id (defaults to bare id without metric. prefix).
        self.catalogue_metric: str = payload.get("catalogue_metric") or self.id.removeprefix("metric.")
        # Optional module override when the measure lives outside the domain agent pin.
        self.seleric_module: str | None = payload.get("seleric_module")
        self.raw = payload


class MetricRegistry:
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

    def all(self) -> list[MetricDefinition]:
        return list(self._metrics.values())

    def catalog_prompt(self) -> str:
        """Registry rows for coordinator.classify — not a phrase table."""
        lines = []
        for metric in self.all():
            lines.append(f"- {metric.id} (domain={metric.domain}, catalogue={metric.catalogue_metric}): {metric.description}")
        return "\n".join(lines)

    def id_for_catalogue(self, catalogue_id: str | None) -> str | None:
        if not catalogue_id:
            return None
        if catalogue_id in self._metrics:
            return catalogue_id
        return self._by_catalogue.get(catalogue_id)

    def get(self, metric_id: str) -> MetricDefinition | None:
        return self._metrics.get(metric_id)

    def ids_for_domain(self, domain: str) -> list[str]:
        return [m.id for m in self._metrics.values() if m.domain == domain]

    def owner_agent_for(self, metric_id: str) -> str | None:
        """Domain agent id that owns this metric (``{domain}_agent``)."""
        m = self.get(metric_id)
        if not m or not m.domain:
            return None
        return f"{m.domain}_agent"

    def require(self, metric_id: str) -> MetricDefinition:
        metric = self.get(metric_id)
        if metric is None:
            raise KeyError(f"Unknown metric id: {metric_id}")
        return metric


def lead_agent_for_hints(hints: list[str], metrics: MetricRegistry | None = None) -> str:
    """Lead from metric ownership in the registry. CAC still starts on performance."""
    if "metric.cac" in hints:
        return "performance_agent"
    if metrics is not None:
        for hint in hints:
            owner = metrics.owner_agent_for(hint)
            if owner:
                return owner
    if "metric.gross_sales" in hints or "metric.net_sales" in hints:
        return "commerce_agent"
    return "coordinator_agent"
