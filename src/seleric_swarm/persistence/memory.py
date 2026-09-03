from __future__ import annotations

from typing import Any, Protocol

from seleric_swarm.contracts.lookup import MissionResult


class MissionStore(Protocol):
    def put(self, result: MissionResult, raw_state: dict[str, Any] | None = None) -> None: ...

    def get(self, mission_id: str) -> MissionResult | None: ...


class InMemoryMissionStore:
    def __init__(self) -> None:
        self._results: dict[str, MissionResult] = {}
        self._raw: dict[str, dict[str, Any]] = {}

    def put(self, result: MissionResult, raw_state: dict[str, Any] | None = None) -> None:
        self._results[result.mission_id] = result
        if raw_state is not None:
            self._raw[result.mission_id] = raw_state

    def get(self, mission_id: str) -> MissionResult | None:
        return self._results.get(mission_id)

    def get_raw(self, mission_id: str) -> dict[str, Any] | None:
        return self._raw.get(mission_id)
