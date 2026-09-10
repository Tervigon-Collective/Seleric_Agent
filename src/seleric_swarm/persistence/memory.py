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

    def list_missions(self, *, limit: int = 50) -> list[dict[str, Any]]: ...


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
        # Missing/zero seq: include only on the first page (after_seq == 0).
        if after_seq > 0 and seq <= after_seq:
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
        # Refuse to clobber a cancelled mission with a later success/failure write
        # (async cancel race: background job finishes after client cancel).
        existing_raw = self._raw.get(result.mission_id)
        existing = self._results.get(result.mission_id)
        if (
            isinstance(existing_raw, dict)
            and existing_raw.get("status") == "cancelled"
            and result.status != "cancelled"
        ):
            return
        if (
            existing is not None
            and existing.status == "cancelled"
            and result.status != "cancelled"
        ):
            return
        # Cancel must not overwrite an already-terminal completion (CAS).
        if result.status == "cancelled":
            cur = None
            if isinstance(existing_raw, dict):
                cur = existing_raw.get("status")
            if cur is None and existing is not None:
                cur = existing.status
            if cur is not None and str(cur) != "running":
                return
        # Keep most-recently-written last so list_missions can page newest-first.
        self._results.pop(result.mission_id, None)
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

    def list_missions(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """Recent missions, newest first — light headers for the office switcher."""
        out: list[dict[str, Any]] = []
        for mid in reversed(list(self._results.keys())):
            raw = self._raw.get(mid)
            result = self._results.get(mid)
            events = extract_events(raw)
            out.append(
                {
                    "mission_id": mid,
                    "query": (raw or {}).get("query")
                    or getattr(result, "final_response", None)
                    or "",
                    "status": (raw or {}).get("status")
                    or (result.status if result else "unknown"),
                    "route": (raw or {}).get("route"),
                    "mission_lead": (raw or {}).get("mission_lead")
                    or (result.mission_lead if result else None),
                    "last_seq": max((int(e.get("seq") or 0) for e in events), default=0),
                }
            )
            if len(out) >= max(1, limit):
                break
        return out
