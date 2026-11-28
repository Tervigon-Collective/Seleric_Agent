"""Bots API (D-244 Phase 1, read-only bots).

``GET    /v1/bots``                               bots with last run and next scheduled run
``GET    /v1/bots/templates``                     starting templates (config/bots/*.yaml) + the tools a bot may add
``POST   /v1/bots``                               create from a template
``GET    /v1/bots/{id}``                          one bot with its triggers
``PATCH  /v1/bots/{id}``                          name / role / enabled / instructions / tools / limits (needs expected_version)
``DELETE /v1/bots/{id}``                          the bot with its triggers, runs, activity and skills (refused mid-run)
``POST   /v1/bots/{id}/triggers``                 add a cron / signal / watch / webhook trigger (a webhook's secret is in this reply only)
``PATCH  /v1/bots/{id}/triggers/{tid}``           enable / disable, and/or new settings (D-257: schedules are editable)
``DELETE /v1/bots/{id}/triggers/{tid}``
``POST   /v1/bots/{id}/triggers/{tid}/run``       run this schedule now (its task, layout, recipients; not limited)
``POST   /v1/bots/{id}/runs``                     run now (manual) or ask it something (chat)
``GET    /v1/bots/{id}/runs``                     recent runs (``?kind=chat`` for the conversation)
``GET    /v1/bots/inbox?days=1``                  every bot's non-chat runs since local midnight (D-253 inbox)
``POST   /v1/bots/runs/{run_id}/dismiss``         a person dismisses a run from "Needs you" (``{"undo": true}`` brings it back)
``GET    /v1/bots/{id}/activity``                 the right-sidebar timeline
``PUT    /v1/bots/{id}/skills``                   create (no skill_id) or replace a skill; ``DELETE .../skills/{sid}``
``GET    /v1/bots/{id}/memory?bank=bot|shared``  what Hindsight holds (``q``, ``offset``); ``POST`` adds a note
``DELETE /v1/bots/{id}/memory/{document_id}``    forget a run's learning or a note, with every fact taken from it
``GET    /v1/bots/skills/library``               skills a bot can turn on (config/bots/skills/*.yaml)
``POST   /v1/bots/{id}/skills/library/{key}``    turn a library skill on for this bot (follows the library until edited)
``POST   /v1/bots/{id}/skills/{sid}/library``    drop this bot's edits: the skill follows the library again (D-260)
``POST   /v1/bots/{id}/knowledge?name=&shared=`` upload one document (raw body) to Hindsight for this bot or the brand
``GET    /v1/bots/{id}/knowledge``               uploaded documents of the bot's and the brand's bank, with status
``DELETE /v1/bots/{id}/knowledge/{document_id}?shared=``  remove one from Hindsight
``POST   /hooks/bots/{trigger_id}``               webhook trigger: ``X-Bot-Secret`` header, body = data for the run (64 KB max)

Under ``/v1`` so the API-key middleware applies; every route needs an authenticated principal (as Monitoring).
The webhook route is outside ``/v1`` (callers have no API key): its per-trigger secret is the only credential.
Runs execute in the ``bots-cron`` worker, not in this process.
"""

from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import tempfile
from hmac import compare_digest
from pathlib import Path, PurePath
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from seleric_swarm.api.mission_access import request_principal
from seleric_swarm.config.settings import get_settings
from seleric_swarm.conversations.blobs import ClamAVMalwareScanner, MalwareScanStatus
from seleric_swarm.conversations.contracts import Principal
from seleric_swarm.services.bots import hindsight, schedule, store

router = APIRouter(prefix="/v1/bots")
hooks_router = APIRouter(prefix="/hooks/bots")
WEBHOOK_MAX_BYTES = 64 * 1024
DOC_MAX_BYTES = 20 * 1024 * 1024
# What Hindsight can read (its file converters, OCR for images). Checked by extension; ClamAV scans the bytes.
DOC_TYPES = frozenset({".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".csv", ".txt", ".md", ".json",
                       ".yaml", ".yml", ".html", ".htm", ".png", ".jpg", ".jpeg"})


