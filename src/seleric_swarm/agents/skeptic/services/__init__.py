"""Production-wiring adapters for Skeptic collaborator Protocols.

Each adapter implements a Protocol from ``agents.skeptic.registries`` /
``agents.skeptic.context`` against a real repo service. They are *optional*: the
in-memory defaults in ``registries`` keep the Skeptic fully functional and
deterministic without them. Inject an adapter through ``SkepticDeps`` when the
backing service exists.
"""

from __future__ import annotations

from seleric_swarm.agents.skeptic.services.business_rules import (
    ConstraintStore,
    ConstraintStoreBusinessRuleService,
    InMemoryConstraintStore,
)
from seleric_swarm.agents.skeptic.services.dowhy_causal import DoWhyCausalValidationService
from seleric_swarm.agents.skeptic.services.model_registry import (
    DriftMonitor,
    NullDriftMonitor,
    YamlModelRegistry,
    model_registry_from_yaml,
)

__all__ = [
    "ConstraintStore",
    "ConstraintStoreBusinessRuleService",
    "DoWhyCausalValidationService",
    "DriftMonitor",
    "InMemoryConstraintStore",
    "NullDriftMonitor",
    "YamlModelRegistry",
    "model_registry_from_yaml",
]
