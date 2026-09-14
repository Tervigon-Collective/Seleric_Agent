"""Artifact management: deduplication, lineage, claims lifecycle."""

from seleric_swarm.coordinator.artifacts.claims import ClaimManager
from seleric_swarm.coordinator.artifacts.manager import ArtifactManager

__all__ = ["ClaimManager", "ArtifactManager"]
