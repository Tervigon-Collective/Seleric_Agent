"""Notification center API: the live ads monitor's status, findings with their narration, and one-click actions.

``GET  /v1/notifications``                                       status strip, pulse, open + recently resolved findings, activity
``GET  /v1/notifications/{finding_id}/events``                    the finding's trail (state changes, actions, diagnosis)
``GET  /v1/notifications/entities/{level}/{entity_id}/activity``  activity across findings for one entity
``POST /v1/notifications/{finding_id}/label``                     useful / noise / wrong_action (the tuning signal)
``POST /v1/notifications/{finding_id}/actions/{action_id}/preview`` the exact change, checked by Seleric MCP; nothing runs
``POST /v1/notifications/actions/{request_id}/approve``           run a previewed change (needs ADS_ACTIONS_ENABLED)
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from seleric_swarm.api.mission_access import request_principal

router = APIRouter(prefix="/v1/notifications")
STALE_AFTER_TICKS = 3
RESOLVED_WINDOW_H = 24


class LabelBody(BaseModel):
    label: Literal["useful", "noise", "wrong_action"]
    note: str = Field(default="", max_length=500)


class PreviewBody(BaseModel):
    inputs: dict[str, str] = Field(default_factory=dict)


def _status(hb: dict[str, Any] | None, updated_at: datetime | None) -> dict[str, Any]:
    if not hb or updated_at is None:
        return {"status": "never_run", "age_s": None}
    age = (datetime.now(UTC) - updated_at).total_seconds()
    tick = float(hb.get("tick_s") or 300)
    status = hb.get("status") or "ok"
    if status != "down" and age > STALE_AFTER_TICKS * tick:
        status = "stale"
    return {**hb, "status": status, "age_s": round(age)}


def _timeline_item(r: dict[str, Any]) -> dict[str, Any]:
    details = r.get("details") or {}
    kind = details.get("kind") or r.get("kind")
    actor = details.get("actor") or details.get("approved_by") or details.get("requested_by") or details.get("labelled_by")
    if not actor and kind in (None, "lifecycle", "diagnosis"):
        actor = "live_monitor"
    return {
        "at": r["at"],
        "finding_id": r.get("finding_id"),
        "from_state": r.get("from_state"),
        "to_state": r.get("to_state"),
        "note": r.get("note") or "",
        "kind": kind,
        "actor": actor,
        "actor_type": details.get("actor_type") or ("user" if details.get("approved_by") or details.get("requested_by") or details.get("labelled_by") else "system"),
        "change_summary": details.get("summary") or details.get("change") or details.get("cause") or r.get("note"),
        "request_id": details.get("request_id"),
        "action_id": details.get("action_id"),
        "status": details.get("status"),
        "entity_level": details.get("entity_level"),
        "entity_id": details.get("entity_id"),
        "details": details,
        "severity": r.get("severity"),
        "title": r.get("title"),
        "entity_name": r.get("entity_name"),
        "request_status": r.get("request_status"),
    }


def _item(r: dict[str, Any], label: str | None) -> dict[str, Any]:
    d = r["diagnosis"] or {}
    e = d.get("entity") or {}
    gates = d.get("gates") or {}
    return {
        "finding_id": r["finding_id"], "state": r["state"], "severity": r["severity"], "title": d.get("title") or r["detector"],
        "rule_id": d.get("rule_id"), "tier": d.get("tier"), "platform": d.get("platform"), "summary": r["summary"],
        "narration": d.get("narration") or d.get("cause"), "narration_source": d.get("narration_source") or d.get("diagnosis_source"),
        "narrated_at": d.get("narrated_at") or d.get("diagnosed_at"),
        "cause": d.get("cause"), "bullets": d.get("bullets") or [], "artifacts": d.get("artifacts") or [],
        "diagnosis_status": d.get("status"), "gates": gates,
        "recommended_action_id": d.get("recommended_action_id"),
        "recommended_summary": d.get("recommended_summary"),
        "level": e.get("level") or r.get("entity_type"), "entity_id": e.get("id") or r.get("entity_id"),
        "entity_name": e.get("name"), "path": e.get("path") or [],
        "evidence": d.get("evidence") or {}, "actions": d.get("actions") or [], "playbook_ref": d.get("playbook_ref"),
        "impact_inr": r["impact_high"], "confidence": r["confidence"] if r["confidence"] is not None else gates.get("confidence"),
        "opened_at": r["opened_at"], "updated_at": r["updated_at"],
        "resolved_at": r["resolved_at"], "label": label,
    }


def _read(brand_id: int | None) -> dict[str, Any]:
    from seleric_swarm.services.live_monitor import config as C
    from seleric_swarm.services.live_monitor import store

    cfg, meta = C.load()
    tenants = [t for t in cfg.tenants if brand_id is None or t.brand_id == brand_id]
    brands = sorted({t.brand_id for t in tenants})
    out: dict[str, Any] = {"tenants": [], "items": [], "activity": [], "config_error": meta.get("error")}
    if not brands:
        return out
    with store.engine().connect() as conn:
        for t in tenants:
            hb = store.get_state(conn, t.tenant_id, "heartbeat")
            out["tenants"].append({"tenant_id": t.tenant_id, "name": t.name, "brand_id": t.brand_id, "platform": t.platform,
                                   "enabled": t.enabled, **_status(hb, store.state_updated_at(conn, t.tenant_id, "heartbeat"))})
        rows = [dict(r) for r in conn.execute(text(
            """SELECT f.finding_id, f.detector, f.state, f.severity, f.summary, f.diagnosis, f.impact_high, f.confidence,
                      f.opened_at, f.updated_at, f.resolved_at, f.entity_type, f.entity_id,
                      (SELECT l.label FROM monitor.labels l WHERE l.finding_id = f.finding_id ORDER BY l.labelled_at DESC LIMIT 1) AS label
               FROM monitor.findings f
               WHERE f.brand_id = ANY(:b) AND f.detector LIKE 'live.%'
                 AND (f.state <> 'resolved' OR f.resolved_at > NOW() - make_interval(hours => :h))
               ORDER BY (f.state = 'resolved'), CASE f.severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
                        f.updated_at DESC
               LIMIT 300"""), {"b": brands, "h": RESOLVED_WINDOW_H}).mappings()]
        out["items"] = [_item(r, r["label"]) for r in rows]
        out["activity"] = [dict(r) for r in conn.execute(text(
            """SELECT e.at, e.finding_id, e.from_state, e.to_state, e.note, f.severity, f.diagnosis->>'title' AS title,
                      f.diagnosis->'entity'->>'name' AS entity_name, e.details->>'kind' AS kind
               FROM monitor.finding_events e JOIN monitor.findings f USING (finding_id)
               WHERE f.brand_id = ANY(:b) AND f.detector LIKE 'live.%' AND e.note <> ''
               ORDER BY e.at DESC LIMIT 50"""), {"b": brands}).mappings()]
    open_items = [i for i in out["items"] if i["state"] != "resolved"]
    out["counts"] = {s: sum(1 for i in open_items if i["severity"] == s) for s in ("critical", "warning", "info")}
    return out


@router.get("")
async def feed(request: Request, brand_id: int | None = None) -> dict[str, Any]:
    request_principal(request)
    from seleric_swarm.services.live_monitor.actions import actions_enabled

    data = await asyncio.to_thread(_read, brand_id)
    return {**data, "actions_enabled": actions_enabled(), "server_time": datetime.now(UTC).isoformat()}


@router.get("/entities/{level}/{entity_id}/activity")
async def entity_activity(level: str, entity_id: str, request: Request, brand_id: int | None = None,
                          limit: int = 50) -> dict[str, Any]:
    """Newest-first trail for every live finding on this entity (and optional sibling findings)."""
    request_principal(request)
    from seleric_swarm.services.live_monitor import store

    lim = max(1, min(limit, 100))

    def _q() -> list[dict[str, Any]]:
        with store.engine().connect() as conn:
            sql = """
                SELECT e.at, e.finding_id, e.from_state, e.to_state, e.note, e.details,
                       f.severity, f.diagnosis->>'title' AS title,
                       f.diagnosis->'entity'->>'name' AS entity_name,
                       (SELECT r.status FROM monitor.live_action_requests r
                        WHERE r.request_id = e.details->>'request_id' LIMIT 1) AS request_status
                FROM monitor.finding_events e
                JOIN monitor.findings f USING (finding_id)
                WHERE f.detector LIKE 'live.%'
                  AND f.entity_type = :lvl AND f.entity_id = :eid
            """
            params: dict[str, Any] = {"lvl": level, "eid": entity_id, "lim": lim}
            if brand_id is not None:
                sql += " AND f.brand_id = :b"
                params["b"] = brand_id
            sql += " ORDER BY e.at DESC LIMIT :lim"
            return [_timeline_item(dict(r)) for r in conn.execute(text(sql), params).mappings()]

    return {"level": level, "entity_id": entity_id, "brand_id": brand_id, "events": await asyncio.to_thread(_q)}


@router.get("/{finding_id}/events")
async def events(finding_id: str, request: Request) -> dict[str, Any]:
    request_principal(request)
    from seleric_swarm.services.live_monitor import store

    def _q() -> list[dict[str, Any]]:
        with store.engine().connect() as conn:
            rows = conn.execute(text(
                """SELECT e.at, e.finding_id, e.from_state, e.to_state, e.note, e.details,
                          f.severity, f.diagnosis->>'title' AS title,
                          f.diagnosis->'entity'->>'name' AS entity_name,
                          (SELECT r.status FROM monitor.live_action_requests r
                           WHERE r.request_id = e.details->>'request_id' LIMIT 1) AS request_status
                   FROM monitor.finding_events e
                   JOIN monitor.findings f USING (finding_id)
                   WHERE e.finding_id = :f
                   ORDER BY e.at DESC LIMIT 100"""),
                {"f": finding_id}).mappings()
            return [_timeline_item(dict(r)) for r in rows]

    return {"finding_id": finding_id, "events": await asyncio.to_thread(_q)}


@router.post("/{finding_id}/label")
async def label(finding_id: str, body: LabelBody, request: Request) -> dict[str, Any]:
    principal = request_principal(request)
    from seleric_swarm.services.live_monitor import store

    def _w() -> bool:
        with store.engine().begin() as conn:
            row = conn.execute(text(
                """SELECT finding_id, state, entity_type, entity_id, diagnosis
                   FROM monitor.findings WHERE finding_id = :f AND detector LIKE 'live.%'"""),
                {"f": finding_id}).mappings().first()
            if row is None:
                return False
            conn.execute(text("INSERT INTO monitor.labels(finding_id, label, note, labelled_by) VALUES (:f, :l, :n, :u)"),
                         {"f": finding_id, "l": body.label, "n": body.note, "u": principal.user_id})
            ent = (row["diagnosis"] or {}).get("entity") or {}
            store.add_event(conn, finding_id, row["state"], row["state"],
                            f"labelled {body.label} by {principal.user_id}" + (f": {body.note}" if body.note else ""),
                            {"kind": "label", "actor": principal.user_id, "actor_type": "user",
                             "at": datetime.now(UTC).isoformat(),
                             "entity_level": ent.get("level") or row["entity_type"],
                             "entity_id": ent.get("id") or row["entity_id"],
                             "label": body.label, "labelled_by": principal.user_id, "note": body.note})
            return True

    if not await asyncio.to_thread(_w):
        raise HTTPException(status_code=404, detail="no such live finding")
    return {"finding_id": finding_id, "label": body.label}


async def _with_client(fn: Any) -> Any:
    """Runs ``fn(client)``; client is None when the MCP is not configured (link actions still work)."""
    from seleric_swarm.services.live_monitor.actions import ActionError
    from seleric_swarm.services.live_monitor.mcp import McpClient

    try:
        client = McpClient()
    except RuntimeError:
        client = None
    try:
        return await fn(client)
    except ActionError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc
    finally:
        if client is not None:
            await client.aclose()


@router.post("/{finding_id}/actions/{action_id}/preview")
async def preview_action(finding_id: str, action_id: str, body: PreviewBody, request: Request) -> dict[str, Any]:
    principal = request_principal(request)
    from seleric_swarm.services.live_monitor import actions

    return await _with_client(lambda c: actions.preview(c, finding_id, action_id, body.inputs, principal.user_id))


@router.post("/actions/{request_id}/approve")
async def approve_action(request_id: str, request: Request) -> dict[str, Any]:
    principal = request_principal(request)
    from seleric_swarm.services.live_monitor import actions

    if not actions.actions_enabled():
        raise HTTPException(status_code=403, detail="one-click actions are switched off (ADS_ACTIONS_ENABLED)")
    return await _with_client(lambda c: actions.approve(c, request_id, principal.user_id))