def _bad(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail="bot not found")
    return HTTPException(status_code=409 if isinstance(exc, store.VersionConflict) else 400, detail=str(exc))


class CreateBot(BaseModel):
    template: str
    name: str | None = Field(default=None, max_length=80)


class UpdateBot(BaseModel):
    expected_version: int
    name: str | None = Field(default=None, min_length=1, max_length=80)
    role: str | None = Field(default=None, max_length=120)
    enabled: bool | None = None
    instructions: str | None = Field(default=None, max_length=20000)
    tools: list[str] | None = None
    limits: dict[str, int] | None = None
    notify: dict[str, list[str]] | None = None


class NewTrigger(BaseModel):
    kind: Literal["cron", "signal", "watch", "webhook"]
    config: dict[str, Any]
    enabled: bool = True


class TriggerState(BaseModel):
    enabled: bool | None = None
    config: dict[str, Any] | None = None


class SkillBody(BaseModel):
    skill_id: str | None = None
    name: str = Field(min_length=1, max_length=80)
    body: str = Field(min_length=1, max_length=8000)
    enabled: bool = True


class NewNote(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    bank: Literal["bot", "shared"] = "bot"


class NewRun(BaseModel):
    kind: Literal["manual", "chat"] = "manual"
    message: str = Field(default="", max_length=4000)


@router.get("/templates")
def get_templates(_: Principal = Depends(request_principal)) -> dict[str, Any]:
    return {"templates": list(store.templates().values()), "read_tools": sorted(store.READ_TOOLS)}


@router.get("")
def list_bots(_: Principal = Depends(request_principal)) -> dict[str, Any]:
    with store.engine().begin() as conn:
        return {"bots": store.list_bots(conn)}


@router.get("/inbox")
def get_inbox(days: int = 1, _: Principal = Depends(request_principal)) -> dict[str, Any]:
    with store.engine().begin() as conn:
        return {"runs": store.inbox(conn, days=min(max(days, 1), 14))}


class Dismiss(BaseModel):
    undo: bool = False


@router.post("/runs/{run_id}/dismiss")
def dismiss_run(run_id: str, body: Dismiss, who: Principal = Depends(request_principal)) -> dict[str, Any]:
    try:
        with store.engine().begin() as conn:
            return store.dismiss_run(conn, run_id, actor=who.user_id, undo=body.undo)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc


@router.post("", status_code=201)
def create_bot(body: CreateBot, who: Principal = Depends(request_principal)) -> dict[str, Any]:
    try:
        with store.engine().begin() as conn:
            return store.create_bot(conn, template=body.template, name=body.name, actor=who.user_id)
    except (store.BotError, KeyError) as exc:
        raise _bad(exc) from exc


@router.get("/{bot_id}")
def get_bot(bot_id: str, _: Principal = Depends(request_principal)) -> dict[str, Any]:
    try:
        with store.engine().begin() as conn:
            return store.get_bot(conn, bot_id)
    except KeyError as exc:
        raise _bad(exc) from exc


@router.patch("/{bot_id}")
def update_bot(bot_id: str, body: UpdateBot, who: Principal = Depends(request_principal)) -> dict[str, Any]:
    changes = body.model_dump(exclude_none=True, exclude={"expected_version"})
    try:
        with store.engine().begin() as conn:
            return store.update_bot(conn, bot_id, expected_version=body.expected_version, actor=who.user_id, changes=changes)
    except (store.BotError, KeyError) as exc:
        raise _bad(exc) from exc


@router.delete("/{bot_id}", status_code=204)
def delete_bot(bot_id: str, _: Principal = Depends(request_principal)) -> None:
    try:
        with store.engine().begin() as conn:
            store.delete_bot(conn, bot_id)
    except (store.BotError, KeyError) as exc:
        raise _bad(exc) from exc


@router.post("/{bot_id}/triggers", status_code=201)
def add_trigger(bot_id: str, body: NewTrigger, _: Principal = Depends(request_principal)) -> dict[str, Any]:
    try:
        with store.engine().begin() as conn:
            store.get_bot(conn, bot_id)
            return store.add_trigger(conn, bot_id, kind=body.kind, config=body.config, enabled=body.enabled)
    except (store.BotError, KeyError) as exc:
        raise _bad(exc) from exc


@router.patch("/{bot_id}/triggers/{trigger_id}")
def set_trigger(bot_id: str, trigger_id: str, body: TriggerState, _: Principal = Depends(request_principal)) -> dict[str, Any]:
    try:
        with store.engine().begin() as conn:
            if body.config is not None:
                store.update_trigger(conn, bot_id, trigger_id, body.config)
            if body.enabled is not None:
                store.set_trigger_enabled(conn, bot_id, trigger_id, body.enabled)
            return {"triggers": store.list_triggers(conn, bot_id)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="trigger not found") from exc
    except store.BotError as exc:
        raise _bad(exc) from exc


@router.delete("/{bot_id}/triggers/{trigger_id}", status_code=204)
def delete_trigger(bot_id: str, trigger_id: str, _: Principal = Depends(request_principal)) -> None:
    with store.engine().begin() as conn:
        store.delete_trigger(conn, bot_id, trigger_id)


@router.post("/{bot_id}/triggers/{trigger_id}/run", status_code=202)
def run_trigger(bot_id: str, trigger_id: str, who: Principal = Depends(request_principal)) -> dict[str, Any]:
    try:
        with store.engine().begin() as conn:
            bot = store.get_bot(conn, bot_id)
            trig = store.get_trigger(conn, trigger_id)
            if not trig or trig["bot_id"] != bot_id:
                raise HTTPException(status_code=404, detail="trigger not found")
            return schedule.run_now(conn, bot, trig, who=who.user_id)
    except (store.BotError, KeyError) as exc:
        raise _bad(exc) from exc


@router.post("/{bot_id}/runs", status_code=202)
def start_run(bot_id: str, body: NewRun, who: Principal = Depends(request_principal)) -> dict[str, Any]:
    if body.kind == "chat" and not body.message.strip():
        raise HTTPException(status_code=400, detail="a chat message is required")
    try:
        with store.engine().begin() as conn:
            if not store.get_bot(conn, bot_id)["enabled"]:
                raise store.BotError("this bot is paused; turn it on first")
            reason = (f"asked by {who.user_id}" if body.kind == "chat"
                      else f"run now by {who.user_id}" + (f": {body.message.strip()}" if body.message.strip() else ""))
            return store.enqueue_run(conn, bot_id, kind=body.kind, reason=reason, query=body.message.strip(),
                                     requested_by=who.user_id)
    except (store.BotError, KeyError) as exc:
        raise _bad(exc) from exc


@router.get("/{bot_id}/runs")
def get_runs(bot_id: str, kind: str | None = None, limit: int = 30, _: Principal = Depends(request_principal)) -> dict[str, Any]:
    with store.engine().begin() as conn:
        return {"runs": store.list_runs(conn, bot_id, kind=kind, limit=min(max(limit, 1), 100))}


@router.get("/{bot_id}/activity")
def get_activity(bot_id: str, limit: int = 200, _: Principal = Depends(request_principal)) -> dict[str, Any]:
    with store.engine().begin() as conn:
        return {"activity": store.list_activity(conn, bot_id, limit=min(max(limit, 1), 500))}


@router.put("/{bot_id}/skills")
def put_skill(bot_id: str, body: SkillBody, who: Principal = Depends(request_principal)) -> dict[str, Any]:
    try:
        with store.engine().begin() as conn:
            store.get_bot(conn, bot_id)
            store.save_skill(conn, bot_id, name=body.name, body=body.body, enabled=body.enabled, author=who.user_id,
                             skill_id=body.skill_id)
            return {"skills": store.list_skills(conn, bot_id)}
    except (store.BotError, KeyError) as exc:
        raise _bad(exc) from exc


@router.get("/skills/library")
def skill_library(_: Principal = Depends(request_principal)) -> dict[str, Any]:
    return {"skills": [{k: v for k, v in s.items() if k in ("key", "name", "summary", "tools", "body")}
                       for s in store.skill_library().values()]}


@router.post("/{bot_id}/skills/library/{key}")
def add_library_skill(bot_id: str, key: str, who: Principal = Depends(request_principal)) -> dict[str, Any]:
    try:
        with store.engine().begin() as conn:
            store.get_bot(conn, bot_id)
            store.add_library_skill(conn, bot_id, key, author=who.user_id)
            return {"skills": store.list_skills(conn, bot_id)}
    except (store.BotError, KeyError) as exc:
        raise _bad(exc) from exc


@router.post("/{bot_id}/skills/{skill_id}/library")
def reset_skill(bot_id: str, skill_id: str, who: Principal = Depends(request_principal)) -> dict[str, Any]:
    try:
        with store.engine().begin() as conn:
            store.reset_skill_to_library(conn, bot_id, skill_id, author=who.user_id)
            return {"skills": store.list_skills(conn, bot_id)}
    except (store.BotError, KeyError) as exc:
        raise _bad(exc) from exc


@router.delete("/{bot_id}/skills/{skill_id}", status_code=204)
def delete_skill(bot_id: str, skill_id: str, _: Principal = Depends(request_principal)) -> None:
    with store.engine().begin() as conn:
        store.delete_skill(conn, bot_id, skill_id)


def _hook(trigger_id: str, secret: str, data: str, idempotency_key: str) -> dict[str, Any] | None:
    with store.engine().begin() as conn:
        trig = store.webhook_trigger(conn, trigger_id)
        if trig is None or not trig["secret_hash"] or not compare_digest(trig["secret_hash"], store.secret_hash(secret)):
            return None
        bot = store.get_bot(conn, trig["bot_id"])
        status = schedule.fire(conn, trig, bot, kind="webhook", reason=f"webhook {trigger_id} ({len(data)} characters)",
                               dedupe_key=f"webhook:{trigger_id}:{idempotency_key}" if idempotency_key else None,
                               detail={"data": data[:4000]}, query=data)
        if status != "duplicate":
            conn.execute(text("UPDATE bots.triggers SET last_fired_at = NOW() WHERE trigger_id = :t"), {"t": trigger_id})
        return {"status": status}


@hooks_router.post("/{trigger_id}", status_code=202)
async def webhook(trigger_id: str, request: Request) -> dict[str, Any]:
    """Unknown trigger, paused bot and wrong secret all answer 404, so the URL reveals nothing without the secret."""
    secret = request.headers.get("x-bot-secret", "")
    raw = await request.body()
    if len(raw) > WEBHOOK_MAX_BYTES:
        raise HTTPException(status_code=413, detail=f"body over {WEBHOOK_MAX_BYTES} bytes")
    if not secret:
        raise HTTPException(status_code=404, detail="not found")
    done = await asyncio.to_thread(_hook, trigger_id, secret, raw.decode("utf-8", "replace"),
                                   request.headers.get("idempotency-key", "")[:200])
    if done is None:
        raise HTTPException(status_code=404, detail="not found")
    return done


def _memory_bank(bot_id: str, bank: str) -> tuple[hindsight.Memory, str]:
    mem = hindsight.memory()
    if mem is None:
        raise HTTPException(status_code=503, detail="bot memory is not set up on this server (no Hindsight key)")
    try:
        with store.engine().begin() as conn:
            bot = store.get_bot(conn, bot_id)
    except KeyError as exc:
        raise _bad(exc) from exc
    if bank not in ("bot", "shared"):
        raise HTTPException(status_code=400, detail="bank must be bot or shared")
    return mem, mem.bot_bank(bot) if bank == "bot" else mem.shared_bank(bot)


async def _hindsight(call: Any) -> Any:
    try:
        return await call
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"memory service error: {type(exc).__name__}") from exc


