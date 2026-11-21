"""One-click actions on a live finding: preview (build the exact MCP calls, let the MCP check its guardrails with
validate_only), then approve (run them, collecting the MCP's confirmation token server-side).

Clicking Approve in Seleric Agent is the approval the MCP asks for; the token never reaches the browser. The MCP's own
policy (brand allow list, created-only, +10 % raise, budget cap, kill switch) still applies and its refusals are shown.
Every request and its result are kept in monitor.live_action_requests and noted on the finding's event trail.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text

from seleric_swarm.services.live_monitor import config as C
from seleric_swarm.services.live_monitor import store
from seleric_swarm.services.live_monitor.core.model import Snapshot
from seleric_swarm.services.live_monitor.core.registry import ACTIONS, ActionDef, ensure_loaded
from seleric_swarm.services.live_monitor.mcp import McpClient

PREVIEW_TTL = timedelta(minutes=10)


class ActionError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def actions_enabled() -> bool:
    return os.getenv("ADS_ACTIONS_ENABLED", "false").strip().lower() in ("1", "true", "yes")


def _dig(obj: Any, path: str) -> Any:
    for part in path.split("."):
        obj = obj.get(part) if isinstance(obj, dict) else None
    return obj


def _error_text(env: dict[str, Any]) -> str:
    err = env.get("error") or {}
    return str(err.get("message") or env.get("message") or err.get("code") or "the MCP refused the change")


def _load_finding(conn: Any, finding_id: str) -> dict[str, Any]:
    row = conn.execute(text("SELECT finding_id, brand_id, state, severity, detector, diagnosis FROM monitor.findings WHERE finding_id = :f"),
                       {"f": finding_id}).mappings().first()
    if row is None or not str(row["detector"]).startswith(store.DETECTOR_PREFIX):
        raise ActionError(404, "no such live finding")
    return dict(row)


def _clean_inputs(act: ActionDef, prefill: dict[str, str], given: dict[str, Any]) -> dict[str, str]:
    out = dict(prefill)
    for spec in act.inputs:
        if spec.name in given and given[spec.name] is not None:
            v = str(given[spec.name]).strip()
            if len(v) > spec.max_length:
                raise ActionError(422, f"{spec.label} is longer than {spec.max_length} characters")
            out[spec.name] = v
    return {k: v for k, v in out.items() if k in {s.name for s in act.inputs}}


def agent_prompt(finding: dict[str, Any]) -> str:
    d = finding["diagnosis"] or {}
    where = " › ".join(d.get("entity", {}).get("path") or []) or "the ad account"
    return (f"Live monitor alert ({finding['severity']}): {d.get('title')} on {where} "
            f"({d.get('entity', {}).get('level')} {d.get('entity', {}).get('id')}). {d.get('narration') or ''} "
            "Look into it and suggest what to do.").strip()


async def preview(client: McpClient | None, finding_id: str, action_id: str, inputs: dict[str, Any], user: str) -> dict[str, Any]:
    ensure_loaded()
    with store.engine().connect() as conn:
        finding = _load_finding(conn, finding_id)
    d = finding["diagnosis"] or {}
    if finding["state"] == "resolved":
        raise ActionError(409, "this finding is resolved")
    if action_id not in {a["id"] for a in d.get("actions") or []}:
        raise ActionError(400, f"{action_id} is not offered for this finding")
    act = ACTIONS.get((d.get("platform"), action_id))
    if act is None:
        raise ActionError(400, f"unknown action {action_id}")
    if act.build is None:
        return {"kind": "link", "action_id": action_id, "title": act.title, "prompt": agent_prompt(finding)}
    if client is None:
        raise ActionError(503, "Seleric MCP is not configured here (SELERIC_MCP_URL / SELERIC_MCP_TOKEN)")

    cfg, _ = C.load()
    tenant = cfg.tenant(d.get("tenant_id"))
    if tenant is None:
        raise ActionError(409, "the tenant of this finding is no longer configured")
    with store.engine().connect() as conn:
        raw = store.get_state(conn, tenant.tenant_id, "snapshot")
    entity = Snapshot.from_json(raw).get(d["entity"]["id"]) if raw else None
    if entity is None:
        raise ActionError(409, "this entity is no longer in the live tree (paused or deleted)")
    cleaned = _clean_inputs(act, act.prefill(entity, tenant) if act.prefill else {}, inputs or {})
    try:
        plan = await act.build(client, entity, tenant, cleaned)
    except ValueError as exc:
        raise ActionError(422, str(exc)) from exc

    checks = []
    for call in plan.calls:
        if call.from_steps:
            checks.append({"tool": call.tool, "checked": False, "note": "checked when the step before it has run"})
            continue
        env = await client.raw(call.tool, {**call.arguments, "validate_only": True})
        if env.get("success") is False or env.get("error"):
            raise ActionError(422, f"Seleric MCP would refuse this: {_error_text(env)}")
        checks.append({"tool": call.tool, "checked": True})

    request_id = f"lar_{uuid.uuid4().hex[:20]}"
    expires = datetime.now(UTC) + PREVIEW_TTL
    stored = {
        "summary": plan.summary, "current": plan.current, "change": plan.change, "inputs": cleaned, "risk": act.risk,
        "tenant_id": tenant.tenant_id, "platform": d.get("platform"), "entity": d["entity"],
        "calls": [{"tool": c.tool, "arguments": c.arguments, "from_steps": {k: list(v) for k, v in c.from_steps.items()}} for c in plan.calls],
    }
    entity = d.get("entity") or {}
    with store.engine().begin() as conn:
        conn.execute(
            text("""INSERT INTO monitor.live_action_requests(request_id, finding_id, action_id, plan, status, requested_by, expires_at)
                    VALUES (:r, :f, :a, CAST(:p AS jsonb), 'previewed', :u, :e)"""),
            {"r": request_id, "f": finding_id, "a": action_id, "p": _json(stored), "u": user, "e": expires},
        )
        store.add_event(conn, finding_id, finding["state"], finding["state"],
                        f"preview by {user}: {plan.summary}"[:500],
                        {"kind": "preview", "actor": user, "actor_type": "user", "at": datetime.now(UTC).isoformat(),
                         "entity_level": entity.get("level"), "entity_id": entity.get("id"),
                         "request_id": request_id, "action_id": action_id, "requested_by": user,
                         "summary": plan.summary, "risk": act.risk})
    return {"kind": "write", "request_id": request_id, "action_id": action_id, "title": act.title, "summary": plan.summary,
            "current": plan.current, "change": plan.change, "risk": act.risk, "checks": checks,
            "expires_at": expires.isoformat(), "enabled": actions_enabled()}


async def approve(client: McpClient | None, request_id: str, user: str) -> dict[str, Any]:
    if not actions_enabled():
        raise ActionError(403, "one-click actions are switched off (ADS_ACTIONS_ENABLED)")
    if client is None:
        raise ActionError(503, "Seleric MCP is not configured here (SELERIC_MCP_URL / SELERIC_MCP_TOKEN)")
    with store.engine().begin() as conn:
        row = conn.execute(text("""SELECT request_id, finding_id, action_id, plan, status, expires_at
                                   FROM monitor.live_action_requests WHERE request_id = :r FOR UPDATE"""),
                           {"r": request_id}).mappings().first()
        if row is None:
            raise ActionError(404, "no such action request")
        if row["status"] != "previewed":
            raise ActionError(409, f"this request is already {row['status']}")
        if row["expires_at"] < datetime.now(UTC):
            conn.execute(text("UPDATE monitor.live_action_requests SET status = 'expired' WHERE request_id = :r"), {"r": request_id})
            raise ActionError(410, "the preview expired; preview again")
        conn.execute(text("UPDATE monitor.live_action_requests SET status = 'executing', approved_by = :u WHERE request_id = :r"),
                     {"r": request_id, "u": user})
        finding = _load_finding(conn, row["finding_id"])
    plan = row["plan"]

    results: list[dict[str, Any]] = []
    error: str | None = None
    for i, call in enumerate(plan["calls"]):
        args = dict(call["arguments"])
        for arg, (step, path) in (call.get("from_steps") or {}).items():
            args[arg] = _dig(results[step], path)
            if args[arg] is None:
                error = f"step {i + 1}: could not read {path} from step {step + 1}"
                break
        if error:
            break
        args["idempotency_key"] = f"{request_id}:{i}"
        try:
            env = await client.raw(call["tool"], args)
            if env.get("status") == "APPROVAL_REQUIRED" and env.get("confirmation_token"):
                env = await client.raw(call["tool"], {**args, "confirmation_token": env["confirmation_token"]})
        except Exception as exc:
            error = f"{call['tool']}: {type(exc).__name__}: {exc}"
            break
        results.append({k: env.get(k) for k in ("success", "status", "operation", "operation_id", "approval_id", "entity", "error")})
        if env.get("success") is False or env.get("error"):
            error = f"{call['tool']}: {_error_text(env)}"
            break

    status = "failed" if error else "executed"
    outcome = {"status": status, "error": error, "results": results}
    note = f"{plan['summary']}: {'done' if not error else 'failed: ' + error}"[:500]
    entity = (finding.get("diagnosis") or {}).get("entity") or plan.get("entity") or {}
    with store.engine().begin() as conn:
        conn.execute(text("""UPDATE monitor.live_action_requests SET status = :s, result = CAST(:res AS jsonb), executed_at = NOW()
                             WHERE request_id = :r"""), {"s": status, "res": _json(outcome), "r": request_id})
        store.add_event(conn, finding["finding_id"], finding["state"], finding["state"], f"action by {user}: {note}",
                        {"kind": "action", "actor": user, "actor_type": "user", "at": datetime.now(UTC).isoformat(),
                         "entity_level": entity.get("level"), "entity_id": entity.get("id"),
                         "request_id": request_id, "action_id": row["action_id"], "approved_by": user,
                         "status": status, "change": plan.get("change"), "current": plan.get("current"), "error": error})
    return {"request_id": request_id, **outcome, "summary": plan["summary"]}


def _json(v: Any) -> str:
    return json.dumps(v, default=str)
