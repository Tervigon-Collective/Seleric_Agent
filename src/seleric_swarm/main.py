from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field

# event families the control plane emits (without the trailing "_")
_EVENT_FAMILIES = frozenset(
    {"mission", "decomposition", "task", "artifact", "leadership", "claim", "skeptic", "remediation"}
)

from seleric_swarm.api.async_missions import (
    cancel_running_mission,
    new_mission_id,
    run_mission_job,
    seed_running_mission,
)
from seleric_swarm.api.ready import check_readiness
from seleric_swarm.api.request_id import RequestIdMiddleware
from seleric_swarm.api.security import ApiSecurityMiddleware
from seleric_swarm.bootstrap import build_runtime
from seleric_swarm.llm.port import ChatMessage, LLMRequest, LLMRequestMetadata
from seleric_swarm.observability.tracing import traced_span
from seleric_swarm.orchestration.dispatch import route_for, run_any_mission
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

# Security middleware reads settings at import/startup; rebuild runtime lazily inside.
_settings_boot = None
try:
    from seleric_swarm.config.settings import get_settings

    _settings_boot = get_settings()
except Exception:
    _settings_boot = None

# Starlette applies middleware in reverse add order: RequestId outermost.
app.add_middleware(
    ApiSecurityMiddleware,
    api_key=getattr(_settings_boot, "api_key", "") or "",
    rate_limit_per_minute=int(getattr(_settings_boot, "rate_limit_per_minute", 60) or 60),
    rate_limit_enabled=bool(getattr(_settings_boot, "rate_limit_enabled", True)),
)
app.add_middleware(RequestIdMiddleware)


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
    # fixture = offline synthetic providers (default).
    # staging/production = prefer MCPGateway for commerce/performance, fixture fallback.
    execution_mode: str = "fixture"
    # wait=true (default): run synchronously and return the finished mission.
    # wait=false: accept immediately (status=running); poll GET /v1/missions/{id}.
    wait: bool = True

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "query": "Why has CAC increased over the last three days?",
                    "scope": {"timezone": "Asia/Kolkata", "as_of": "2026-09-03"},
                    "mode": "read_only",
                    "full_diagnostic": True,
                    "full_prediction": True,
                    "full_skeptic": True,
                    "scenario_id": "cac_regression",
                    "wait": True,
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
        "readyz": "/readyz",
        "missions": "POST /v1/missions",
        "mission_get": "GET /v1/missions/{mission_id}",
        "mission_cancel": "POST /v1/missions/{mission_id}/cancel",
        "mission_events": "GET /v1/missions/{mission_id}/events",
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
def readyz() -> dict[str, Any]:
    """Dependency readiness — 200 when ready, 503 when not."""
    payload = check_readiness(get_runtime())
    if not payload.get("ready"):
        raise HTTPException(status_code=503, detail=payload)
    return payload


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
async def create_mission(
    req: MissionRequest,
    background_tasks: BackgroundTasks,
    request: Request,
) -> dict[str, Any]:
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

    timezone = str(req.scope.get("timezone") or "Asia/Kolkata")
    as_of = req.scope.get("as_of") or req.scope.get("asOf")
    # Correlate with X-Request-ID middleware (echoed on the response).
    request_id = str(getattr(request.state, "request_id", None) or uuid4().hex)
    session_id = req.session_id or uuid4().hex

    # Async accept path — validate scenario early for swarm-bound queries.
    if not req.wait:
        route_hint = await route_for(runtime, query=query)
        if route_hint == "swarm":
            from seleric_swarm.coordinator.intake import has_analytical_signal
            from seleric_swarm.swarm.providers.errors import ScenarioNotFoundError
            from seleric_swarm.swarm.providers.fixtures import load_scenario

            if not has_analytical_signal(query, runtime.metrics):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Query does not name a known metric or a supported analysis "
                        "(diagnose / forecast / compare / recommend / health check)."
                    ),
                )
            try:
                load_scenario(req.scenario_id)
            except ScenarioNotFoundError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        mission_id = new_mission_id(swarm_likely=route_hint == "swarm")
        accepted = seed_running_mission(
            runtime,
            mission_id=mission_id,
            query=query,
            request_id=request_id,
            session_id=session_id,
        )
        background_tasks.add_task(
            run_mission_job,
            runtime,
            mission_id=mission_id,
            query=query,
            timezone=timezone,
            as_of=as_of,
            session_id=session_id,
            request_id=request_id,
            full_diagnostic=req.full_diagnostic,
            full_prediction=req.full_prediction,
            full_skeptic=req.full_skeptic,
            scenario_id=req.scenario_id,
            execution_mode=req.execution_mode,
        )
        return accepted

    try:
        dispatched = await run_any_mission(
            runtime,
            query=query,
            timezone=timezone,
            as_of=as_of,
            session_id=req.session_id,
            request_id=request_id,
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
    # Prefer raw payload (swarm + async running placeholders).
    raw = getattr(runtime.store, "get_raw", lambda _mid: None)(mission_id)
    if isinstance(raw, dict) and (
        raw.get("route") in {"swarm", "pending", "failed", "lookup"} or raw.get("async")
    ):
        return raw
    result = runtime.store.get(mission_id)
    if result is None:
        raise HTTPException(status_code=404, detail="mission not found")
    return result.model_dump()


@app.post("/v1/missions/{mission_id}/cancel")
def cancel_mission(mission_id: str) -> dict[str, Any]:
    """Cancel a running async mission (cooperative / best-effort)."""
    runtime = get_runtime()
    try:
        return cancel_running_mission(runtime, mission_id=mission_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="mission not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/v1/missions/{mission_id}/events")
def get_mission_events(
    mission_id: str,
    family: str | None = Query(None, description="one of: " + ", ".join(sorted(_EVENT_FAMILIES))),
    after_seq: int = Query(0, ge=0, description="return events with seq > this"),
    limit: int = Query(200, ge=1, le=1000),
) -> dict[str, Any]:
    """Return structured control-plane events for a persisted mission."""
    if family is not None and family not in _EVENT_FAMILIES:
        raise HTTPException(
            status_code=400,
            detail=f"unknown family '{family}'; valid: {', '.join(sorted(_EVENT_FAMILIES))}",
        )
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
