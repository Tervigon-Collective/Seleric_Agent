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
        self.mcp_capability: str | None = payload.get("mcp_capability")
        self.raw = payload


class MetricRegistry:
    def __init__(self, config_path: str | Path) -> None:
        root = repo_root()
        path = Path(config_path)
        if not path.is_absolute():
            path = root / path
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        self._metrics = {item["id"]: MetricDefinition(item) for item in data.get("metrics", [])}

    def get(self, metric_id: str) -> MetricDefinition | None:
        return self._metrics.get(metric_id)

    def ids_for_domain(self, domain: str) -> list[str]:
        return [m.id for m in self._metrics.values() if m.domain == domain]

    def require(self, metric_id: str) -> MetricDefinition:
        metric = self.get(metric_id)
        if metric is None:
            raise KeyError(f"Unknown metric id: {metric_id}")
        return metric
