"""Phase 7 scoped search, diagnostics, replay metadata, and approval APIs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from seleric_swarm.api.ready import effective_capabilities
from seleric_swarm.conversations.contracts import (
    ApprovalRequest,
    ApprovalStatus,
    Principal,
    SearchResult,
)
from seleric_swarm.conversations.phase7 import (
    ApprovalExpiryService,
    transition_approval,
)
from seleric_swarm.conversations.repositories import ConversationRepositories
from seleric_swarm.observability.tracing import langfuse_trace_url, operation_span
from seleric_swarm.runtime import SwarmRuntime

router = APIRouter(prefix="/v1", tags=["phase7"])


def _runtime(request: Request) -> SwarmRuntime:
    provider = getattr(request.app.state, "runtime_provider", None)
    runtime = provider() if callable(provider) else provider
    if not isinstance(runtime, SwarmRuntime):
        raise HTTPException(status_code=503, detail="runtime unavailable")
    return runtime


def _repositories(request: Request) -> ConversationRepositories:
    repositories = _runtime(request).conversations
    if repositories is None:
        raise HTTPException(status_code=503, detail="conversation persistence unavailable")
    return repositories


def _principal(request: Request) -> Principal:
    principal = getattr(request.state, "principal", None)
    if not isinstance(principal, Principal) or not principal.authenticated:
        raise HTTPException(status_code=401, detail="authentication required")
    return principal


def _admin(request: Request) -> Principal:
    principal = _principal(request)
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="admin role required")
    return principal


def _approval_for_principal(
    repository: Any,
    approval_id: str,
    principal: Principal,
) -> ApprovalRequest | None:
    approval = repository.get(approval_id, principal.workspace_id, principal.user_id)
    if approval is not None:
        return approval
    approval = repository.get_for_workspace(approval_id, principal.workspace_id)
    if approval is None:
        return None
    roles = {role.lower() for role in principal.roles}
    if approval.required_role.lower() not in roles and not principal.is_admin:
        return None
    return approval


@router.get("/search", response_model=list[SearchResult])
def search(
    request: Request,
    q: str = Query(min_length=1, max_length=500),
    kinds: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
) -> list[SearchResult]:
    principal = _principal(request)
    selected = {item.strip() for item in kinds.split(",") if item.strip()} if kinds else None
    valid = {"thread", "message", "memory", "artifact", "report", "run"}
    if selected and not selected <= valid:
        raise HTTPException(status_code=400, detail="invalid search kind")
    repositories = _repositories(request)
    with operation_span(
        "http",
        "search",
        {"workspace_id": principal.workspace_id, "kinds": sorted(selected or valid)},
    ):
        return repositories.search.search(
            q, principal.workspace_id, principal.user_id, kinds=selected, limit=limit
        )


class CreateApprovalBody(BaseModel):
    action_type: str = Field(min_length=1, max_length=200)
    action_preview: dict[str, Any]
    required_role: str = Field(min_length=1, max_length=100)
    idempotency_key: str = Field(min_length=1, max_length=200)
    run_id: str | None = None
    dry_run: bool = True
    checkpoint_resume_token: str | None = None
    expires_in_seconds: int = Field(default=3600, ge=1, le=604800)


@router.post("/approvals", response_model=ApprovalRequest, status_code=status.HTTP_201_CREATED)
def create_approval(body: CreateApprovalBody, request: Request) -> ApprovalRequest:
    principal = _principal(request)
    repositories = _repositories(request)
    if body.run_id:
        run = repositories.runs.get(body.run_id)
        if run is None or not principal.owns(
            workspace_id=run.workspace_id, user_id=run.requested_by_user_id
        ):
            raise HTTPException(status_code=404, detail="run not found")
    now = datetime.now(UTC)
    approval = ApprovalRequest(
        workspace_id=principal.workspace_id,
        owner_user_id=principal.user_id,
        run_id=body.run_id,
        action_type=body.action_type,
        action_preview=body.action_preview,
        required_role=body.required_role,
        idempotency_key=body.idempotency_key,
        dry_run=body.dry_run,
        checkpoint_resume_token=body.checkpoint_resume_token,
        expires_at=now + timedelta(seconds=body.expires_in_seconds),
    )
    with operation_span(
        "persistence",
        "approval_request",
        {"action_type": body.action_type, "workspace_id": principal.workspace_id},
    ):
        return repositories.approvals.create(approval)


class ApprovalDecisionBody(BaseModel):
    decision: Literal["APPROVED", "REJECTED", "CANCELLED", "EXECUTED", "ROLLED_BACK"]
    reason: str | None = Field(default=None, max_length=1000)


@router.get("/approvals/{approval_id}")
def get_approval(approval_id: str, request: Request) -> dict[str, Any]:
    principal = _principal(request)
    repository = _repositories(request).approvals
    ApprovalExpiryService(repository).sweep()
    approval = _approval_for_principal(repository, approval_id, principal)
    if approval is None:
        raise HTTPException(status_code=404, detail="approval not found")
    return {
        "approval": approval,
        "events": repository.list_events(approval.id),
    }


@router.post("/approvals/{approval_id}/decision", response_model=ApprovalRequest)
def decide_approval(
    approval_id: str, body: ApprovalDecisionBody, request: Request
) -> ApprovalRequest:
    principal = _principal(request)
    runtime = _runtime(request)
    repository = _repositories(request).approvals
    ApprovalExpiryService(repository).sweep()
    approval = _approval_for_principal(repository, approval_id, principal)
    if approval is None:
        raise HTTPException(status_code=404, detail="approval not found")
    target = ApprovalStatus(body.decision)
    try:
        if target is ApprovalStatus.EXECUTED:
            if runtime.action_execution is None:
                raise LookupError("action execution service unavailable")
            updated, _outcome = runtime.action_execution.execute(
                approval,
                principal,
                allow_write_actions=runtime.settings.allow_write_actions,
            )
            return updated
        if target is ApprovalStatus.ROLLED_BACK:
            if runtime.action_execution is None:
                raise LookupError("action execution service unavailable")
            updated, _record = runtime.action_execution.rollback(approval, principal)
            return updated
        updated = transition_approval(
            repository,
            approval,
            target,
            principal,
            reason=body.reason,
            allow_write_actions=runtime.settings.allow_write_actions,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return updated


@router.get("/admin/runs/{run_id}/diagnostics")
def run_diagnostics(run_id: str, request: Request) -> dict[str, Any]:
    principal = _admin(request)
    runtime = _runtime(request)
    repositories = _repositories(request)
    run = repositories.runs.get(run_id)
    if run is None or not principal.can_access_workspace(run.workspace_id):
        raise HTTPException(status_code=404, detail="run not found")
    trace = run.metadata.get("trace", {}) if isinstance(run.metadata, dict) else {}
    trace_id = trace.get("trace_id")
    trace_url = trace.get("url") or langfuse_trace_url(
        runtime.settings.langfuse_base_url,
        runtime.settings.langfuse_project_id,
        trace_id,
    )
    memory_items: Any = repositories.memories.list_used_by_run(
        run.id, run.workspace_id, run.requested_by_user_id
    )
    return {
        "run": run,
        "replay": {
            "dry_run": True,
            "read_only": True,
            "mission_id": run.mission_id,
            "checkpoint_resume_token": run.metadata.get("checkpoint_resume_token"),
        },
        "versions": {
            "workflow": runtime.settings.workflow_version,
            "model": runtime.settings.primary_model(),
            "prompt": run.metadata.get("prompt_version"),
            "tool": run.metadata.get("tool_version"),
        },
        "telemetry": {
            "trace_id": trace_id,
            "lost_spans": run.metadata.get("telemetry_lost", 0),
            "trace_url": trace_url,
            "langfuse_configured": bool(
                runtime.settings.langfuse_project_id and runtime.settings.langfuse_otel_endpoint
            ),
        },
        "capabilities": effective_capabilities(runtime),
        "memory_provenance": [item.model_dump(mode="json") for item in memory_items],
        "retention": {
            "status": run.metadata.get("retention_status", "active"),
            "deletion_status": run.metadata.get("deletion_status", "not_requested"),
        },
    }


@router.get("/admin/capabilities")
def backend_capabilities(request: Request) -> dict[str, bool]:
    _admin(request)
    runtime = _runtime(request)
    return effective_capabilities(runtime)


@router.post("/admin/approvals/expire")
def expire_approvals(request: Request) -> dict[str, int]:
    _admin(request)
    repository = _repositories(request).approvals
    return {"expired": ApprovalExpiryService(repository).sweep()}


@router.post("/admin/runs/{run_id}/replay")
def replay_metadata(run_id: str, request: Request, dry_run: bool = True) -> dict[str, Any]:
    _admin(request)
    if not dry_run:
        raise HTTPException(
            status_code=409,
            detail="replay execution is disabled; inspect dry-run metadata first",
        )
    diagnostics = run_diagnostics(run_id, request)
    return {"accepted": False, "dry_run": True, "read_only": True, **diagnostics["replay"]}
