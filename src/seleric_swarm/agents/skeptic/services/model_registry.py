"""YAML-backed ``ModelRegistry`` + a ``DriftMonitor`` port.

``YamlModelRegistry`` reads ``config/model_registry.yaml`` (falls back to the
``.example`` file) and maps each entry to a ``ModelRecord``. ``DriftMonitor`` is
the seam a real drift service (PSI / KS / JS / calibration) plugs into; the
Skeptic only ever reads a ``drift_status`` string, so a monitor's job is to
produce that string for a given model + current feature window.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from seleric_swarm.agents.skeptic.registries import (
    DriftMonitor,
    DriftReport,
    InMemoryModelRegistry,
    ModelRecord,
    NullDriftMonitor,
)
from seleric_swarm.paths import repo_root

__all__ = [
    "DriftMonitor",
    "DriftReport",
    "NullDriftMonitor",
    "YamlModelRegistry",
    "model_registry_from_yaml",
]

_CANDIDATE_PATHS = ("config/model_registry.yaml", "config/model_registry.example.yaml")


class YamlModelRegistry(InMemoryModelRegistry):
    """Same interface as InMemoryModelRegistry; seeded from YAML."""

    def __init__(self, path: str | Path | None = None) -> None:
        super().__init__()
        p = _resolve(path)
        if p is None:
            return
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        for item in data.get("models", []):
            applic = item.get("applicability", {}) or {}
            self.add(
                ModelRecord(
                    model_id=str(item["id"]),
                    version=str(item.get("version", "1")),
                    status=str(item.get("status", "candidate")),
                    target=str(item.get("target", "")),
                    model_type=str(item.get("type", "forecast")),
                    minimum_history_days=int(applic.get("minimum_history_days", 0)),
                    supports_seasonality=bool(applic.get("supports_seasonality", False)),
                    last_validated_at=item.get("last_validated_at"),
                    backtest_available=bool(
                        item.get("backtest_available")
                        or (item.get("validation") or {}).get("metric")
                        or item.get("backtest_metrics")
                    ),
                )
            )


def model_registry_from_yaml(path: str | Path | None = None) -> YamlModelRegistry:
    return YamlModelRegistry(path)


def _resolve(path: str | Path | None) -> Path | None:
    if path is not None:
        p = Path(path)
        return p if p.exists() else None
    root = repo_root()
    for candidate in _CANDIDATE_PATHS:
        p = root / candidate
        if p.exists():
            return p
    return None


# DriftMonitor / DriftReport / NullDriftMonitor are defined in
# ``agents.skeptic.registries`` (with the other ports) and re-exported above so a
# real monitor -- PSI / KS / Jensen-Shannon / calibration -- has one import site.
