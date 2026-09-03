"""Mission Blackboard + Evidence Ledger (architecture sec. 14, 30).

Shared mission knowledge, kept separate from A2A messaging. Agents post typed
artifacts and read *references*; they never pass full histories or datasets to
each other. The in-memory store here sits behind ``ArtifactStore`` so it can be
swapped for Redis / Postgres without touching agent code.
"""

from __future__ import annotations

from typing import Any, Protocol

from seleric_swarm.swarm.artifacts import SwarmArtifact


class ArtifactStore(Protocol):
    def put(self, artifact_id: str, payload: dict[str, Any]) -> None: ...
    def get(self, artifact_id: str) -> dict[str, Any] | None: ...
    def all(self) -> list[dict[str, Any]]: ...


class InMemoryArtifactStore:
    def __init__(self) -> None:
        self._items: dict[str, dict[str, Any]] = {}

    def put(self, artifact_id: str, payload: dict[str, Any]) -> None:
        self._items[artifact_id] = payload

    def get(self, artifact_id: str) -> dict[str, Any] | None:
        return self._items.get(artifact_id)

    def all(self) -> list[dict[str, Any]]:
        return list(self._items.values())


class Blackboard:
    def __init__(self, mission_id: str, store: ArtifactStore | None = None) -> None:
        self.mission_id = mission_id
        self._store: ArtifactStore = store or InMemoryArtifactStore()
        self.evidence_ledger: list[str] = []
        self.mission_lead: str | None = None
        self.active_specialist: str | None = None
        self.leadership_epoch: int = 0
        self.handoff_history: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []

    # -- artifacts -----------------------------------------------------------
    def post(self, artifact: SwarmArtifact) -> str:
        payload = artifact.model_dump()
        self._store.put(artifact.artifact_id, payload)
        if artifact.artifact_type == "evidence":
            self.evidence_ledger.append(artifact.artifact_id)
        self.record_event(
            "artifact_posted",
            artifact_id=artifact.artifact_id,
            artifact_type=artifact.artifact_type,
            by=artifact.created_by,
            synthetic=artifact.synthetic,
        )
        return artifact.artifact_id

    def get(self, artifact_id: str) -> dict[str, Any] | None:
        return self._store.get(artifact_id)

    def update(self, artifact_id: str, patch: dict[str, Any]) -> None:
        current = self._store.get(artifact_id)
        if current is None:
            return
        current.update(patch)
        self._store.put(artifact_id, current)
        self.record_event("artifact_updated", artifact_id=artifact_id, keys=sorted(patch))

    def by_type(self, artifact_type: str) -> list[dict[str, Any]]:
        return [a for a in self._store.all() if a.get("artifact_type") == artifact_type]

    def refs_by_type(self, artifact_type: str) -> list[str]:
        return [a["artifact_id"] for a in self.by_type(artifact_type)]

    def has_synthetic_inputs(self, evidence_refs: list[str]) -> bool:
        for ref in evidence_refs:
            payload = self._store.get(ref)
            if payload and payload.get("synthetic"):
                return True
        return False

    # -- leadership --------------------------------------------------------
    def apply_transfer(self, record: dict[str, Any]) -> None:
        self.mission_lead = record.get("to_agent") or record.get("requested_target")
        self.leadership_epoch = int(record.get("epoch") or self.leadership_epoch + 1)
        self.handoff_history.append(record)
        self.record_event("leadership_transfer", **record)

    def record_event(self, kind: str, **data: Any) -> None:
        self.events.append({"kind": kind, **data})

    # -- state view for the LeadershipManager -----------------------------
    def leadership_state(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "mission_lead": self.mission_lead,
            "leadership_epoch": self.leadership_epoch,
            "handoff_history": list(self.handoff_history),
        }
