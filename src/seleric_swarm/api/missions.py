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
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from seleric_swarm.agent.agent import build_seleric_agent
from seleric_swarm.agent.dependencies import ExecutionLimits, SelericDeps
from seleric_swarm.config.settings import Settings
from seleric_swarm.conversations.contracts import ContextBundle, Principal, PrincipalAuthMethod
from seleric_swarm.state.artifacts import InMemoryArtifactStore

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
    deps = SelericDeps(
        mission_id=mission_id,
        as_of=datetime.now(UTC),
        principal=Principal(
            principal_id=uuid4().hex,
            workspace_id=req.workspace_id,
            user_id=req.user_id,
            authenticated=False,
            auth_method=PrincipalAuthMethod.ANONYMOUS,
        ),
        thread_id=uuid4().hex,
        run_id=uuid4().hex,
        trace_id=uuid4().hex,
        context=ContextBundle(),
        mcp_client=object(),  # Profile B hasn't built SelericMcpClient yet.
        artifact_store=InMemoryArtifactStore(),
        limits=ExecutionLimits(),
    )
    agent = build_seleric_agent()
    result = await agent.run(req.query, deps=deps)
    return result.output.model_dump(mode="json")
