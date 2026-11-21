"""Postgres access for Bots (schema ``bots``, migrations 022-023): registry, triggers, runs, activity, skills."""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml
from sqlalchemy import Connection, Engine, create_engine, text

from seleric_swarm.config.settings import get_settings
from seleric_swarm.protocols.mcp.servers.seleric_remote import ADS_READ_TOOLS

TEMPLATES_DIR = Path("config/bots")  # relative to the working dir, like config/live_monitor.yaml (/app in the image)

# Phase 1 is read-only: a bot may only add these Seleric MCP tools to the agent's base (catalogue/metrics) set.
# Write tools reach bots in Phase 3, through per-bot D-239 keys, never through this list.
READ_TOOLS: frozenset[str] = frozenset(ADS_READ_TOOLS)
TRIGGER_KINDS = ("cron", "signal", "watch", "webhook")
# Triggers with no person waiting: they merge into a queued run and count toward max_scheduled_runs_per_day.
AUTO_KINDS = ("cron", "signal", "watch", "webhook")
WATCH_MIN_INTERVAL = 5  # minutes; live-monitor-cron already reads the same account every 5
MAX_NOTIFY = 20
DEFAULT_LIMITS: dict[str, int] = {"max_scheduled_runs_per_day": 24, "max_tool_calls": 40, "max_pending_proposals": 5}


class BotError(ValueError):
    pass


class VersionConflict(BotError):
    pass


@lru_cache(maxsize=1)
def engine() -> Engine:
    return create_engine(get_settings().database_url, pool_pre_ping=True, pool_size=3, max_overflow=2)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def _j(value: Any) -> str:
    return json.dumps(value, default=str)


# ---------------------------------------------------------------- templates

def templates() -> dict[str, dict[str, Any]]:
    out = {}
    for path in sorted(TEMPLATES_DIR.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text()) or {}
        out[doc["template"]] = doc
    return out


def check_tools(tools: list[str]) -> list[str]:
    bad = sorted(set(tools) - READ_TOOLS)
    if bad:
        raise BotError(f"not allowed for bots in this phase (read-only): {', '.join(bad)}")
    return sorted(set(tools))


def check_trigger(kind: str, config: dict[str, Any]) -> dict[str, Any]:
    from seleric_swarm.services.bots.schedule import (
        next_cron,  # validates the expression and timezone
    )

    if kind not in TRIGGER_KINDS:
        raise BotError(f"trigger kind must be one of {TRIGGER_KINDS}")
    if kind == "cron":
        try:
            next_cron(str(config.get("cron") or ""), str(config.get("timezone") or "Asia/Kolkata"))
        except Exception as exc:  # croniter / zoneinfo raise several types
            raise BotError(f"invalid cron schedule: {exc}") from exc
        return {"cron": str(config["cron"]).strip(), "timezone": str(config.get("timezone") or "Asia/Kolkata")}
    if kind == "webhook":
        return {}
    if kind == "watch":
        tool, fields = str(config.get("tool") or ""), [str(f) for f in config.get("fields") or [] if str(f).strip()]
        if tool not in READ_TOOLS:
            raise BotError(f"watch tool must be one of the bot read tools, got {tool!r}")
        if not fields:
            raise BotError("watch trigger needs the fields whose change should start a run (e.g. effective_status)")
        args = config.get("args") or {}
        if not isinstance(args, dict):
            raise BotError("watch args must be an object")
        interval = int(config.get("interval_min") or 15)
        if interval < WATCH_MIN_INTERVAL:
            raise BotError(f"watch interval must be at least {WATCH_MIN_INTERVAL} minutes")
        return {"tool": tool, "args": args, "fields": fields, "interval_min": interval}
    sev = [s for s in (config.get("severities") or ["critical", "warning"]) if s in ("critical", "warning", "info")]
    prefix = str(config.get("detector_prefix") or "").strip()
    if not prefix or not sev:
        raise BotError("signal trigger needs detector_prefix and at least one severity")
    return {"detector_prefix": prefix, "severities": sev}


