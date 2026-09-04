"""Production-wiring adapters for the Prediction subsystem."""

from __future__ import annotations

from seleric_swarm.agents.prediction.services.model_registry import (
    feature_store_from_yaml,
    model_registry_from_yaml,
)

__all__ = ["feature_store_from_yaml", "model_registry_from_yaml"]
