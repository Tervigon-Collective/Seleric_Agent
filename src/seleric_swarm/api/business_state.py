"""Business-state endpoint.

``POST /v1/business-state`` answers high-level "How's the business?" questions by running the agent loop. The loop
reads the pre-computed ready-store snapshot through its ``business_snapshot`` tool when the question calls for it
and queries the metrics itself otherwise, so a missing or stale snapshot never leaves the caller without an answer.
``domain`` is accepted for compatibility; the loop's snapshot tool serves the headline ("business") domain.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from seleric_swarm.agent.runner import run_v3_mission
from seleric_swarm.api.mission_access import request_principal
from seleric_swarm.runtime import SwarmRuntime
from seleric_swarm.services.domain_health.scheduler import HEADLINE_DOMAIN

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


async def _answer_with_agent_loop(
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
    question = (req.question or "").strip()
    if not question or question.lower() == "string":
        raise HTTPException(status_code=400, detail="question must be a non-empty string")
    return await _answer_with_agent_loop(_runtime(request), question=question, timezone=req.timezone, request=request)
