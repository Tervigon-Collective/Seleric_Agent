"""D-257: tools a bot gets when a person talks to it, so "every Monday 9:00 email me the weekly report in this format"
sets itself up. Only for chat runs and manual runs with a question (a person is there); scheduled, signal, watch and
webhook runs never get them, so a run cannot rewrite its own schedule. The bot id is fixed when the tools are built,
never taken from the model. Every change goes through ``store`` (same checks as the API) and leaves an activity row
(kind ``note``, tool ``bot_settings``) so the run's timeline shows exactly what changed.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

from pydantic_ai import RunContext

from seleric_swarm.agent.dependencies import SelericDeps
from seleric_swarm.services.bots import hindsight, store

TOOL = "bot_settings"
_log = logging.getLogger("seleric.bots.settings_tools")


def gets_settings_tools(run: dict[str, Any]) -> bool:
    return run["trigger_kind"] == "chat" or (run["trigger_kind"] == "manual" and bool(str(run.get("query") or "").strip()))


def schedule_view(t: dict[str, Any]) -> dict[str, Any]:
    c = t["config"]
    return {"schedule_id": t["trigger_id"], "title": c.get("title") or "", "cron": c["cron"], "timezone": c["timezone"],
            "task": c.get("task") or "(does the bot's goal)", "email_to": c.get("email") or "(the bot's recipients)",
            "format_document": (c.get("format_doc") or {}).get("name"), "enabled": t["enabled"],
            "next_run_at": str(t["next_run_at"] or "")}


async def knowledge(mem: hindsight.Memory | None, bot: dict[str, Any]) -> list[dict[str, str]]:
    """Uploaded documents of the bot's own and its brand's shared bank (best-effort: [] when Hindsight is down)."""
    if mem is None:
        return []
    out = []
    for scope, bank in (("this bot", mem.bot_bank(bot)), ("shared", mem.shared_bank(bot))):
        try:
            docs = await mem.documents(bank)
        except Exception as exc:  # Hindsight down: the run goes on without the list
            _log.warning("bots_documents_failed bank=%s %s", bank, type(exc).__name__)
            continue
        out += [{"document_id": d["id"], "name": hindsight.document_name(d.get("tags") or [], d["id"]), "scope": scope,
                 "bank": bank} for d in docs]
    return out


def build(bot_id: str, run_id: str, *, mem: hindsight.Memory | None,
          engine: Callable[[], Any] = store.engine) -> list[Any]:
    actor = f"bot:{bot_id}"

    def _log(summary: str, detail: dict[str, Any]) -> None:
        with engine().begin() as conn:
            store.add_activity(conn, run_id, bot_id, kind="note", tool=TOOL, outcome="completed", summary=summary,
                               detail=detail)

    def _bot() -> dict[str, Any]:
        with engine().begin() as conn:
            return store.get_bot(conn, bot_id)

    def _err(exc: Exception) -> str:
        return json.dumps({"ok": False, "error": str(exc) or type(exc).__name__})

    async def bot_settings(ctx: RunContext[SelericDeps]) -> str:
        """Your own settings: goal, skills, schedules (with ids) and the documents in your knowledge (with ids). Read
        this before changing a schedule or picking a format document."""
        bot = await asyncio.to_thread(_bot)
        return json.dumps({
            "goal": bot["instructions"], "skills": [{"name": k["name"], "enabled": k["enabled"]} for k in bot["skills"]],
            "schedules": [schedule_view(t) for t in bot["triggers"] if t["kind"] == "cron"],
            "other_triggers": [t["kind"] for t in bot["triggers"] if t["kind"] != "cron"],
            "default_email_to": (bot.get("notify") or {}).get("email") or [],
            "documents": await knowledge(mem, bot)}, default=str)

    async def save_schedule(ctx: RunContext[SelericDeps], cron: str, task: str, title: str = "",
                            timezone: str = "Asia/Kolkata", email_to: list[str] | None = None,
                            format_document_id: str = "", schedule_id: str = "") -> str:
        """Create a schedule, or change one (pass its schedule_id), when the operator asks you to do something at set
        times. cron: 5-field cron read in ``timezone`` (every Monday 09:00 is "0 9 * * 1"). task: what to do each
        time, in the operator's words. title: a short name. email_to: contact names or addresses to email (empty:
        the bot's own recipients). format_document_id: an uploaded document whose layout the email must follow
        (from bot_settings). Each scheduled run has your goal, skills, documents and memory."""
        bot = await asyncio.to_thread(_bot)
        config: dict[str, Any] = {"cron": cron, "timezone": timezone, "title": title, "task": task,
                                  "email": email_to or []}
        if format_document_id:
            doc = next((d for d in await knowledge(mem, bot) if d["document_id"] == format_document_id), None)
            if doc is None:
                return _err(ValueError(f"no document {format_document_id!r} in your knowledge; see bot_settings"))
            config["format_doc"] = {"bank": doc["bank"], "document_id": doc["document_id"], "name": doc["name"]}

        def write() -> dict[str, Any]:
            with engine().begin() as conn:
                if schedule_id:
                    old = next((t for t in store.list_triggers(conn, bot_id)
                                if t["trigger_id"] == schedule_id and t["kind"] == "cron"), None)
                    if old is None:
                        raise store.BotError(f"no schedule {schedule_id!r} on this bot")
                    # what the model left empty keeps its current value (format, recipients, title)
                    return store.update_trigger(conn, bot_id, schedule_id, {**old["config"], **{k: v for k, v in config.items() if v}})
                return store.add_trigger(conn, bot_id, kind="cron", config=config)

        try:
            saved = await asyncio.to_thread(write)
        except (store.BotError, KeyError) as exc:
            return _err(exc)
        view = schedule_view(saved)
        await asyncio.to_thread(_log, f"{'Changed' if schedule_id else 'Added'} schedule "
                                f"'{view['title'] or view['task'][:60]}' ({view['cron']} {view['timezone']})", view)
        return json.dumps({"ok": True, "saved": view}, default=str)

    async def pause_schedule(ctx: RunContext[SelericDeps], schedule_id: str, paused: bool = True) -> str:
        """Pause a schedule (paused=true) or start it again (paused=false), when the operator asks."""
        def write() -> None:
            with engine().begin() as conn:
                store.set_trigger_enabled(conn, bot_id, schedule_id, not paused)
        try:
            await asyncio.to_thread(write)
        except KeyError as exc:
            return _err(KeyError(f"no schedule {exc}"))
        await asyncio.to_thread(_log, f"{'Paused' if paused else 'Resumed'} schedule {schedule_id}", {"schedule_id": schedule_id})
        return json.dumps({"ok": True, "schedule_id": schedule_id, "paused": paused})

    async def delete_schedule(ctx: RunContext[SelericDeps], schedule_id: str) -> str:
        """Delete a schedule for good, when the operator asks."""
        def write() -> bool:
            with engine().begin() as conn:
                if not any(t["trigger_id"] == schedule_id and t["kind"] == "cron" for t in store.list_triggers(conn, bot_id)):
                    return False
                store.delete_trigger(conn, bot_id, schedule_id)
                return True
        if not await asyncio.to_thread(write):
            return _err(KeyError(f"no schedule {schedule_id!r} on this bot"))
        await asyncio.to_thread(_log, f"Deleted schedule {schedule_id}", {"schedule_id": schedule_id})
        return json.dumps({"ok": True, "deleted": schedule_id})

    async def set_goal(ctx: RunContext[SelericDeps], goal: str) -> str:
        """Replace your goal (what you are for, 1-3 plain sentences) when the operator asks. The old goal is kept as
        an earlier version."""
        goal = goal.strip()
        if not goal:
            return _err(ValueError("the goal is empty"))

        def write() -> dict[str, Any]:
            with engine().begin() as conn:
                bot = store.get_bot(conn, bot_id)
                return store.update_bot(conn, bot_id, expected_version=bot["version"], actor=actor, changes={"instructions": goal})
        try:
            bot = await asyncio.to_thread(write)
        except (store.BotError, KeyError) as exc:
            return _err(exc)
        await asyncio.to_thread(_log, f"Changed the goal (version {bot['instructions_version']})", {"goal": goal})
        return json.dumps({"ok": True, "goal_version": bot["instructions_version"]})

    return [bot_settings, save_schedule, pause_schedule, delete_schedule, set_goal]
