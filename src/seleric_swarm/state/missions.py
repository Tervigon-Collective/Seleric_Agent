"""V3 Mission model + store (Profile A — Sprint 1 scaffolding).

Replaces ad hoc mission dict/context threading in ``coordinator/graph.py``.
Deliberately separate from ``contracts.lookup.MissionResult`` /
``persistence.memory.MissionStore`` (the swarm_v2 pipeline's shapes) — the
two pipelines run side by side until this one clears its parity gate
(``docs/refactor/00_OVERVIEW.md`` §5), so they must not share mutable state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, Protocol

MissionStatus = Literal["running", "completed", "partial", "failed", "cancelled"]


@dataclass
class Mission:
    mission_id: str
    query: str
    as_of: datetime
    workspace_id: str
    owner_user_id: str
    status: MissionStatus = "running"
    thread_id: str | None = None
    run_id: str | None = None
    final_response: str | None = None
    error_code: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class MissionStore(Protocol):
    def create(self, mission: Mission) -> Mission: ...

    def get(self, mission_id: str) -> Mission | None: ...

    def update_status(self, mission_id: str, status: MissionStatus) -> Mission | None: ...

    def finish(
        self,
        mission_id: str,
        *,
        status: MissionStatus,
        final_response: str | None = None,
        error_code: str | None = None,
    ) -> Mission | None: ...

    def list_for_workspace(self, workspace_id: str, *, limit: int = 50) -> list[Mission]: ...


class InMemoryMissionStore:
    """Reference implementation — one process, one dict."""

    def __init__(self) -> None:
        self._by_id: dict[str, Mission] = {}

    def create(self, mission: Mission) -> Mission:
        self._by_id[mission.mission_id] = mission
        return mission

    def get(self, mission_id: str) -> Mission | None:
        return self._by_id.get(mission_id)

    def update_status(self, mission_id: str, status: MissionStatus) -> Mission | None:
        mission = self._by_id.get(mission_id)
        if mission is None:
            return None
        mission.status = status
        mission.updated_at = datetime.now(UTC)
        return mission

    def finish(
        self,
        mission_id: str,
        *,
        status: MissionStatus,
        final_response: str | None = None,
        error_code: str | None = None,
    ) -> Mission | None:
        mission = self._by_id.get(mission_id)
        if mission is None:
            return None
        mission.status = status
        mission.final_response = final_response
        mission.error_code = error_code
        mission.updated_at = datetime.now(UTC)
        return mission

    def list_for_workspace(self, workspace_id: str, *, limit: int = 50) -> list[Mission]:
        matches = [m for m in self._by_id.values() if m.workspace_id == workspace_id]
        matches.sort(key=lambda m: m.created_at, reverse=True)
        return matches[:limit]