def check_notify(notify: dict[str, Any]) -> dict[str, Any]:
    """B19: who gets this bot's emails. Each entry is a contact name, group or address; it must resolve in the email
    contacts (allowed domains) when mail is set up. Without mail the list is kept as typed and nothing is sent."""
    from seleric_swarm.mail.recipients import Clarify, resolve
    from seleric_swarm.mail.session import get_mail_runtime

    refs = list(dict.fromkeys(str(r).strip() for r in (notify.get("email") or []) if str(r).strip()))
    if len(refs) > MAX_NOTIFY:
        raise BotError(f"at most {MAX_NOTIFY} email recipients")
    mail = get_mail_runtime()
    for ref in refs if mail else []:
        found = resolve(ref, mail.contacts)
        if isinstance(found, Clarify):
            raise BotError(found.reason)
    return {"email": refs}


# ---------------------------------------------------------------- bots

_BOT_COLS = ("bot_id, brand_id, name, template, role, enabled, instructions, instructions_version, tools, limits, notify, "
             "version, created_by, updated_by, created_at, updated_at")
_TRIGGER_COLS = "trigger_id, bot_id, kind, config, enabled, next_run_at, cursor, last_fired_at, state, created_at"


def list_bots(conn: Connection) -> list[dict[str, Any]]:
    rows = conn.execute(text(f"""
        SELECT {_BOT_COLS},
          (SELECT MIN(t.next_run_at) FROM bots.triggers t WHERE t.bot_id = b.bot_id AND t.enabled AND t.kind = 'cron') AS next_run_at,
          (SELECT row_to_json(r) FROM (SELECT run_id, status, outcome, trigger_kind, created_at, finished_at
               FROM bots.runs WHERE bot_id = b.bot_id ORDER BY created_at DESC LIMIT 1) r) AS last_run
        FROM bots.bots b ORDER BY b.created_at""")).mappings()
    return [dict(r) for r in rows]


def get_bot(conn: Connection, bot_id: str) -> dict[str, Any]:
    row = conn.execute(text(f"SELECT {_BOT_COLS} FROM bots.bots WHERE bot_id = :b"), {"b": bot_id}).mappings().first()
    if row is None:
        raise KeyError(bot_id)
    bot = dict(row)
    bot["limits"] = {**DEFAULT_LIMITS, **(bot.get("limits") or {})}
    bot["triggers"] = list_triggers(conn, bot_id)
    bot["skills"] = list_skills(conn, bot_id)
    return bot


def create_bot(conn: Connection, *, template: str, actor: str, name: str | None = None, brand_id: int = 20) -> dict[str, Any]:
    tpl = templates().get(template)
    if tpl is None:
        raise BotError(f"unknown template {template!r}")
    bot_id = new_id("bot")
    conn.execute(text("""
        INSERT INTO bots.bots(bot_id, brand_id, name, template, role, instructions, tools, limits, notify, created_by, updated_by)
        VALUES (:b, :brand, :name, :tpl, :role, :ins, CAST(:tools AS jsonb), CAST(:limits AS jsonb), CAST(:notify AS jsonb), :a, :a)"""),
        {"b": bot_id, "brand": brand_id, "name": (name or tpl["name"]).strip(), "tpl": template, "role": tpl.get("role", ""),
         "ins": tpl.get("instructions", ""), "tools": _j(check_tools(tpl.get("tools") or [])), "limits": _j(DEFAULT_LIMITS),
         "notify": _j(check_notify(tpl.get("notify") or {})), "a": actor})
    conn.execute(text("INSERT INTO bots.instruction_versions(bot_id, version, body, author) VALUES (:b, 1, :body, :a)"),
                 {"b": bot_id, "body": tpl.get("instructions", ""), "a": actor})
    for trig in tpl.get("triggers") or []:
        add_trigger(conn, bot_id, kind=trig["kind"], config=trig.get("config") or {}, enabled=trig.get("enabled", True))
    for skill in tpl.get("skills") or []:
        save_skill(conn, bot_id, name=skill["name"], body=skill["body"], author=actor)
    return get_bot(conn, bot_id)


