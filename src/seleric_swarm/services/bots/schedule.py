"""Bot triggers: when a cron trigger is due, which new monitoring findings a signal trigger picks up, and whether a
watched read changed (the watch check itself calls the live tool in the worker, outside these transactions)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from croniter import croniter
from sqlalchemy import Connection, text

from seleric_swarm.agent.live_rows import MINOR_UNIT_FIELDS
from seleric_swarm.services.bots import store


def next_cron(expr: str, tz: str, after: datetime | None = None) -> datetime:
    """Next fire time (UTC) of a 5-field cron expression read in ``tz`` (so 0 9 * * * Asia/Kolkata is 03:30 UTC)."""
    zone = ZoneInfo(tz)
    start = (after or datetime.now(UTC)).astimezone(zone)
    return croniter(expr, start).get_next(datetime).astimezone(UTC)


def fire(conn: Connection, trig: dict[str, Any], bot: dict[str, Any], *, kind: str, reason: str, dedupe_key: str | None,
         detail: dict[str, Any] | None = None, query: str = "") -> str:
    limit = int(bot["limits"].get("max_scheduled_runs_per_day", store.DEFAULT_LIMITS["max_scheduled_runs_per_day"]))
    if store.scheduled_runs_today(conn, bot["bot_id"]) >= limit:
        store.skip_run(conn, bot["bot_id"], kind=kind, reason=reason, trigger_id=trig["trigger_id"], dedupe_key=dedupe_key,
                       why=f"Not run: the bot reached its limit of {limit} scheduled runs today.")
        return "skipped"
    return store.enqueue_run(conn, bot["bot_id"], kind=kind, reason=reason, trigger_id=trig["trigger_id"],
                             dedupe_key=dedupe_key, detail=detail, query=query)["status"]


def due_cron(conn: Connection, now: datetime) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(text("""
        SELECT t.* FROM bots.triggers t JOIN bots.bots b ON b.bot_id = t.bot_id
        WHERE t.kind = 'cron' AND t.enabled AND b.enabled AND t.next_run_at <= :now
        FOR UPDATE OF t SKIP LOCKED"""), {"now": now}).mappings()]


def new_findings(conn: Connection, trig: dict[str, Any], brand_id: int, limit: int = 20) -> list[dict[str, Any]]:
    """Finding events after the trigger's cursor that newly opened a finding matching its detector and severities."""
    cfg = trig["config"]
    return [dict(r) for r in conn.execute(text("""
        SELECT e.id AS event_id, e.at, f.finding_id, f.detector, f.severity, f.summary, f.entity_type, f.entity_id
        FROM monitor.finding_events e JOIN monitor.findings f ON f.finding_id = e.finding_id
        WHERE e.id > :cursor AND e.to_state = 'open' AND f.brand_id = :brand
          AND (e.details->>'kind') IS DISTINCT FROM 'diagnosis'  -- diagnose notes are written with to_state 'open' too
          AND starts_with(f.detector, :prefix) AND f.severity = ANY(:sev)
        ORDER BY e.id LIMIT :n"""),
        {"cursor": trig["cursor"] or 0, "brand": brand_id, "prefix": cfg["detector_prefix"],
         "sev": list(cfg["severities"]), "n": limit}).mappings()]


