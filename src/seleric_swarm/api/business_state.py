"""Fast-path business-state endpoint (business_state_ready_store.md Phase 3).

``POST /v1/business-state`` answers high-level "How's the business?" questions
from the pre-computed ready-store snapshot in one LLM call, bypassing the agent
loop entirely. When the snapshot is missing or stale (> freshness SLA) it
silently falls back to the full agent loop so the caller always gets an answer.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from seleric_swarm.agent.runner import run_v3_mission
from seleric_swarm.api.mission_access import request_principal
from seleric_swarm.runtime import SwarmRuntime
from seleric_swarm.services.business_state.formatter import format_business_state, is_stale
from seleric_swarm.services.domain_health.scheduler import HEADLINE_DOMAIN
from seleric_swarm.services.domain_health.snapshot_store import SnapshotStore

router = APIRouter()


def _runtime(request: Request) -> SwarmRuntime:
    provider = getattr(request.app.state, "runtime_provider", None)
    if not callable(provider):
        raise TypeError("runtime provider is not configured")
    return provider()


class BusinessStateRequest(BaseModel):
    question: str = Field(..., json_schema_extra={"examples": ["How is the business doing?"]})
    # Which ready-store snapshot to read. Defaults to the cross-domain headline
    # snapshot; pass a single domain (e.g. "finance") for a domain-scoped answer.
    domain: str = HEADLINE_DOMAIN
    timezone: str = "Asia/Kolkata"


async def _fallback_to_agent_loop(
    runtime: Any, *, question: str, timezone: str, request: Request
) -> dict[str, Any]:
    principal = request_principal(request)
    request_id = str(getattr(request.state, "request_id", None) or uuid4().hex)
    session_id = uuid4().hex
    dispatched = await run_v3_mission(
        runtime,
        query=question,
        timezone=timezone,
        as_of=None,
        session_id=session_id,
        request_id=request_id,
        workspace_id=principal.workspace_id,
        owner_user_id=principal.user_id,
        thread_id=session_id,
        run_id=request_id,
    )
    result = dispatched.get("result", {}) if isinstance(dispatched, dict) else {}
    return {
        "answer": result.get("final_response"),
        "source": "agent_loop",
        "as_of": None,
        "freshness": None,
    }


@router.post("/v1/business-state")
async def business_state(req: BusinessStateRequest, request: Request) -> dict[str, Any]:
    runtime = _runtime(request)
    question = (req.question or "").strip()
    if not question or question.lower() == "string":
        raise HTTPException(status_code=400, detail="question must be a non-empty string")

    snapshot = await SnapshotStore().aget_latest(req.domain)
    if snapshot is None or snapshot.status == "UNAVAILABLE" or is_stale(snapshot):
        return await _fallback_to_agent_loop(
            runtime, question=question, timezone=req.timezone, request=request
        )

    request_id = str(getattr(request.state, "request_id", None) or uuid4().hex)
    answer = await format_business_state(
        runtime, question=question, snapshot=snapshot, request_id=request_id
    )
    return {
        "answer": answer,
        "source": "snapshot",
        "as_of": snapshot.as_of,
        "freshness": snapshot.status,
    }
