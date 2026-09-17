"""Mission Blackboard + Evidence Ledger (architecture sec. 14, 30).

Shared mission knowledge, kept separate from A2A messaging. Agents post typed
artifacts and read *references*; they never pass full histories or datasets to
each other. The in-memory store here sits behind ``ArtifactStore`` so it can be
swapped for Redis / Postgres without touching agent code.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Protocol

from seleric_swarm.swarm.artifacts import SwarmArtifact

MissionEventObserver = Callable[[dict[str, Any]], None]
_event_observer: ContextVar[MissionEventObserver | None] = ContextVar(
    "seleric_mission_event_observer", default=None
)


@contextmanager
def observe_mission_events(observer: MissionEventObserver) -> Iterator[None]:
    """Observe mission events emitted in this async context."""
    token = _event_observer.set(observer)
    try:
        yield
    finally:
        _event_observer.reset(token)


observe_blackboard_events = observe_mission_events


def notify_mission_event(event: dict[str, Any]) -> None:
    """Notify the active observer without changing execution semantics."""
    observer = _event_observer.get()
    if observer is not None:
        try:
            observer(dict(event))
        except Exception:
            return


class ArtifactStore(Protocol):
    def put(self, artifact_id: str, payload: dict[str, Any]) -> None: ...
    def get(self, artifact_id: str) -> dict[str, Any] | None: ...
    def all(self) -> list[dict[str, Any]]: ...
    def delete(self, artifact_id: str) -> None: ...


class InMemoryArtifactStore:
    def __init__(self) -> None:
        self._items: dict[str, dict[str, Any]] = {}

    def put(self, artifact_id: str, payload: dict[str, Any]) -> None:
        self._items[artifact_id] = payload

    def get(self, artifact_id: str) -> dict[str, Any] | None:
        return self._items.get(artifact_id)

    def all(self) -> list[dict[str, Any]]:
        return list(self._items.values())

    def delete(self, artifact_id: str) -> None:
        self._items.pop(artifact_id, None)


class Blackboard:
    def __init__(
        self,
        mission_id: str,
        store: ArtifactStore | None = None,
        event_observer: MissionEventObserver | None = None,
    ) -> None:
        self.mission_id = mission_id
        self._store: ArtifactStore = store or InMemoryArtifactStore()
        self.evidence_ledger: list[str] = []
        self.mission_lead: str | None = None
        self.active_specialist: str | None = None
        self.leadership_epoch: int = 0
        self.handoff_history: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self._event_observer = event_observer or _event_observer.get()

    def append_event(self, event: dict[str, Any]) -> None:
        self.events.append(event)
        observer = self._event_observer
        if observer is not None:
            try:
                observer(dict(event))
            except Exception:
                # Persistence/notification observers are best-effort and must not
                # change mission execution semantics.
                return

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

    def discard(self, artifact_id: str) -> None:
        self._store.delete(artifact_id)
        self.evidence_ledger = [e for e in self.evidence_ledger if e != artifact_id]
        self.record_event("artifact_discarded", artifact_id=artifact_id)

    def discard_by(self, *, created_by: str, artifact_types: tuple[str, ...]) -> int:
        """Remove this author's prior artifacts of the given types. Lets an agent
        that runs twice in one mission (e.g. a Skeptic-driven re-diagnosis)
        replace its output instead of accumulating stale duplicates."""

        victims = [
            a["artifact_id"]
            for a in self._store.all()
            if a.get("created_by") == created_by and a.get("artifact_type") in artifact_types
        ]
        for aid in victims:
            self.discard(aid)
        return len(victims)

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

    def synthetic_summary(self) -> dict[str, Any]:
        items = self._store.all()
        synthetic = [a["artifact_id"] for a in items if a.get("synthetic")]
        total = len(items)
        return {
            "total": total,
            "synthetic": len(synthetic),
            "ratio": (len(synthetic) / total) if total else 0.0,
            "all_synthetic": total > 0 and len(synthetic) == total,
            "mixed": 0 < len(synthetic) < total,
            "synthetic_ids": synthetic,
        }

    # -- leadership --------------------------------------------------------
    def apply_transfer(self, record: dict[str, Any]) -> None:
        self.mission_lead = record.get("to_agent") or record.get("requested_target")
        self.leadership_epoch = int(record.get("epoch") or self.leadership_epoch + 1)
        self.handoff_history.append(record)
        self.record_event("leadership_transfer", **record)

    def record_event(self, kind: str, **data: Any) -> None:
        from seleric_swarm.coordinator.observability.events import family_of, now_iso

        event = {
            "kind": kind,
            "ts": now_iso(),
            "seq": len(self.events) + 1,
            "mission_id": self.mission_id,
            "family": family_of(kind),
            **data,
        }
        self.append_event(event)

    # -- state view for the LeadershipManager -----------------------------
    def leadership_state(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "mission_lead": self.mission_lead,
            "leadership_epoch": self.leadership_epoch,
            "handoff_history": list(self.handoff_history),
        }
