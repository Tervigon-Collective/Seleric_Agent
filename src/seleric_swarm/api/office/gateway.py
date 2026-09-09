"""Read-only HTTP surface for the Seleric AI Office UI.

Routes (all GET, no side effects):

* ``GET /v1/office/missions``            — recent mission ids + light headers
* ``GET /v1/office/missions/{id}/snapshot`` — full :class:`OfficeSnapshot`
* ``GET /v1/office/missions/{id}/stream``   — SSE: ``snapshot`` then ``events`` / ``heartbeat``

The stream is a poll-and-diff bridge over the existing persistence layer: it
never touches orchestration. When the backend later grows a native event bus,
only this file changes.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from seleric_swarm.api.office.normalize import build_office_snapshot, normalize_events
from seleric_swarm.api.office.registry import known_mission_ids

router = APIRouter(prefix="/v1/office", tags=["office"])

_POLL_INTERVAL_S = 1.0
_HEARTBEAT_EVERY_S = 15.0


def _runtime() -> Any:
    # Imported lazily to avoid a circular import with seleric_swarm.main.
    from seleric_swarm.main import get_runtime

    return get_runtime()


def _raw(mission_id: str) -> dict[str, Any] | None:
    store = _runtime().store
    getter = getattr(store, "get_raw", None)
    raw = getter(mission_id) if getter else None
    if raw is None and store.get(mission_id) is not None:
        raw = store.get(mission_id).model_dump()
    return raw


@router.get("/missions")
def list_office_missions(limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    """Recent missions. Prefers the store's own listing (durable, multi-worker);
    falls back to the in-process registry when the store cannot enumerate."""
    store = _runtime().store
    seen: set[str] = set()
    out: list[dict[str, Any]] = []

    lister = getattr(store, "list_missions", None)
    if callable(lister):
        try:
            for m in lister(limit=limit):
                mid = m.get("mission_id") or m.get("missionId")
                if not mid or mid in seen:
                    continue
                seen.add(mid)
                out.append(
                    {
                        "missionId": mid,
                        "query": m.get("query") or "",
                        "status": m.get("status") or "running",
                        "route": m.get("route"),
                        "missionLead": m.get("mission_lead") or m.get("missionLead"),
                        "lastSeq": int(m.get("last_seq") or m.get("lastSeq") or 0),
                    }
                )
        except Exception:  # noqa: S110 - degrade to the registry below
            pass

    for mid in known_mission_ids():
        if len(out) >= limit or mid in seen:
            continue
        raw = _raw(mid)
        if not raw:
            continue
        seen.add(mid)
        out.append(
            {
                "missionId": mid,
                "query": raw.get("query") or "",
                "status": raw.get("status") or "running",
                "route": raw.get("route"),
                "missionLead": raw.get("mission_lead"),
                "lastSeq": max(
                    (int(e.get("seq") or 0) for e in (raw.get("events") or []) if isinstance(e, dict)),
                    default=0,
                ),
            }
        )
    return {"count": len(out), "missions": out[:limit]}


@router.get("/missions/{mission_id}/snapshot")
def office_snapshot(mission_id: str) -> dict[str, Any]:
    raw = _raw(mission_id)
    if raw is None:
        raise HTTPException(status_code=404, detail="mission not found")
    return build_office_snapshot(raw, mission_id=mission_id)


def _sse(event: str, data: Any) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n".encode()


@router.get("/missions/{mission_id}/stream")
async def office_stream(mission_id: str, request: Request) -> StreamingResponse:
    raw = _raw(mission_id)
    if raw is None:
        raise HTTPException(status_code=404, detail="mission not found")

    async def gen() -> Any:
        snap = build_office_snapshot(raw, mission_id=mission_id)
        yield _sse("snapshot", snap)
        last_seq = int(snap.get("lastSeq") or 0)
        idle = 0.0
        while True:
            if await request.is_disconnected():
                break
            await asyncio.sleep(_POLL_INTERVAL_S)
            idle += _POLL_INTERVAL_S
            cur = _raw(mission_id)
            events = [e for e in (cur or {}).get("events", []) if isinstance(e, dict)]
            fresh = [e for e in events if int(e.get("seq") or 0) > last_seq]
            if fresh:
                ui_events = normalize_events(events, mission_id=mission_id, after_seq=last_seq)
                for ue in ui_events:
                    yield _sse("event", ue)
                last_seq = max(int(e.get("seq") or 0) for e in fresh)
                # Re-emit a fresh derived snapshot so late joiners / drifted
                # clients converge without replaying the whole log.
                yield _sse("snapshot", build_office_snapshot(cur, mission_id=mission_id))
                idle = 0.0
            elif idle >= _HEARTBEAT_EVERY_S:
                yield _sse("heartbeat", {"ts": (cur or {}).get("status"), "lastSeq": last_seq})
                idle = 0.0
            status = str((cur or {}).get("status") or "")
            if status in {"completed", "prototype_completed", "failed", "cancelled", "blocked", "partial"}:
                yield _sse("done", {"status": status, "lastSeq": last_seq})
                break

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
