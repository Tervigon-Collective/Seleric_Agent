"""Bot triggers: when a cron trigger is due and which new monitoring findings a signal trigger picks up."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from croniter import croniter
from sqlalchemy import Connection, text

from seleric_swarm.services.bots import store


def next_cron(expr: str, tz: str, after: datetime | None = None) -> datetime:
    """Next fire time (UTC) of a 5-field cron expression read in ``tz`` (so 0 9 * * * Asia/Kolkata is 03:30 UTC)."""
    zone = ZoneInfo(tz)
    start = (after or datetime.now(UTC)).astimezone(zone)
    return croniter(expr, start).get_next(datetime).astimezone(UTC)


def _fire(conn: Connection, trig: dict[str, Any], bot: dict[str, Any], *, kind: str, reason: str, dedupe_key: str,
          detail: dict[str, Any] | None = None) -> str:
    limit = int(bot["limits"].get("max_scheduled_runs_per_day", store.DEFAULT_LIMITS["max_scheduled_runs_per_day"]))
    if store.scheduled_runs_today(conn, bot["bot_id"]) >= limit:
        store.skip_run(conn, bot["bot_id"], kind=kind, reason=reason, trigger_id=trig["trigger_id"], dedupe_key=dedupe_key,
                       why=f"Not run: the bot reached its limit of {limit} scheduled runs today.")
        return "skipped"
    return store.enqueue_run(conn, bot["bot_id"], kind=kind, reason=reason, trigger_id=trig["trigger_id"],
                             dedupe_key=dedupe_key, detail=detail)["status"]


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
        _fire(conn, trig, bot(trig["bot_id"]), kind="cron",
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
            _fire(conn, trig, b, kind="signal",
                  reason=f"{ev['severity']} finding {ev['finding_id']} ({ev['detector']}): {ev['summary']}",
                  dedupe_key=f"signal:{trig['trigger_id']}:{ev['event_id']}",  # a re-opened finding is a new event
                  detail={"finding_id": ev["finding_id"], "detector": ev["detector"], "severity": ev["severity"],
                          "opened_at": ev["at"].isoformat(), "entity": f"{ev['entity_type']}:{ev['entity_id']}"})
            counts["signal"] += 1
        if events:
            conn.execute(text("UPDATE bots.triggers SET cursor = :c, last_fired_at = :now WHERE trigger_id = :t"),
                         {"c": events[-1]["event_id"], "now": now, "t": trig["trigger_id"]})
    return counts