def update_bot(conn: Connection, bot_id: str, *, expected_version: int, actor: str, changes: dict[str, Any]) -> dict[str, Any]:
    current = get_bot(conn, bot_id)
    if current["version"] != expected_version:
        raise VersionConflict(f"bot changed since you opened it (version {current['version']}, you had {expected_version})")
    sets, params = ["version = version + 1", "updated_by = :a", "updated_at = NOW()"], {"b": bot_id, "a": actor}
    for key in ("name", "role", "enabled"):
        if key in changes:
            sets.append(f"{key} = :{key}")
            params[key] = changes[key]
    if "tools" in changes:
        sets.append("tools = CAST(:tools AS jsonb)")
        params["tools"] = _j(check_tools(list(changes["tools"])))
    if "notify" in changes:
        sets.append("notify = CAST(:notify AS jsonb)")
        params["notify"] = _j(check_notify(changes["notify"] or {}))
    if "limits" in changes:
        limits = {k: int(v) for k, v in (changes["limits"] or {}).items() if k in DEFAULT_LIMITS and int(v) >= 0}
        sets.append("limits = CAST(:limits AS jsonb)")
        params["limits"] = _j({**current["limits"], **limits})
    if "instructions" in changes and changes["instructions"] != current["instructions"]:
        version = current["instructions_version"] + 1
        sets += ["instructions = :ins", "instructions_version = :iv"]
        params |= {"ins": changes["instructions"], "iv": version}
        conn.execute(text("INSERT INTO bots.instruction_versions(bot_id, version, body, author) VALUES (:b, :v, :body, :a)"),
                     {"b": bot_id, "v": version, "body": changes["instructions"], "a": actor})
    conn.execute(text(f"UPDATE bots.bots SET {', '.join(sets)} WHERE bot_id = :b"), params)
    return get_bot(conn, bot_id)


# ---------------------------------------------------------------- triggers

def list_triggers(conn: Connection, bot_id: str) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(text(
        f"SELECT {_TRIGGER_COLS} FROM bots.triggers WHERE bot_id = :b ORDER BY created_at"), {"b": bot_id}).mappings()]


def secret_hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def webhook_trigger(conn: Connection, trigger_id: str) -> dict[str, Any] | None:
    """The enabled webhook trigger of an enabled bot, with its secret hash (only the hook endpoint reads that)."""
    row = conn.execute(text("""SELECT t.trigger_id, t.bot_id, t.secret_hash FROM bots.triggers t
                               JOIN bots.bots b ON b.bot_id = t.bot_id
                               WHERE t.trigger_id = :t AND t.kind = 'webhook' AND t.enabled AND b.enabled"""),
                       {"t": trigger_id}).mappings().first()
    return dict(row) if row else None


def add_trigger(conn: Connection, bot_id: str, *, kind: str, config: dict[str, Any], enabled: bool = True) -> dict[str, Any]:
    from seleric_swarm.services.bots.schedule import next_cron

    config = check_trigger(kind, config)
    # cron: next slot; watch: first check right away (it records the baseline, it does not fire)
    next_run = next_cron(config["cron"], config["timezone"]) if kind == "cron" else datetime.now(UTC) if kind == "watch" else None
    # a new signal trigger starts at the newest finding event: it reacts to what happens from now on, not to history
    cursor = (conn.execute(text("SELECT COALESCE(MAX(id), 0) FROM monitor.finding_events")).scalar()
              if kind == "signal" else None)
    secret = secrets.token_urlsafe(32) if kind == "webhook" else None
    trigger_id = new_id("trg")
    conn.execute(text("""INSERT INTO bots.triggers(trigger_id, bot_id, kind, config, enabled, next_run_at, cursor, secret_hash)
                         VALUES (:t, :b, :k, CAST(:c AS jsonb), :e, :n, :cur, :sh)"""),
                 {"t": trigger_id, "b": bot_id, "k": kind, "c": _j(config), "e": enabled, "n": next_run, "cur": cursor,
                  "sh": secret_hash(secret) if secret else None})
    row = dict(conn.execute(text(f"SELECT {_TRIGGER_COLS} FROM bots.triggers WHERE trigger_id = :t"),
                            {"t": trigger_id}).mappings().one())
    return {**row, "secret": secret} if secret else row  # shown once; only its hash is kept


