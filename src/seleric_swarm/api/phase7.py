"""Scoped search, run diagnostics and replay metadata APIs."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from seleric_swarm.api.ready import effective_capabilities
from seleric_swarm.conversations.contracts import Principal, SearchResult
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
                runtime.settings.is_langfuse_configured
                or (runtime.settings.langfuse_project_id and runtime.settings.langfuse_otel_endpoint)
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
