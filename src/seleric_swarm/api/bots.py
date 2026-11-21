"""Bots API (D-244 Phase 1, read-only bots).

``GET    /v1/bots``                               bots with last run and next scheduled run
``GET    /v1/bots/templates``                     starting templates (config/bots/*.yaml) + the tools a bot may add
``POST   /v1/bots``                               create from a template
``GET    /v1/bots/{id}``                          one bot with its triggers
``PATCH  /v1/bots/{id}``                          name / role / enabled / instructions / tools / limits (needs expected_version)
``POST   /v1/bots/{id}/triggers``                 add a cron or signal trigger
``PATCH  /v1/bots/{id}/triggers/{tid}``           enable / disable
``DELETE /v1/bots/{id}/triggers/{tid}``
``POST   /v1/bots/{id}/runs``                     run now (manual) or ask it something (chat)
``GET    /v1/bots/{id}/runs``                     recent runs (``?kind=chat`` for the conversation)
``GET    /v1/bots/{id}/activity``                 the right-sidebar timeline

Under ``/v1`` so the API-key middleware applies; every route needs an authenticated principal (as Monitoring).
Runs execute in the ``bots-cron`` worker, not in this process.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from seleric_swarm.api.mission_access import request_principal
from seleric_swarm.conversations.contracts import Principal
from seleric_swarm.services.bots import store

router = APIRouter(prefix="/v1/bots")


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


class NewTrigger(BaseModel):
    kind: Literal["cron", "signal"]
    config: dict[str, Any]
    enabled: bool = True


class TriggerState(BaseModel):
    enabled: bool


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
            store.set_trigger_enabled(conn, bot_id, trigger_id, body.enabled)
            return {"triggers": store.list_triggers(conn, bot_id)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="trigger not found") from exc


@router.delete("/{bot_id}/triggers/{trigger_id}", status_code=204)
def delete_trigger(bot_id: str, trigger_id: str, _: Principal = Depends(request_principal)) -> None:
    with store.engine().begin() as conn:
        store.delete_trigger(conn, bot_id, trigger_id)


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