def set_trigger_enabled(conn: Connection, bot_id: str, trigger_id: str, enabled: bool) -> None:
    from seleric_swarm.services.bots.schedule import next_cron

    row = conn.execute(text("SELECT kind, config FROM bots.triggers WHERE trigger_id = :t AND bot_id = :b"),
                       {"t": trigger_id, "b": bot_id}).mappings().first()
    if row is None:
        raise KeyError(trigger_id)
    next_run = (None if not enabled else next_cron(row["config"]["cron"], row["config"]["timezone"]) if row["kind"] == "cron"
                else datetime.now(UTC) if row["kind"] == "watch" else None)
    conn.execute(text("UPDATE bots.triggers SET enabled = :e, next_run_at = :n WHERE trigger_id = :t"),
                 {"e": enabled, "n": next_run, "t": trigger_id})


def delete_trigger(conn: Connection, bot_id: str, trigger_id: str) -> None:
    conn.execute(text("DELETE FROM bots.triggers WHERE trigger_id = :t AND bot_id = :b"), {"t": trigger_id, "b": bot_id})


# ---------------------------------------------------------------- runs

def enqueue_run(conn: Connection, bot_id: str, *, kind: str, reason: str, query: str = "", trigger_id: str | None = None,
                dedupe_key: str | None = None, requested_by: str = "", detail: dict[str, Any] | None = None) -> dict[str, Any]:
    """Queue a run, or fold this trigger into the bot's run that is still waiting to start.

    Returns ``{"run_id", "status": "queued" | "merged" | "duplicate"}``. A scheduled/signal trigger whose
    ``dedupe_key`` was already used is a no-op, so a second scheduler tick or a repeated signal never runs twice.
    """
    if dedupe_key:
        seen = conn.execute(text("SELECT run_id FROM bots.runs WHERE dedupe_key = :k UNION ALL "
                                 "SELECT run_id FROM bots.runs WHERE merged @> CAST(:m AS jsonb) LIMIT 1"),
                            {"k": dedupe_key, "m": _j([{"dedupe_key": dedupe_key}])}).first()
        if seen:
            return {"run_id": seen[0], "status": "duplicate"}
    entry = {"kind": kind, "reason": reason, "trigger_id": trigger_id, "dedupe_key": dedupe_key, **(detail or {})}
    if kind in AUTO_KINDS:  # chat/manual always get their own run: the person is waiting for that answer
        waiting = conn.execute(text("""SELECT run_id FROM bots.runs WHERE bot_id = :b AND status = 'queued'
                                       AND trigger_kind = ANY(:auto) ORDER BY created_at LIMIT 1 FOR UPDATE"""),
                               {"b": bot_id, "auto": list(AUTO_KINDS)}).first()
        if waiting:
            conn.execute(text("UPDATE bots.runs SET merged = merged || CAST(:m AS jsonb) WHERE run_id = :r"),
                         {"m": _j([entry]), "r": waiting[0]})
            _activity(conn, waiting[0], bot_id, kind="trigger", outcome="completed",
                      summary=f"Merged {kind} trigger: {reason}", detail=entry)
            return {"run_id": waiting[0], "status": "merged"}
    run_id = new_id("run")
    conn.execute(text("""INSERT INTO bots.runs(run_id, bot_id, trigger_id, trigger_kind, reason, query, dedupe_key, requested_by)
                         VALUES (:r, :b, :t, :k, :reason, :q, :d, :by)"""),
                 {"r": run_id, "b": bot_id, "t": trigger_id, "k": kind, "reason": reason, "q": query, "d": dedupe_key,
                  "by": requested_by})
    _activity(conn, run_id, bot_id, kind="trigger", outcome="completed", summary=f"{kind.capitalize()}: {reason}", detail=entry)
    return {"run_id": run_id, "status": "queued"}