def tick(conn: Connection, now: datetime | None = None) -> dict[str, int]:
    """One scheduler pass. Every fire carries a dedupe key, so running two ticks over the same moment queues nothing twice."""
    now = now or datetime.now(UTC)
    counts = {"cron": 0, "signal": 0}
    bots: dict[str, dict[str, Any]] = {}

    def bot(bot_id: str) -> dict[str, Any]:
        if bot_id not in bots:
            bots[bot_id] = store.get_bot(conn, bot_id)
        return bots[bot_id]

    for trig in due_cron(conn, now):
        cfg = trig["config"]
        slot = trig["next_run_at"]
        local = slot.astimezone(ZoneInfo(cfg["timezone"])).strftime("%Y-%m-%d %H:%M")
        fire(conn, trig, bot(trig["bot_id"]), kind="cron",
              reason=f"schedule '{cfg['cron']}' ({cfg['timezone']}), due {local}",
              dedupe_key=f"cron:{trig['trigger_id']}:{slot.isoformat()}",
              detail={"scheduled_for": slot.isoformat(), "late_by_s": round((now - slot).total_seconds())})
        # next slot after now: a worker that was down for hours runs the missed schedule once, not once per missed slot
        conn.execute(text("UPDATE bots.triggers SET next_run_at = :n, last_fired_at = :now WHERE trigger_id = :t"),
                     {"n": next_cron(cfg["cron"], cfg["timezone"], now), "now": now, "t": trig["trigger_id"]})
        counts["cron"] += 1

    signals = conn.execute(text("""SELECT t.* FROM bots.triggers t JOIN bots.bots b ON b.bot_id = t.bot_id
                                   WHERE t.kind = 'signal' AND t.enabled AND b.enabled
                                   FOR UPDATE OF t SKIP LOCKED""")).mappings()
    for trig in [dict(r) for r in signals]:
        b = bot(trig["bot_id"])
        events = new_findings(conn, trig, int(b["brand_id"]))
        for ev in events:
            fire(conn, trig, b, kind="signal",
                  reason=f"{ev['severity']} finding {ev['finding_id']} ({ev['detector']}): {ev['summary']}",
                  dedupe_key=f"signal:{trig['trigger_id']}:{ev['event_id']}",  # a re-opened finding is a new event
                  detail={"finding_id": ev["finding_id"], "detector": ev["detector"], "severity": ev["severity"],
                          "opened_at": ev["at"].isoformat(), "entity": f"{ev['entity_type']}:{ev['entity_id']}"})
            counts["signal"] += 1
        if events:
            conn.execute(text("UPDATE bots.triggers SET cursor = :c, last_fired_at = :now WHERE trigger_id = :t"),
                         {"c": events[-1]["event_id"], "now": now, "t": trig["trigger_id"]})
    return counts


# ---------------------------------------------------------------- watch

WATCH_KEEP_ROWS = 500  # rows of the last check kept to say what changed
WATCH_SHOWN_CHANGES = 5


def claim_watches(conn: Connection, now: datetime) -> list[dict[str, Any]]:
    """Due watch triggers, with their next check moved forward in the same transaction so no other tick takes them."""
    rows = [dict(r) for r in conn.execute(text("""
        SELECT t.*, b.brand_id FROM bots.triggers t JOIN bots.bots b ON b.bot_id = t.bot_id
        WHERE t.kind = 'watch' AND t.enabled AND b.enabled AND t.next_run_at <= :now
        FOR UPDATE OF t SKIP LOCKED"""), {"now": now}).mappings()]
    for r in rows:
        conn.execute(text("UPDATE bots.triggers SET next_run_at = :n WHERE trigger_id = :t"),
                     {"n": now + timedelta(minutes=int(r["config"]["interval_min"])), "t": r["trigger_id"]})
    return rows


def _row_key(row: dict[str, Any]) -> str:
    ids = [f"{k}={row[k]}" for k in sorted(row) if (k == "id" or k.endswith("_id")) and row[k] not in (None, "")]
    return ",".join(ids) or json.dumps(row, sort_keys=True, default=str)


def _value(field: str, value: Any) -> Any:
    """Budgets in rupees, as the bot reads them (live_rows), so a change reads '3000.0 -> 2500.0' not paise."""
    if field in MINOR_UNIT_FIELDS and value not in (None, ""):
        try:
            return float(value) / 100
        except (TypeError, ValueError):
            return value
    return value


def truncated(payload: Any) -> bool:
    """More pages exist: a row moving between pages would read as gone/new, so such a check is not trusted."""
    paging = payload.get("paging") if isinstance(payload, dict) else None
    return isinstance(paging, dict) and bool(paging.get("after") or paging.get("has_next"))


