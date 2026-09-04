"""YAML-backed model registry + feature store for the Prediction subsystem.

Thin wrappers over the Skeptic's ``YamlModelRegistry`` (which already reads
``config/model_registry.yaml`` / ``.example``) plus a feature-set resolver built
from the same file's ``feature_set`` fields.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from seleric_swarm.agents.prediction.registries import FeatureSetRef, InMemoryFeatureStore
from seleric_swarm.agents.skeptic.services.model_registry import (
    YamlModelRegistry,
)
from seleric_swarm.agents.skeptic.services.model_registry import (
    model_registry_from_yaml as _skeptic_model_registry_from_yaml,
)
from seleric_swarm.paths import repo_root

_CANDIDATE_PATHS = ("config/model_registry.yaml", "config/model_registry.example.yaml")


def model_registry_from_yaml(path: str | Path | None = None) -> YamlModelRegistry:
    return _skeptic_model_registry_from_yaml(path)


def feature_store_from_yaml(path: str | Path | None = None) -> InMemoryFeatureStore:
    store = InMemoryFeatureStore()
    p = _resolve(path)
    if p is None:
        return store
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    for item in data.get("models", []):
        fs = item.get("feature_set")
        if fs:
            store.add(
                str(item["id"]),
                FeatureSetRef(feature_set_id=str(fs), version=str(item.get("feature_set_version", "1")), fresh=True),
            )
    return store


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
