"""Minimal in-memory ArtifactStore — Sprint 1 v0.

Wraps ``conversations/contracts.py::Artifact`` (already-live envelope, not a
new one) so toolsets have somewhere to write typed payloads and get an id
back. Durable/queryable persistence is Profile A's later Sprint work
(``state/missions.py``); this is the smallest thing that lets Sprint 1
toolsets be built and tested against a real interface.
"""

from __future__ import annotations

from typing import Any, Literal

from seleric_swarm.conversations.contracts import Artifact, ArtifactProvenance


class ArtifactStore:
    def __init__(self) -> None:
        self._artifacts: dict[str, Artifact] = {}

    def put(
        self,
        *,
        artifact_type: str,
        payload: dict[str, Any],
        classification: Literal["ui", "factual", "derived"],
        workspace_id: str,
        mission_id: str | None = None,
        evidence_ids: list[str] | None = None,
        provenance: ArtifactProvenance | None = None,
    ) -> Artifact:
        artifact = Artifact(
            workspace_id=workspace_id,
            artifact_type=artifact_type,
            payload=payload,
            classification=classification,
            evidence_ids=evidence_ids or [],
            provenance=provenance or ArtifactProvenance(),
            mission_id=mission_id,
        ).require_provenance()
        self._artifacts[artifact.id] = artifact
        return artifact

    def get(self, artifact_id: str) -> Artifact | None:
        return self._artifacts.get(artifact_id)