@router.get("/{bot_id}/memory")
async def get_memory(bot_id: str, bank: str = "bot", q: str = "", offset: int = 0,
                     _: Principal = Depends(request_principal)) -> dict[str, Any]:
    mem, bank_id = await asyncio.to_thread(_memory_bank, bot_id, bank)
    found = await _hindsight(mem.list(bank_id, limit=50, offset=max(offset, 0), q=q.strip()[:200]))
    # D-257: facts read from uploaded documents are listed under Knowledge > Documents, not as things it learned.
    # shortcut: filtered after paging, so a page can hold fewer than 50; page by tag if Hindsight's list takes one.
    found["items"] = [i for i in found.get("items") or [] if hindsight.DOC_TAG not in (i.get("tags") or [])]
    return {"bank": bank_id, **found}


@router.post("/{bot_id}/memory", status_code=202)
async def add_note(bot_id: str, body: NewNote, who: Principal = Depends(request_principal)) -> dict[str, Any]:
    mem, bank_id = await asyncio.to_thread(_memory_bank, bot_id, body.bank)
    doc = hindsight.new_note_id()
    await _hindsight(mem.retain(bank_id, content=body.text.strip(), document_id=doc, kind="note",
                                context=f"Note from {who.user_id} for Seleric bots"))
    return {"bank": bank_id, "document_id": doc}  # facts appear once Hindsight has extracted them


