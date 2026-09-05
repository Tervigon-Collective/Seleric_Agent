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
    scope: dict[str, Any] = Field(
        default_factory=dict,
        json_schema_extra={"examples": [{"timezone": "Asia/Kolkata", "as_of": "2026-09-03"}]},
    )
    mode: str = "read_only"
    session_id: str | None = None
    # When the query is diagnostic / predictive / prescriptive it is routed to the
    # dynamic two-axis swarm. These switch in the full agent subsystems
    # (agents/diagnostic, agents/prediction, agents/skeptic) instead of the
    # lightweight in-loop specialists. Lookup / comparison queries ignore them.
    # When True they ALSO ensure the matching intent is present so the specialist
    # actually runs (e.g. full_prediction on a "why" query still forecasts).
    full_diagnostic: bool = True
    full_prediction: bool = True
    full_skeptic: bool = True
    scenario_id: str = "cac_regression"
    # fixture = offline synthetic providers.
    # staging/production = live Seleric MCP (catalogue + metrics_query), fixture fallback.
    execution_mode: str = "production"

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "query": "Why has CAC increased over the last three days?",
                    "scope": {"timezone": "Asia/Kolkata", "as_of": "2026-09-03"},
                    "mode": "read_only",
                    "execution_mode": "production",
                    "full_diagnostic": True,
                    "full_prediction": True,
                    "full_skeptic": True,
                    "scenario_id": "cac_regression",
                }
            ]
        }
    }


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
        "mission_get": "GET /v1/missions/{mission_id}",
        "mission_events": "GET /v1/missions/{mission_id}/events",
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
    if req.execution_mode not in {"fixture", "staging", "production"}:
        raise HTTPException(
            status_code=400,
            detail="execution_mode must be one of: fixture, staging, production",
        )
    query = (req.query or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="query must be a non-empty string")
    if req.session_id is not None and req.session_id.strip() in {"", "string"}:
        # Swagger placeholder "string" should not pollute mission metadata
        req.session_id = None
    try:
        dispatched = await run_any_mission(
            runtime,
            query=query,
            timezone=str(req.scope.get("timezone") or "Asia/Kolkata"),
            as_of=req.scope.get("as_of") or req.scope.get("asOf"),
            session_id=req.session_id,
            full_diagnostic=req.full_diagnostic,
            full_prediction=req.full_prediction,
            full_skeptic=req.full_skeptic,
            scenario_id=req.scenario_id,
            execution_mode=req.execution_mode,
        )
    except Exception as exc:
        from seleric_swarm.swarm.providers.errors import ScenarioNotFoundError

        if isinstance(exc, ScenarioNotFoundError):
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        raise
    # Flatten: a consistent top-level mission object with a `route` marker.
    # lookup  -> MissionResult fields; swarm -> SwarmMissionResult fields.
    return {"route": dispatched["route"], **dispatched["result"]}


@app.get("/v1/missions/{mission_id}")
def get_mission(mission_id: str) -> dict[str, Any]:
    runtime = get_runtime()
    # a swarm mission stores its full dict under raw state; prefer it
    raw = getattr(runtime.store, "get_raw", lambda _mid: None)(mission_id)
    if isinstance(raw, dict) and raw.get("route") == "swarm":
        return raw
    result = runtime.store.get(mission_id)
    if result is None:
        raise HTTPException(status_code=404, detail="mission not found")
    return result.model_dump()


@app.get("/v1/missions/{mission_id}/events")
def get_mission_events(
    mission_id: str,
    family: str | None = None,
    after_seq: int = 0,
    limit: int = 200,
) -> dict[str, Any]:
    """Return structured control-plane events for a persisted mission."""
    runtime = get_runtime()
    store = runtime.store
    exists = store.get(mission_id) is not None or getattr(store, "get_raw", lambda _m: None)(mission_id)
    if not exists:
        raise HTTPException(status_code=404, detail="mission not found")
    list_events = getattr(store, "list_events", None)
    if list_events is None:
        from seleric_swarm.persistence.memory import extract_events, filter_events

        events = filter_events(
            extract_events(getattr(store, "get_raw", lambda _m: None)(mission_id)),
            family=family,
            after_seq=after_seq,
            limit=limit,
        )
    else:
        events = list_events(mission_id, family=family, after_seq=after_seq, limit=limit)
    return {
        "mission_id": mission_id,
        "count": len(events),
        "family": family,
        "after_seq": after_seq,
        "events": events,
    }


def serve() -> None:
    """Console entrypoint used by `seleric-api` after an editable install."""
    import os

    import uvicorn

    from seleric_swarm.config.settings import get_settings

    settings = get_settings()
    host = settings.api_host or os.environ.get("API_HOST") or ""
    port = settings.api_port or int(os.environ.get("API_PORT") or "0")
    if not host or not port:
        raise SystemExit("API_HOST and API_PORT must be set in the environment (or .env)")
    uvicorn.run("seleric_swarm.main:app", host=host, port=port, reload=settings.is_dev_surface())