def skip_run(conn: Connection, bot_id: str, *, kind: str, reason: str, why: str, trigger_id: str | None,
             dedupe_key: str | None) -> None:
    """A trigger fired but a limit stopped it: keep a visible record instead of dropping it silently."""
    run_id = new_id("run")
    conn.execute(text("""INSERT INTO bots.runs(run_id, bot_id, trigger_id, trigger_kind, reason, status, dedupe_key, outcome,
                         finished_at) VALUES (:r, :b, :t, :k, :reason, 'skipped', :d, 'skipped', NOW())"""),
                 {"r": run_id, "b": bot_id, "t": trigger_id, "k": kind, "reason": reason, "d": dedupe_key})
    _activity(conn, run_id, bot_id, kind="note", outcome="skipped", summary=why)


def scheduled_runs_today(conn: Connection, bot_id: str, tz: str = "Asia/Kolkata") -> int:
    return int(conn.execute(text("""SELECT COUNT(*) FROM bots.runs WHERE bot_id = :b AND trigger_kind = ANY(:auto)
                                    AND status <> 'skipped'
                                    AND (created_at AT TIME ZONE :tz)::date = (NOW() AT TIME ZONE :tz)::date"""),
                            {"b": bot_id, "tz": tz, "auto": list(AUTO_KINDS)}).scalar() or 0)


def claim_run(conn: Connection) -> dict[str, Any] | None:
    """The oldest queued run of an enabled bot that has nothing running (one run at a time per bot)."""
    row = conn.execute(text("""
        SELECT r.* FROM bots.runs r JOIN bots.bots b ON b.bot_id = r.bot_id
        WHERE r.status = 'queued' AND b.enabled
          AND NOT EXISTS (SELECT 1 FROM bots.runs x WHERE x.bot_id = r.bot_id AND x.status = 'running')
        ORDER BY r.created_at LIMIT 1 FOR UPDATE OF r SKIP LOCKED""")).mappings().first()
    if row is None:
        return None
    conn.execute(text("UPDATE bots.runs SET status = 'running', started_at = NOW() WHERE run_id = :r"), {"r": row["run_id"]})
    return dict(row)


def finish_run(conn: Connection, run_id: str, *, status: str, outcome: str | None, answer: str, error: str | None,
               mission_id: str | None, instructions_version: int | None, tool_calls: int) -> None:
    conn.execute(text("""UPDATE bots.runs SET status = :s, outcome = :o, answer = :a, error = :e, mission_id = :m,
                         instructions_version = :iv, tool_calls = :tc, finished_at = NOW() WHERE run_id = :r"""),
                 {"s": status, "o": outcome, "a": answer, "e": error, "m": mission_id, "iv": instructions_version,
                  "tc": tool_calls, "r": run_id})


def previous_auto_outcome(conn: Connection, bot_id: str, run_id: str) -> str | None:
    """Outcome of the bot's last finished automatic run before this one (for the 'unverified twice' rule, B20)."""
    return conn.execute(text("""SELECT outcome FROM bots.runs WHERE bot_id = :b AND run_id <> :r AND trigger_kind = ANY(:auto)
                                AND status IN ('completed', 'partial', 'failed') ORDER BY finished_at DESC NULLS LAST LIMIT 1"""),
                        {"b": bot_id, "r": run_id, "auto": list(AUTO_KINDS)}).scalar()