@router.delete("/{bot_id}/memory/{document_id}", status_code=204)
async def forget(bot_id: str, document_id: str, bank: str = "bot", _: Principal = Depends(request_principal)) -> None:
    mem, bank_id = await asyncio.to_thread(_memory_bank, bot_id, bank)
    await _hindsight(mem.forget(bank_id, document_id))



# ---------------------------------------------------------------- knowledge (D-257)

def doc_id(name: str) -> str:
    """Same file name, same id: uploading it again replaces it in that bank."""
    return "doc_" + hashlib.sha256(name.encode()).hexdigest()[:16]


def check_document(name: str, size: int) -> str:
    """The file's content type, or HTTPException for a name, type or size we do not take."""
    name = PurePath(name.replace("\\", "/")).name.strip()
    ext = PurePath(name).suffix.lower()
    if not name or ext not in DOC_TYPES:
        raise HTTPException(status_code=415, detail=f"file type not supported: {ext or name or 'no name'}")
    if size == 0:
        raise HTTPException(status_code=400, detail="the file is empty")
    if size > DOC_MAX_BYTES:
        raise HTTPException(status_code=413, detail=f"file over {DOC_MAX_BYTES // (1024 * 1024)} MB")
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


def scan(data: bytes) -> None:
    """ClamAV over the bytes (a temp file, removed right after). No scanner or not clean: refused (fail closed)."""
    s = get_settings()
    if s.malware_scanner_backend != "clamav" or not s.clamav_host.strip():
        raise HTTPException(status_code=503, detail="virus scanner is not set up on this server; uploads are off")
    with tempfile.NamedTemporaryFile() as tmp:
        tmp.write(data)
        tmp.flush()
        found = ClamAVMalwareScanner(s.clamav_host, s.clamav_port, max(s.clamav_timeout_s, 30.0)).scan(Path(tmp.name))
    if found.status is MalwareScanStatus.QUARANTINED:
        raise HTTPException(status_code=422, detail="the virus scanner flagged this file; it was not uploaded")
    if found.status is not MalwareScanStatus.CLEAN:
        raise HTTPException(status_code=503, detail=f"virus scan did not finish ({found.status.value}); try again")


