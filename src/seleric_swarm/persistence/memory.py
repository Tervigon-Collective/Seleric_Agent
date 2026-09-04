from __future__ import annotations

from typing import Any, Protocol

from seleric_swarm.contracts.lookup import MissionResult


class MissionStore(Protocol):
    def put(self, result: MissionResult, raw_state: dict[str, Any] | None = None) -> None: ...

    def get(self, mission_id: str) -> MissionResult | None: ...

    def get_raw(self, mission_id: str) -> dict[str, Any] | None: ...

    def list_events(
        self,
        mission_id: str,
        *,
        family: str | None = None,
        after_seq: int = 0,
        limit: int = 500,
    ) -> list[dict[str, Any]]: ...


def extract_events(raw_state: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Pull structured events from a persisted raw mission payload."""
    if not raw_state:
        return []
    events = raw_state.get("events")
    if isinstance(events, list):
        return [e for e in events if isinstance(e, dict)]
    return []


def filter_events(
    events: list[dict[str, Any]],
    *,
    family: str | None = None,
    after_seq: int = 0,
    limit: int = 500,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for event in events:
        seq = int(event.get("seq") or 0)
        if seq and seq <= after_seq:
            continue
        if family:
            fam = event.get("family") or ""
            kind = str(event.get("kind") or "")
            if fam != family and not kind.startswith(f"{family}_"):
                continue
        out.append(event)
        if len(out) >= max(1, limit):
            break
    return out


class InMemoryMissionStore:
    def __init__(self) -> None:
        self._results: dict[str, MissionResult] = {}
        self._raw: dict[str, dict[str, Any]] = {}
        self._events: dict[str, list[dict[str, Any]]] = {}

    def put(self, result: MissionResult, raw_state: dict[str, Any] | None = None) -> None:
        self._results[result.mission_id] = result
        if raw_state is not None:
            self._raw[result.mission_id] = raw_state
            self._events[result.mission_id] = extract_events(raw_state)
        elif result.mission_id not in self._events:
            self._events[result.mission_id] = []

    def get(self, mission_id: str) -> MissionResult | None:
        return self._results.get(mission_id)

    def get_raw(self, mission_id: str) -> dict[str, Any] | None:
        return self._raw.get(mission_id)

    def list_events(
        self,
        mission_id: str,
        *,
        family: str | None = None,
        after_seq: int = 0,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        return filter_events(
            self._events.get(mission_id) or extract_events(self._raw.get(mission_id)),
            family=family,
            after_seq=after_seq,
            limit=limit,
        )
