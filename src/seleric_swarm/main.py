from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from seleric_swarm.bootstrap import build_runtime
from seleric_swarm.llm.port import ChatMessage, LLMRequest, LLMRequestMetadata
from seleric_swarm.observability.tracing import traced_span
from seleric_swarm.orchestration.dispatch import run_any_mission
from seleric_swarm.runtime import SwarmRuntime

_runtime: SwarmRuntime | None = None


def get_runtime() -> SwarmRuntime:
    global _runtime
    if _runtime is None:
        _runtime = build_runtime()
    return _runtime


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _runtime
    if _runtime is None:
        _runtime = build_runtime()
    yield


app = FastAPI(title="Seleric Intelligence Swarm", version="0.1.0", lifespan=lifespan)


class MissionRequest(BaseModel):
    query: str
    scope: dict[str, Any] = Field(default_factory=dict)
    mode: str = "read_only"
    session_id: str | None = None
    # When the query is diagnostic / predictive / prescriptive it is routed to the
    # dynamic two-axis swarm. These switch in the full agent subsystems
    # (agents/diagnostic, agents/prediction, agents/skeptic) instead of the
    # lightweight in-loop specialists. Lookup / comparison queries ignore them.
    full_diagnostic: bool = True
    full_prediction: bool = True
    full_skeptic: bool = True
    scenario_id: str = "cac_regression"


class PingRequest(BaseModel):
    message: str = "ping"


@app.get("/")
def root() -> dict[str, Any]:
    return {
        "name": "Seleric Intelligence Swarm",
        "status": "ok",
        "docs": "/docs",
        "health": "/health",
        "missions": "POST /v1/missions",
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/llm/ping")
async def llm_ping(req: PingRequest) -> dict[str, Any]:
    runtime = get_runtime()
    if not runtime.settings.is_dev_surface():
        raise HTTPException(status_code=404, detail="Not found")
    metadata = {
        "request_id": uuid4().hex,
        "session_id": "ping",
        "mission_id": "ping",
        "workflow_name": "llm_ping",
        "workflow_version": runtime.settings.workflow_version,
        "agent_name": "llm_port",
        "agent_version": "0.1.0",
        "model": runtime.settings.azure_openai_model,
    }
    with traced_span("llm.ping", metadata, runtime.settings.langsmith_tracing):
        response = await runtime.llm.complete(
            LLMRequest(
                messages=[ChatMessage(role="user", content=req.message)],
                model=runtime.settings.azure_openai_model,
                temperature=0,
                max_tokens=32,
                timeout_s=runtime.settings.llm_timeout_s,
                metadata=LLMRequestMetadata(
                    request_id=metadata["request_id"],
                    session_id="ping",
                    mission_id="ping",
                    agent_id="llm_port",
                    agent_version="0.1.0",
                    workflow_name="llm_ping",
                    workflow_version=runtime.settings.workflow_version,
                ),
                tags=["ping"],
            )
        )
    return {
        "text": response.text,
        "model": response.model,
        "latency_ms": response.latency_ms,
        "retry_count": response.retry_count,
        "usage": response.usage.model_dump(),
    }


@app.post("/v1/missions")
async def create_mission(req: MissionRequest) -> dict[str, Any]:
    runtime = get_runtime()
    if req.mode != "read_only":
        raise HTTPException(status_code=400, detail="Only read_only mode is allowed in V1")
    dispatched = await run_any_mission(
        runtime,
        query=req.query,
        timezone=str(req.scope.get("timezone") or "Asia/Kolkata"),
        as_of=req.scope.get("as_of") or req.scope.get("asOf"),
        session_id=req.session_id,
        full_diagnostic=req.full_diagnostic,
        full_prediction=req.full_prediction,
        full_skeptic=req.full_skeptic,
        scenario_id=req.scenario_id,
    )
    # Flatten: a consistent top-level mission object with a `route` marker.
    # lookup  -> MissionResult fields; swarm -> SwarmMissionResult fields.
    return {"route": dispatched["route"], **dispatched["result"]}


@app.get("/v1/missions/{mission_id}")
def get_mission(mission_id: str) -> dict[str, Any]:
    runtime = get_runtime()
    result = runtime.store.get(mission_id)
    if result is None:
        raise HTTPException(status_code=404, detail="mission not found")
    return result.model_dump()


def serve() -> None:
    """Console entrypoint used by `seleric-api` after an editable install."""
    import uvicorn

    uvicorn.run("seleric_swarm.main:app", host="127.0.0.1", port=8000, reload=True)