async def _read_capped(request: Request) -> bytes:
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > DOC_MAX_BYTES:
            raise HTTPException(status_code=413, detail=f"file over {DOC_MAX_BYTES // (1024 * 1024)} MB")
    return bytes(data)


@router.post("/{bot_id}/knowledge", status_code=202)
async def upload_document(bot_id: str, request: Request, name: str, shared: bool = False,
                          who: Principal = Depends(request_principal)) -> dict[str, Any]:
    """One file per request, as the raw body (no multipart). Hindsight converts, chunks, embeds and indexes it."""
    mem, bank = await asyncio.to_thread(_memory_bank, bot_id, "shared" if shared else "bot")
    data = await _read_capped(request)
    name = PurePath(name.replace("\\", "/")).name.strip()[:200]
    content_type = check_document(name, len(data))
    await asyncio.to_thread(scan, data)
    document_id = doc_id(name)
    ops = await _hindsight(mem.retain_files(bank, [(name, data, content_type)], [{
        "document_id": document_id, "context": f"{name}, uploaded by {who.user_id} for Seleric bots",
        "tags": [hindsight.DOC_TAG, f"{hindsight.NAME_TAG}{name}"]}]))
    return {"bank": bank, "document_id": document_id, "name": name, "operation_id": ops[0] if ops else None,
            "status": "processing"}


