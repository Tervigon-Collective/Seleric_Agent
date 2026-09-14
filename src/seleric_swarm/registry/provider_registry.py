from pathlib import Path

import yaml

from seleric_swarm.paths import repo_root


class ProviderRegistry:
    """Loads config/provider_registry.yaml -- per-{domain,metric} anomaly /
    forecast strategy selection (docs/features/business-state-service
    03_DEFINITIONS_TO_MAKE_FUNCTIONAL.md #10)."""

    def __init__(self, config_path: str = "config/provider_registry.yaml"):
        path = Path(config_path)
        self.config_path = path if path.is_absolute() else repo_root() / path
        data = yaml.safe_load(self.config_path.read_text()) or {}
        self._default: dict = data.get("default") or {}
        overrides = data.get("overrides") or {}
        self._domain: dict = overrides.get("domain") or {}
        self._metric: dict = overrides.get("metric") or {}

    def _resolve(self, key: str, *, metric_id: str | None, domain: str | None) -> str:
        if metric_id and metric_id in self._metric and key in self._metric[metric_id]:
            return self._metric[metric_id][key]
        if domain and domain in self._domain and key in self._domain[domain]:
            return self._domain[domain][key]
        return self._default.get(key, "template")

    def anomaly_strategy_for(self, *, metric_id: str | None = None, domain: str | None = None) -> str:
        return self._resolve("anomaly_strategy", metric_id=metric_id, domain=domain)

    def forecast_strategy_for(self, *, metric_id: str | None = None, domain: str | None = None) -> str:
        return self._resolve("forecast_strategy", metric_id=metric_id, domain=domain)
