"""``ArtifactStore`` — Profile A owns this (``CONTRACTS.md`` §3).

Wraps the existing, reused ``conversations.contracts.Artifact`` envelope
(unchanged) rather than inventing a new one. Profile B is the only writer
of ``artifact_type="evidence"``; Profile C is the only writer of
``"finding"``/``"causal"``/``"prediction"`` — this store does not enforce
that by itself (there's no per-writer identity to check yet); it's a
process convention until/unless a real access-control need shows up.

In-memory only for Sprint 1 — no durability guarantee across process
restarts. Evidence artifacts are immutable once put (non-negotiable rule 8):
``put`` never overwrites an existing id.
"""

from __future__ import annotations

from typing import Protocol

from seleric_swarm.conversations.contracts import Artifact


class ArtifactStore(Protocol):
    def put(self, artifact: Artifact) -> Artifact: ...

    def get(self, artifact_id: str) -> Artifact | None: ...

    def get_many(self, artifact_ids: list[str]) -> list[Artifact]: ...

    def list_for_mission(self, mission_id: str) -> list[Artifact]: ...


class InMemoryArtifactStore:
    """Reference implementation — one process, one dict, no eviction yet."""

    def __init__(self) -> None:
        self._by_id: dict[str, Artifact] = {}

    def put(self, artifact: Artifact) -> Artifact:
        if artifact.id in self._by_id:
            raise ValueError(f"artifact {artifact.id} already exists — artifacts are immutable")
        stored = artifact.require_provenance()
        self._by_id[stored.id] = stored
        return stored

    def get(self, artifact_id: str) -> Artifact | None:
        return self._by_id.get(artifact_id)

    def get_many(self, artifact_ids: list[str]) -> list[Artifact]:
        return [self._by_id[aid] for aid in artifact_ids if aid in self._by_id]

    def list_for_mission(self, mission_id: str) -> list[Artifact]:
        return [a for a in self._by_id.values() if a.mission_id == mission_id]