def document_status(docs: list[dict[str, Any]], ops: list[dict[str, Any]], scope: str) -> list[dict[str, Any]]:
    """One row per document: ready once indexed; processing / failed from its latest operation (a file is converted,
    then retained: two operations with the same document id)."""
    latest: dict[str, dict[str, Any]] = {}
    for op in sorted(ops, key=lambda o: str(o.get("created_at") or "")):
        if op.get("document_id"):
            latest[op["document_id"]] = op
    rows = {}
    for d in docs:
        rows[d["id"]] = {"document_id": d["id"], "name": hindsight.document_name(d.get("tags") or [], d["id"]),
                         "scope": scope, "status": "ready", "updated_at": d.get("updated_at"), "error": None}
    for did, op in latest.items():
        status = str(op.get("status") or "")
        if status in ("pending", "processing"):
            state = "processing"
        elif status in ("failed", "cancelled"):
            state = "failed"
        else:
            continue  # completed: the document row says ready
        if did not in rows and not str(op.get("filename") or ""):
            continue  # a run learning or a note, not an upload
        rows[did] = {"document_id": did, "name": rows.get(did, {}).get("name") or op.get("filename") or did, "scope": scope,
                     "status": state, "updated_at": op.get("updated_at"), "error": op.get("error_message")}
    return sorted(rows.values(), key=lambda r: str(r["updated_at"] or ""), reverse=True)


@router.get("/{bot_id}/knowledge")
async def list_documents(bot_id: str, _: Principal = Depends(request_principal)) -> dict[str, Any]:
    out = []
    for scope in ("bot", "shared"):
        mem, bank = await asyncio.to_thread(_memory_bank, bot_id, scope)
        docs, ops = await asyncio.gather(_hindsight(mem.documents(bank)), _hindsight(mem.operations(bank)))
        out += [{**r, "bank": bank} for r in document_status(docs, ops, scope)]
    return {"documents": out, "storage": "Hindsight Cloud", "max_mb": DOC_MAX_BYTES // (1024 * 1024),
            "types": sorted(DOC_TYPES)}


@router.delete("/{bot_id}/knowledge/{document_id}", status_code=204)
async def delete_document(bot_id: str, document_id: str, shared: bool = False, _: Principal = Depends(request_principal)) -> None:
    mem, bank = await asyncio.to_thread(_memory_bank, bot_id, "shared" if shared else "bot")
    await _hindsight(mem.forget(bank, document_id))
