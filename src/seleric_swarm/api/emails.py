"""The email preview card's endpoints: read a draft's status, approve the version shown and send it."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from seleric_swarm.conversations.contracts import Principal
from seleric_swarm.mail.send import NotApproved, Record, StaleApproval
from seleric_swarm.mail.session import MailRuntime, get_mail_runtime

router = APIRouter(prefix="/v1/emails", tags=["emails"])


class ApproveRequest(BaseModel):
    version: int


def _principal(request: Request) -> Principal:
    principal = getattr(request.state, "principal", None)
    if not isinstance(principal, Principal) or not principal.authenticated:
        raise HTTPException(status_code=401, detail="authentication required")
    return principal


def _runtime() -> MailRuntime:
    runtime = get_mail_runtime()
    if runtime is None:
        raise HTTPException(status_code=503, detail="email is not set up on this server")
    return runtime


def _mine(runtime: MailRuntime, draft_id: str, principal: Principal) -> Record:
    try:
        rec = runtime.service.get(draft_id)
    except KeyError:
        rec = None
    if rec is None or rec.meta.get("user_id") != principal.user_id or rec.meta.get("workspace_id") != principal.workspace_id:
        raise HTTPException(status_code=404, detail="draft not found or expired; ask the agent to draft it again")
    return rec


def _view(rec: Record) -> dict[str, Any]:
    return {"approval_id": rec.id, "version": rec.version, "status": rec.status.value, "error": rec.error,
            "provider_message_id": rec.provider_message_id, "to": list(rec.draft.to), "subject": rec.draft.subject}


@router.get("/{draft_id}")
def email_status(draft_id: str, request: Request) -> dict[str, Any]:
    return _view(_mine(_runtime(), draft_id, _principal(request)))


@router.post("/{draft_id}/approve")
async def approve_and_send(draft_id: str, body: ApproveRequest, request: Request) -> dict[str, Any]:
    runtime = _runtime()
    rec = _mine(runtime, draft_id, _principal(request))
    if rec.status.value in ("sending", "submitted", "unknown"):  # a second click or a retry: report, never resend
        return _view(rec)
    try:
        runtime.service.approve(draft_id, body.version)
        done = await runtime.service.send(draft_id)
    except StaleApproval as exc:
        raise HTTPException(status_code=409, detail=f"This preview is out of date ({exc}). Review the latest draft.") from exc
    except NotApproved as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _view(done)