def project(payload: Any, fields: list[str]) -> dict[str, dict[str, Any]] | None:
    """The watched fields of each row, keyed by the row's ids; None when the payload is not a row list."""
    if not isinstance(payload, dict) or payload.get("success") is not True or not isinstance(payload.get("data"), list):
        return None
    out: dict[str, dict[str, Any]] = {}
    for row in payload["data"]:
        if isinstance(row, dict):
            out[_row_key(row)] = {f: _value(f, row.get(f)) for f in fields}
    return out


def fingerprint(rows: dict[str, dict[str, Any]]) -> str:
    return hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()[:16]


def changes(before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]], names: dict[str, str]) -> list[str]:
    """What moved, in words: 'Spring Sale (id=1): effective_status ACTIVE -> PAUSED', added and removed rows."""
    out = []
    for key in sorted(set(before) | set(after)):
        label = f"{names[key]} ({key})" if names.get(key) else key
        if key not in before:
            out.append(f"{label}: new")
        elif key not in after:
            out.append(f"{label}: no longer in the result (e.g. paused or deleted)")
        else:
            moved = [f"{f} {before[key].get(f)} -> {after[key].get(f)}" for f in after[key] if before[key].get(f) != after[key].get(f)]
            if moved:
                out.append(f"{label}: " + ", ".join(moved))
    return out


def record_watch(conn: Connection, trig: dict[str, Any], payload: Any, error: str | None, now: datetime) -> str:
    """Store this check and start a run when the watched fields changed since the last good check.

    Returns ``baseline`` (first good check: remembered, no run), ``same``, ``error`` (the check failed: the last good
    state stays, no run) or the enqueue status. A check is not a run, so it writes no activity row unless it fires."""
    cfg, state = trig["config"], dict(trig.get("state") or {})
    if not error and truncated(payload):
        error = "the result has more pages than one call returns; narrow the arguments (e.g. status) or raise limit"
    rows = None if error else project(payload, cfg["fields"])
    if rows is None:
        state |= {"checked_at": now.isoformat(), "error": error or "the tool did not return a row list"}
        conn.execute(text("UPDATE bots.triggers SET state = CAST(:s AS jsonb) WHERE trigger_id = :t"),
                     {"s": json.dumps(state, default=str), "t": trig["trigger_id"]})
        return "error"
    fp, prev, before = fingerprint(rows), state.get("fingerprint"), state.get("rows") or {}
    kept = dict(list(rows.items())[:WATCH_KEEP_ROWS])
    conn.execute(text("UPDATE bots.triggers SET state = CAST(:s AS jsonb) WHERE trigger_id = :t"),
                 {"s": json.dumps({"fingerprint": fp, "rows": kept, "checked_at": now.isoformat(), "error": None}, default=str),
                  "t": trig["trigger_id"]})
    if prev is None:
        return "baseline"
    if prev == fp:
        return "same"
    names = {_row_key(r): str(r.get("name") or r.get("campaign_name") or r.get("adset_name") or "")
             for r in payload["data"] if isinstance(r, dict)}
    moved = changes(before, rows, names)
    shown = "; ".join(moved[:WATCH_SHOWN_CHANGES]) + (f"; and {len(moved) - WATCH_SHOWN_CHANGES} more"
                                                      if len(moved) > WATCH_SHOWN_CHANGES else "")
    conn.execute(text("UPDATE bots.triggers SET last_fired_at = :now WHERE trigger_id = :t"), {"now": now, "t": trig["trigger_id"]})
    return fire(conn, trig, store.get_bot(conn, trig["bot_id"]), kind="watch",
                 reason=f"{cfg['tool']} {json.dumps(cfg.get('args') or {}, sort_keys=True)} changed: {shown or 'rows changed'}",
                 dedupe_key=f"watch:{trig['trigger_id']}:{now.isoformat()}",
                 detail={"tool": cfg["tool"], "fields": cfg["fields"], "changes": moved[:50], "before": prev, "after": fp})
