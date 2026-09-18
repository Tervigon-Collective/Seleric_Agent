"""V3 mission endpoint — Sprint 1 scaffolding, NOT mounted into ``main.py``.

``docs/refactor/01_PROFILE_RUNTIME.md`` Sprint 1 asks for "``POST
/v1/missions`` wired to a stub agent (no real toolsets yet) behind a
feature flag, 0% traffic". The live ``/v1/missions`` in ``main.py`` already
serves 100% of production traffic through ``orchestration/dispatch.py``
(the strangler-fig rule, ``docs/refactor/00_OVERVIEW.md`` §5) and is a
large, load-bearing handler (async accept path, durable mission queue,
mission-access checks) — replacing it in place is exactly the kind of
un-gated change that rule forbids.

So "0% traffic" is implemented literally: this ``APIRouter`` exists and is
tested on its own (see ``tests/unit/test_v3_missions_stub.py``), but is not
``include_router``-ed into the running ``app`` in ``main.py``. Mounting it
(at whatever path, flag-gated or canary-split) is a deliberate later-sprint
decision, not implied by this file existing.

``settings.v3_agent_enabled`` is checked here anyway so the 501 behavior is
already correct on the day this does get mounted.

Persists to the shared V3 stores (``api/v3_state.py``) so the mission is
retrievable afterward and visible to the Office UI's V3 adapter
(``api/office/v3_adapter.py``) — an earlier version of this handler built a
throwaway ``InMemoryArtifactStore()`` per request and never stored the
``Mission`` at all, so nothing survived past the response (found 2026-09-18
while checking Sprint 1/2 for UI connectivity).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from seleric_swarm.agent.agent import build_seleric_agent
from seleric_swarm.agent.dependencies import ExecutionLimits, NullMcpClient, SelericDeps
from seleric_swarm.agent.output import MissionResult
from seleric_swarm.api.office.registry import register_mission
from seleric_swarm.api.v3_state import get_v3_artifact_store, get_v3_mission_store
from seleric_swarm.config.settings import Settings
from seleric_swarm.conversations.contracts import ContextBundle, Principal, PrincipalAuthMethod
from seleric_swarm.state.missions import Mission

router = APIRouter()


class V3MissionRequest(BaseModel):
    query: str = Field(..., min_length=1)
    workspace_id: str = "default"
    user_id: str = "default"


@router.post("/v1/missions")
async def create_mission_v3(req: V3MissionRequest) -> dict[str, Any]:
    if not Settings().v3_agent_enabled:
        raise HTTPException(status_code=501, detail="v3 agent is not enabled")

    mission_id = f"MS3-{uuid4().hex[:10]}"
    as_of = datetime.now(UTC)
    thread_id = uuid4().hex
    run_id = uuid4().hex
    mission_store = get_v3_mission_store()
    mission_store.create(
        Mission(
            mission_id=mission_id,
            query=req.query,
            as_of=as_of,
            workspace_id=req.workspace_id,
            owner_user_id=req.user_id,
            thread_id=thread_id,
            run_id=run_id,
        )
    )
    register_mission(mission_id)

    deps = SelericDeps(
        mission_id=mission_id,
        as_of=as_of,
        principal=Principal(
            principal_id=uuid4().hex,
            workspace_id=req.workspace_id,
            user_id=req.user_id,
            authenticated=False,
            auth_method=PrincipalAuthMethod.ANONYMOUS,
        ),
        thread_id=thread_id,
        run_id=run_id,
        trace_id=uuid4().hex,
        context=ContextBundle(),
        mcp_client=NullMcpClient(),
        artifact_store=get_v3_artifact_store(),
        limits=ExecutionLimits(),
    )
    agent = build_seleric_agent()
    run = await agent.run(req.query, deps=deps)
    # The stub model (agent/agent.py::_stub_test_model) returns a fixed
    # payload with a placeholder mission_id/query/as_of -- correct those
    # back to the real request context before this leaves the handler, so
    # a caller (and the Office UI, via api/v3_state.py) sees the mission id
    # it actually asked for, not the literal string "stub".
    result: MissionResult = run.output.model_copy(
        update={"mission_id": mission_id, "query": req.query, "as_of": as_of}
    )
    mission_store.finish(
        mission_id,
        status=result.status,
        final_response=result.final_response,
        error_code=result.error_code,
    )
    return result.model_dump(mode="json")