def fail_stale_runs(conn: Connection, older_than_s: int) -> int:
    """A worker that died mid-run leaves 'running' rows; free the bot so its queue moves again."""
    return conn.execute(text("""UPDATE bots.runs SET status = 'failed', error = 'worker stopped during the run',
                                finished_at = NOW() WHERE status = 'running'
                                AND started_at < NOW() - make_interval(secs => :s)"""), {"s": older_than_s}).rowcount


def list_runs(conn: Connection, bot_id: str, *, kind: str | None = None, limit: int = 30) -> list[dict[str, Any]]:
    where = "bot_id = :b" + (" AND trigger_kind = :k" if kind else "")
    return [dict(r) for r in conn.execute(text(
        f"SELECT * FROM bots.runs WHERE {where} ORDER BY created_at DESC LIMIT :n"),
        {"b": bot_id, "k": kind, "n": limit}).mappings()]


def list_activity(conn: Connection, bot_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(text(
        "SELECT * FROM bots.activity WHERE bot_id = :b ORDER BY id DESC LIMIT :n"), {"b": bot_id, "n": limit}).mappings()]


def _activity(conn: Connection, run_id: str, bot_id: str, *, kind: str, outcome: str, summary: str = "",
              tool: str | None = None, target: str | None = None, scope: str | None = None, decision: str | None = None,
              detail: dict[str, Any] | None = None, at: datetime | None = None) -> None:
    conn.execute(text("""INSERT INTO bots.activity(run_id, bot_id, at, kind, tool, target, scope, decision, outcome, summary, detail)
                         VALUES (:r, :b, COALESCE(:at, NOW()), :k, :tool, :target, :scope, :d, :o, :s, CAST(:detail AS jsonb))"""),
                 {"r": run_id, "b": bot_id, "at": at, "k": kind, "tool": tool, "target": target, "scope": scope, "d": decision,
                  "o": outcome, "s": summary[:500], "detail": _j(detail or {})})


add_activity = _activity


# ---------------------------------------------------------------- skills

def list_skills(conn: Connection, bot_id: str) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(text(
        "SELECT * FROM bots.skills WHERE bot_id = :b ORDER BY name"), {"b": bot_id}).mappings()]


def save_skill(conn: Connection, bot_id: str, *, name: str, body: str, author: str, enabled: bool = True,
               skill_id: str | None = None) -> dict[str, Any]:
    """Create a skill, or replace one (by id). Names are unique per bot."""
    name, body = name.strip(), body.strip()
    if not name or not body:
        raise BotError("a skill needs a name and a body")
    taken = conn.execute(text("SELECT skill_id FROM bots.skills WHERE bot_id = :b AND name = :n"), {"b": bot_id, "n": name}).first()
    if taken and taken[0] != skill_id:
        raise BotError(f"this bot already has a skill named {name!r}")
    if skill_id:
        done = conn.execute(text("""UPDATE bots.skills SET name = :n, body = :body, enabled = :e, author = :a, updated_at = NOW()
                                    WHERE skill_id = :s AND bot_id = :b"""),
                            {"n": name, "body": body, "e": enabled, "a": author, "s": skill_id, "b": bot_id}).rowcount
        if not done:
            raise KeyError(skill_id)
    else:
        skill_id = new_id("skl")
        conn.execute(text("""INSERT INTO bots.skills(skill_id, bot_id, name, body, enabled, author)
                             VALUES (:s, :b, :n, :body, :e, :a)"""),
                     {"s": skill_id, "b": bot_id, "n": name, "body": body, "e": enabled, "a": author})
    return dict(conn.execute(text("SELECT * FROM bots.skills WHERE skill_id = :s"), {"s": skill_id}).mappings().one())


def delete_skill(conn: Connection, bot_id: str, skill_id: str) -> None:
    conn.execute(text("DELETE FROM bots.skills WHERE skill_id = :s AND bot_id = :b"), {"s": skill_id, "b": bot_id})
