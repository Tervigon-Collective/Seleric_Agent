"""Artifact management: fingerprint dedup, lineage tracking, dependency invalidation."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from seleric_swarm.swarm.artifacts import SwarmArtifact
from seleric_swarm.swarm.blackboard import Blackboard


def _fingerprint(artifact: SwarmArtifact) -> str:
    """Generate a stable fingerprint for deduplication.
    
    For Evidence: mission + metric + source + time_range + dimensions + value + baseline
    For Hypothesis: mission + statement (case-normalized)
    For others: basic dedup on mission + artifact_type + key fields
    """
    data = artifact.model_dump()
    artifact_type = data.get("artifact_type")
    
    if artifact_type == "evidence":
        # Evidence fingerprint includes key identifying fields
        key_fields = {
            "mission_id": data.get("mission_id"),
            "metric_or_fact": data.get("metric_or_fact"),
            "source": data.get("source"),
            "time_range": data.get("time_range"),
            "dimensions": data.get("dimensions"),
            "value": data.get("value"),
            "baseline": data.get("baseline"),
        }
    elif artifact_type == "hypothesis":
        # Hypothesis fingerprint based on normalized statement
        statement = str(data.get("statement", "")).lower().strip()
        key_fields = {
            "mission_id": data.get("mission_id"),
            "statement": statement,
        }
    else:
        # Generic fingerprint for other types
        key_fields = {
            "mission_id": data.get("mission_id"),
            "artifact_type": artifact_type,
            "created_by": data.get("created_by"),
        }
    
    # Stable JSON serialization for hashing
    payload = json.dumps(key_fields, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


class ArtifactManager:
    """Manages artifact lifecycle: deduplication, lineage, and invalidation.
    
    Provides:
    - Fingerprint-based deduplication (Evidence, Hypothesis)
    - Dependency tracking through input_refs
    - Synthetic taint propagation
    - Dependency-aware invalidation
    """

    def __init__(self, blackboard: Blackboard) -> None:
        self.blackboard = blackboard
        self._fingerprints: dict[str, str] = {}  # fingerprint -> artifact_id
        self._dependencies: dict[str, list[str]] = {}  # artifact_id -> list of dependent IDs

    def ingest(
        self,
        artifact: SwarmArtifact,
        input_refs: list[str] | None = None,
    ) -> tuple[str, bool]:
        """Ingest an artifact with deduplication and dependency tracking.
        
        Args:
            artifact: The artifact to ingest
            input_refs: Optional list of artifact IDs this artifact depends on
        
        Returns:
            Tuple of (artifact_id, is_duplicate)
        """
        # Check for duplicate
        fp = _fingerprint(artifact)
        if fp in self._fingerprints:
            existing_id = self._fingerprints[fp]
            return (existing_id, True)
        
        # Propagate synthetic taint from inputs
        if input_refs:
            for ref in input_refs:
                parent = self.blackboard.get(ref)
                if parent and parent.get("synthetic"):
                    artifact.mark_synthetic()
                    break
        
        # Post to blackboard
        artifact_id = self.blackboard.post(artifact)
        self._fingerprints[fp] = artifact_id
        
        # Track dependencies
        if input_refs:
            for parent_id in input_refs:
                if parent_id not in self._dependencies:
                    self._dependencies[parent_id] = []
                self._dependencies[parent_id].append(artifact_id)
        
        return (artifact_id, False)

    def invalidate_dependents(self, artifact_id: str) -> list[str]:
        """Invalidate all artifacts that depend on the given artifact.
        
        When an artifact is invalidated (e.g., a causal graph is replaced),
        all artifacts that used it as input should be invalidated.
        
        Args:
            artifact_id: The artifact whose dependents should be invalidated
        
        Returns:
            List of invalidated artifact IDs
        """
        invalidated: list[str] = []
        to_process = [artifact_id]
        processed: set[str] = set()
        
        while to_process:
            current = to_process.pop(0)
            if current in processed:
                continue
            processed.add(current)
            
            # Get direct dependents
            dependents = self._dependencies.get(current, [])
            for dep_id in dependents:
                if dep_id not in invalidated:
                    invalidated.append(dep_id)
                    # Recursively invalidate their dependents
                    to_process.append(dep_id)
        
        return invalidated
