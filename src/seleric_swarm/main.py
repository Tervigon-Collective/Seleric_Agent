from __future__ import annotations

import asyncio
import inspect
import json
from contextlib import asynccontextmanager
from datetime import date
from typing import Any
from uuid import uuid4

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.responses import Response, StreamingResponse

from seleric_swarm.agent.runner import run_v3_mission
from seleric_swarm.api.async_missions import (
    cancel_running_mission,
    enqueue_durable_mission,
    new_mission_id,
    publish_durable_mission,
)
from seleric_swarm.api.conversations import router as conversations_router
from seleric_swarm.api.mission_access import request_principal, require_mission_access
from seleric_swarm.api.phase7 import router as phase7_router
from seleric_swarm.api.ready import check_readiness
from seleric_swarm.api.request_id import RequestIdMiddleware
from seleric_swarm.api.security import ApiSecurityMiddleware
from seleric_swarm.bootstrap import build_runtime
from seleric_swarm.llm.port import ChatMessage, LLMRequest, LLMRequestMetadata
from seleric_swarm.observability.tracing import (
    configure_opentelemetry,
    instrument_fastapi,
    traced_span,
)
from seleric_swarm.runtime import SwarmRuntime

# event families the control plane emits (without the trailing "_")
_EVENT_FAMILIES = frozenset(
    {"mission", "decomposition", "task", "artifact", "leadership", "claim", "skeptic", "remediation"}
)

_runtime: SwarmRuntime | None = None


def get_runtime() -> SwarmRuntime:
    global _runtime
    if _runtime is None:
        _runtime = build_runtime()
    return _runtime


async def _close_component(component: object | None, method: str = "close") -> None:
    closer = getattr(component, method, None)
    if closer is None:
        return
    if inspect.iscoroutinefunction(closer):
        await closer()
        return
    result = await asyncio.to_thread(closer)
    if inspect.isawaitable(result):
        await result


async def _warmup(runtime: Any) -> None:
    """Fire-and-forget: warm Laya/JEV once at boot so the first real query
    doesn't eat its model cold-start (first call can take >12s). Laya is shared
    server-side, so warming from the api process also benefits the recovery
    worker. Never raises — a warmup failure must not affect startup."""
    try:
        from seleric_swarm.agent.intent import classify_query

        s = runtime.settings
        base = getattr(s, "jev_base_url", "")
        if not base:
            return
        await classify_query(
            "warmup",
            base_url=base,
            api_key=getattr(s, "jev_api_key", ""),
            timeout=float(getattr(s, "jev_timeout_s", 20.0)),
        )
    except Exception:  # noqa: S110 - warmup is best-effort
        pass


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _runtime
    if _runtime is None:
        _runtime = build_runtime()
    checkpoint_provider = getattr(_runtime, "checkpoint_provider", None)
    checkpoint_setup = getattr(checkpoint_provider, "setup", None)
    if checkpoint_setup is not None:
        await checkpoint_setup()
    asyncio.create_task(_warmup(_runtime))
    try:
        yield
    finally:
        rt = _runtime
        if rt is not None:
            activity_events = getattr(rt, "activity_events", None)
            notifier = getattr(activity_events, "notifier", None)
            shutdown = asyncio.gather(
                _close_component(getattr(rt, "run_queue", None)),
                _close_component(getattr(rt, "checkpoint_provider", None)),
                _close_component(notifier),
                _close_component(rt.mcp, "aclose"),
                return_exceptions=True,
            )
            try:
                await asyncio.wait_for(
                    shutdown,
                    timeout=max(0.1, rt.settings.shutdown_timeout_s),
                )
            except TimeoutError:
                shutdown.cancel()


app = FastAPI(
    title="Seleric Intelligence Swarm",
    version="0.1.0",
    lifespan=lifespan,
    # Relative server URL so Swagger Try-it-out posts to whatever host served
    # /docs (localhost vs 127.0.0.1 vs a preview proxy), not a hardcoded origin.
    servers=[{"url": "/", "description": "this host"}],
    swagger_ui_parameters={"displayRequestDuration": True},
)
app.state.runtime_provider = get_runtime
app.include_router(conversations_router)
app.include_router(phase7_router)

# Voice agent token route (docs/features/voice-agent/). Mounted unconditionally
# so the route can answer 404 "voice is not enabled" rather than vanishing —
# but an import failure while voice is switched ON is fatal, not a warning that
# would leave a silent 404 in production.
try:
    from seleric_swarm.voice.token import router as _voice_router

    # Registers GET /v1/voice/dev on the same router (dev surfaces only).
    from seleric_swarm.voice import dev_page as _voice_dev_page  # noqa: F401

    app.include_router(_voice_router)
except Exception:
    import logging as _logging

    from seleric_swarm.config.settings import get_settings as _get_settings

    if _get_settings().voice_enabled:
        raise
    _logging.getLogger("seleric.api.voice").warning(
        "voice token route not mounted (voice is disabled)", exc_info=True
    )

# Read-only spatial AI-Office UI gateway (SSE snapshot + event stream).
try:
    from fastapi.middleware.cors import CORSMiddleware

    from seleric_swarm.api.office.gateway import router as _office_router
    from seleric_swarm.api.office.registry import register_mission as _register_mission

    app.include_router(_office_router)
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
        allow_methods=["GET"],
        allow_headers=["*"],
    )
except Exception:  # office UI is optional — never block the core API on it
    import logging as _logging

    _logging.getLogger("seleric.api.office").warning(
        "office gateway not mounted", exc_info=True
    )

    def _register_mission(_mission_id: str | None) -> None:  # type: ignore[misc]
        return None

# Load repo .env before middleware reads settings (CWD-independent).
_settings_boot = None
try:
    from dotenv import load_dotenv

    from seleric_swarm.config.settings import get_settings
    from seleric_swarm.paths import repo_root

    load_dotenv(repo_root() / ".env")
    get_settings.cache_clear()
    _settings_boot = get_settings()
except Exception:
    _settings_boot = None

# Starlette applies middleware in reverse add order: CORS outermost so
# Swagger / Cursor-preview preflights never hit API-key or rate-limit 401s.
app.add_middleware(
    ApiSecurityMiddleware,
    api_key=getattr(_settings_boot, "api_key", "") or "",
    rate_limit_per_minute=int(getattr(_settings_boot, "rate_limit_per_minute", 60) or 60),
    rate_limit_enabled=bool(getattr(_settings_boot, "rate_limit_enabled", True)),
    default_workspace_id=getattr(_settings_boot, "default_workspace_id", "default"),
    default_user_id=getattr(_settings_boot, "default_user_id", "default"),
    trust_x_forwarded_for=bool(
        getattr(_settings_boot, "trust_x_forwarded_for", False)
    ),
    trust_identity_headers=bool(
        getattr(_settings_boot, "trust_identity_headers", False)
    ),
)
app.add_middleware(RequestIdMiddleware)
_local_cors = _settings_boot is None or _settings_boot.is_dev_surface()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if _local_cors else [],
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?" if _local_cors else None,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
if _settings_boot is not None:
    configure_opentelemetry(_settings_boot)
    instrument_fastapi(app)


_KNOWN_SCENARIO_IDS = {"cac_regression"}


_SWAGGER_PLACEHOLDERS = {"string"}


def _is_swagger_placeholder(value: object) -> bool:
    return isinstance(value, str) and value.strip().lower() in _SWAGGER_PLACEHOLDERS


class MissionRequest(BaseModel):
    query: str = Field(
        ...,
        json_schema_extra={"examples": ["Why has CAC increased over the last three days?"]},
    )
    # docs/24_API_CONTRACTS.md documents this for fixture/replay-mode swarm
    # testing. Previously accepted-but-silently-ignored by pydantic (no field
    # existed) — an unknown id now 400s instead of being dropped (docs/44
    # ROB-004).
    scenario_id: str | None = Field(default=None, json_schema_extra={"examples": [None]})
    scope: dict[str, Any] = Field(
        default_factory=dict,
        json_schema_extra={"examples": [{"timezone": "Asia/Kolkata"}]},
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
    full_strategy: bool = True
    # Only one mode: development, using the live Seleric MCP (catalogue + metrics_query).
    execution_mode: str = "development"
    # wait=true (default): run synchronously and return the finished mission.
    # wait=false: accept immediately (status=running); poll GET /v1/missions/{id}.
    wait: bool = True
    # stream=true: return an SSE stream (text/event-stream) that emits the final
    # answer token-by-token as answer.delta frames, then a terminal
    # answer.completed frame with the full mission. Implies synchronous run
    # (wait is ignored).
    stream: bool = False

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "query": "Why has CAC increased over the last three days?",
                    "scope": {"timezone": "Asia/Kolkata"},
                    "mode": "read_only",
                    "execution_mode": "development",
                    "full_diagnostic": True,
                    "full_prediction": True,
                    "full_skeptic": True,
                    "full_strategy": True,
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
        "ready": "/ready",
        "missions": "POST /v1/missions",
        "mission_get": "GET /v1/missions/{mission_id}",
        "mission_cancel": "POST /v1/missions/{mission_id}/cancel",
        "mission_events": "GET /v1/missions/{mission_id}/events",
        "mission_trace": "GET /v1/missions/{mission_id}/trace",
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
@app.get("/ready")
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


def _sse_frame(event_type: str, data: dict[str, Any], seq: int) -> str:
    payload = {**data, "type": event_type}
    return (
        f"id: {seq}\n"
        f"event: {event_type}\n"
        f"data: {json.dumps(payload, separators=(',', ':'), default=str)}\n\n"
    )


def _stream_mission_response(
    runtime: SwarmRuntime,
    *,
    query: str,
    timezone: str,
    as_of: Any,
    session_id: str,
    request_id: str,
    principal: Any,
    req: MissionRequest,
) -> StreamingResponse:
    """Run the mission synchronously while streaming the answer over SSE.

    ``run_v3_mission`` runs in a task; its ``on_stream`` callback pushes deltas
    onto a queue the generator drains into ``answer.delta`` frames. The final
    mission object rides the terminal ``answer.completed`` frame.
    """
    queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

    def on_stream(kind: str, text: str = "") -> None:
        queue.put_nowait((kind, text))

    async def _run() -> None:
        try:
            dispatched = await run_v3_mission(
                runtime,
                query=query,
                timezone=timezone,
                as_of=as_of,
                session_id=session_id,
                request_id=request_id,
                full_diagnostic=req.full_diagnostic,
                full_prediction=req.full_prediction,
                full_skeptic=req.full_skeptic,
                full_strategy=req.full_strategy,
                execution_mode=req.execution_mode,
                workspace_id=principal.workspace_id,
                owner_user_id=principal.user_id,
                thread_id=session_id,
                run_id=request_id,
                on_stream=on_stream,
            )
            queue.put_nowait(("__done__", dispatched))
        except Exception as exc:  # surface as a terminal error frame, never hang
            queue.put_nowait(("__error__", str(exc)))

    async def generate() -> Any:
        # Immediate frame so the client sees the stream open right away — if this
        # arrives instantly but deltas don't, the transport streams fine and the
        # model isn't streaming (e.g. TestModel fallback with no LLM configured).
        seq = 1
        yield _sse_frame("answer.started", {"request_id": request_id}, seq)
        task = asyncio.create_task(_run())
        deltas = 0
        try:
            while True:
                kind, payload = await queue.get()
                seq += 1
                if kind == "delta":
                    deltas += 1
                    yield _sse_frame("answer.delta", {"delta": payload}, seq)
                elif kind == "reset":
                    yield _sse_frame("answer.reset", {}, seq)
                elif kind == "__done__":
                    import logging

                    logging.getLogger("seleric.api.stream").info(
                        "mission_stream_done request_id=%s deltas=%d", request_id, deltas
                    )
                    result = payload.get("result", {}) if isinstance(payload, dict) else {}
                    yield _sse_frame(
                        "answer.completed",
                        {
                            "route": payload.get("route") if isinstance(payload, dict) else None,
                            "mission_id": result.get("mission_id"),
                            "status": result.get("status"),
                            "final_response": result.get("final_response"),
                            "evidence": result.get("evidence"),
                            "limitations": result.get("limitations"),
                        },
                        seq,
                    )
                    break
                elif kind == "__error__":
                    yield _sse_frame("error", {"error": str(payload)}, seq)
                    break
        finally:
            await task

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/v1/missions")
async def create_mission(
    req: MissionRequest,
    background_tasks: BackgroundTasks,
    request: Request,
) -> Any:
    runtime = get_runtime()
    principal = request_principal(request)
    if req.mode != "read_only":
        raise HTTPException(status_code=400, detail="Only read_only mode is allowed in V1")
    if req.execution_mode != "development":
        raise HTTPException(
            status_code=400,
            detail="execution_mode must be: development",
        )
    if _is_swagger_placeholder(req.scenario_id):
        req.scenario_id = None
    if req.scenario_id is not None and req.scenario_id not in _KNOWN_SCENARIO_IDS:
        raise HTTPException(status_code=400, detail=f"Unknown scenario_id: {req.scenario_id!r}")
    query = (req.query or "").strip()
    if _is_swagger_placeholder(query):
        raise HTTPException(
            status_code=400,
            detail="query must be a real question, not the OpenAPI placeholder 'string'",
        )
    if not query:
        raise HTTPException(status_code=400, detail="query must be a non-empty string")
    if req.session_id is not None and req.session_id.strip() in {"", "string"}:
        # Swagger placeholder "string" should not pollute mission metadata
        req.session_id = None

    timezone = str(req.scope.get("timezone") or "Asia/Kolkata")
    if _is_swagger_placeholder(timezone):
        timezone = "Asia/Kolkata"
    as_of = req.scope.get("as_of") or req.scope.get("asOf")
    if _is_swagger_placeholder(as_of):
        as_of = None
    if as_of is not None:
        if not isinstance(as_of, str):
            raise HTTPException(status_code=400, detail="scope.as_of must be a date string (YYYY-MM-DD)")
        try:
            parsed_as_of = date.fromisoformat(as_of[:10])
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail=f"scope.as_of is not a valid date: {as_of!r}"
            ) from exc
        # Dates too close to date.min/date.max overflow the day-range arithmetic
        # ("last N days", "as_of - 1 year", ...) used throughout time-range
        # resolution. Reject far outside any plausible business range instead.
        if not (1900 <= parsed_as_of.year <= 2400):
            raise HTTPException(
                status_code=400,
                detail=f"scope.as_of year out of supported range (1900-2400): {as_of!r}",
            )
    # Correlate with X-Request-ID middleware (echoed on the response).
    request_id = str(getattr(request.state, "request_id", None) or uuid4().hex)
    session_id = req.session_id or uuid4().hex
    # Sprint 5: V3 is the only mission path — swarm_v2's LLM-classification
    # routing gate (diagnostic/predictive/prescriptive -> "swarm",
    # everything else -> "lookup") was retired along with the pipeline it fed.
    route_hint = "v3"
    try:
        from seleric_swarm.observability.flow import log_mission_step

        log_mission_step(
            None,
            "mission_classified",
            route=route_hint,
            intents=[],
            query=query[:160],
            request_id=request_id,
        )
    except Exception:  # noqa: S110 - optional telemetry must not fail mission handling
        pass

    # SSE streaming path: run synchronously but stream the final answer's
    # tokens as they are generated. Shares run_v3_mission's on_stream callback
    # with the conversations run worker (same answer.delta/answer.reset shape).
    if req.stream:
        return _stream_mission_response(
            runtime,
            query=query,
            timezone=timezone,
            as_of=as_of,
            session_id=session_id,
            request_id=request_id,
            principal=principal,
            req=req,
        )

    # Async accept path.
    if not req.wait:
        mission_id = new_mission_id(swarm_likely=False)
        _register_mission(mission_id)
        accepted = await enqueue_durable_mission(
            runtime,
            mission_id=mission_id,
            query=query,
            timezone=timezone,
            as_of=as_of,
            request_id=request_id,
            session_id=session_id,
            workspace_id=principal.workspace_id,
            owner_user_id=principal.user_id,
            full_diagnostic=req.full_diagnostic,
            full_prediction=req.full_prediction,
            full_skeptic=req.full_skeptic,
            full_strategy=req.full_strategy,
            execution_mode=req.execution_mode,
            schedule=False,
        )
        background_tasks.add_task(
            publish_durable_mission,
            runtime,
            str(accepted["run_id"]),
        )
        return accepted

    dispatched = await run_v3_mission(
        runtime,
        query=query,
        timezone=timezone,
        as_of=as_of,
        session_id=session_id,
        request_id=request_id,
        full_diagnostic=req.full_diagnostic,
        full_prediction=req.full_prediction,
        full_skeptic=req.full_skeptic,
        full_strategy=req.full_strategy,
        execution_mode=req.execution_mode,
        workspace_id=principal.workspace_id,
        owner_user_id=principal.user_id,
        thread_id=session_id,
        run_id=request_id,
    )
    # Flatten: a consistent top-level mission object with a `route` marker.
    # lookup  -> MissionResult fields; swarm -> SwarmMissionResult fields.
    out = {"route": dispatched["route"], **dispatched["result"]}
    _register_mission(out.get("mission_id"))
    if not isinstance(out.get("trace"), dict):
        out["trace"] = {"request_id": request_id, "session_id": session_id}
    persisted = runtime.store.get(str(out.get("mission_id") or ""))
    raw = getattr(runtime.store, "get_raw", lambda _mid: None)(out.get("mission_id"))
    if persisted is not None:
        runtime.store.put(
            persisted,
            {
                **(raw if isinstance(raw, dict) else out),
                "workspace_id": principal.workspace_id,
                "owner_user_id": principal.user_id,
            },
        )
    list_events = getattr(runtime.store, "list_events", None)
    if list_events is None:
        from seleric_swarm.persistence.memory import extract_events, filter_events

        events = filter_events(extract_events(raw), family=None, after_seq=0, limit=1000)
    else:
        events = list_events(out.get("mission_id"), family=None, after_seq=0, limit=1000)
    out["trace"] = {**out["trace"], "events": events}
    return out


@app.get("/v1/missions/{mission_id}")
def get_mission(mission_id: str, request: Request) -> dict[str, Any]:
    runtime = get_runtime()
    # Prefer raw payload (swarm + async running placeholders).
    raw = getattr(runtime.store, "get_raw", lambda _mid: None)(mission_id)
    if not isinstance(raw, dict):
        from seleric_swarm.api.office.v3_adapter import v3_raw_snapshot

        raw = v3_raw_snapshot(mission_id)
    if isinstance(raw, dict):
        require_mission_access(request, raw, runtime)
    if isinstance(raw, dict) and (
        raw.get("route") in {"swarm", "pending", "failed", "lookup", "v3"} or raw.get("async")
    ):
        return raw
    result = runtime.store.get(mission_id)
    if result is None:
        raise HTTPException(status_code=404, detail="mission not found")
    if not isinstance(raw, dict):
        require_mission_access(request, {}, runtime)
    return result.model_dump()


@app.post("/v1/missions/{mission_id}/cancel")
def cancel_mission(mission_id: str, request: Request) -> dict[str, Any]:
    """Cancel a running async mission (cooperative / best-effort)."""
    runtime = get_runtime()
    raw = getattr(runtime.store, "get_raw", lambda _mid: None)(mission_id)
    if not isinstance(raw, dict):
        raise HTTPException(status_code=404, detail="mission not found")
    require_mission_access(request, raw, runtime)
    try:
        return cancel_running_mission(runtime, mission_id=mission_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="mission not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/v1/missions/{mission_id}/events")
def get_mission_events(
    mission_id: str,
    request: Request,
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
    raw = getattr(store, "get_raw", lambda _m: None)(mission_id)
    require_mission_access(request, raw if isinstance(raw, dict) else {}, runtime)
    # Fetch one extra row so clients can paginate without a separate count query.
    fetch_limit = limit + 1
    list_events = getattr(store, "list_events", None)
    if list_events is None:
        from seleric_swarm.persistence.memory import extract_events, filter_events

        page = filter_events(
            extract_events(getattr(store, "get_raw", lambda _m: None)(mission_id)),
            family=family,
            after_seq=after_seq,
            limit=fetch_limit,
        )
    else:
        page = list_events(mission_id, family=family, after_seq=after_seq, limit=fetch_limit)
    has_more = len(page) > limit
    events = page[:limit]
    next_after_seq = None
    if has_more and events:
        next_after_seq = int(events[-1].get("seq") or after_seq)
    return {
        "mission_id": mission_id,
        "count": len(events),
        "family": family,
        "after_seq": after_seq,
        "limit": limit,
        "has_more": has_more,
        "next_after_seq": next_after_seq,
        "events": events,
    }


@app.get("/v1/missions/{mission_id}/trace")
def get_mission_trace(mission_id: str, request: Request) -> dict[str, Any]:
    """Return trace metadata (request/session/langsmith ids) plus the full event timeline."""
    runtime = get_runtime()
    store = runtime.store
    raw = getattr(store, "get_raw", lambda _m: None)(mission_id)
    result = store.get(mission_id)
    if raw is None and result is None:
        raise HTTPException(status_code=404, detail="mission not found")
    require_mission_access(request, raw if isinstance(raw, dict) else {}, runtime)

    trace = (raw or {}).get("trace") if isinstance(raw, dict) else None
    if not isinstance(trace, dict):
        trace = result.trace.model_dump() if result is not None else {}

    list_events = getattr(store, "list_events", None)
    if list_events is None:
        from seleric_swarm.persistence.memory import extract_events, filter_events

        events = filter_events(extract_events(raw), family=None, after_seq=0, limit=1000)
    else:
        events = list_events(mission_id, family=None, after_seq=0, limit=1000)

    status = (raw or {}).get("status") if isinstance(raw, dict) else (result.status if result else None)
    return {
        "mission_id": mission_id,
        "status": status,
        "trace": trace,
        "events": events,
    }


# Office UI bundle (built into the image by the Dockerfile's ui-builder stage).
# Mounted last so every API route above wins; absent in local dev, where the
# Vite dev server serves the UI instead.
try:
    from fastapi.staticfiles import StaticFiles

    from seleric_swarm.paths import repo_root

    _ui_dist = repo_root() / "office-ui" / "dist"
    if (_ui_dist / "index.html").is_file():

        # MVP: hand the shared API key to the bundled UI so users need no setup.
        # Anyone who can load /ui/ can therefore call /v1 — replace with real
        # user login before this is more than an MVP.
        @app.get("/ui/config.js", include_in_schema=False)
        def office_ui_config() -> Response:
            key = getattr(_settings_boot, "api_key", "") or ""
            return Response(
                f"window.__SELERIC_API_KEY__ = {json.dumps(key)};\n",
                media_type="application/javascript",
                headers={"Cache-Control": "no-store"},
            )

        app.mount("/ui", StaticFiles(directory=_ui_dist, html=True), name="office-ui")
except Exception:  # the UI is optional — never block the core API on it
    import logging as _logging

    _logging.getLogger("seleric.api.office").warning("office UI not mounted", exc_info=True)


def serve() -> None:
    """Console entrypoint used by `seleric-api` after an editable install."""
    import os

    import uvicorn

    from seleric_swarm.config.settings import get_settings

    settings = get_settings()
    settings.validate_for_startup()
    host = settings.api_host or os.environ.get("API_HOST") or ""
    port = settings.api_port or int(os.environ.get("API_PORT") or "0")
    if not host or not port:
        raise SystemExit("API_HOST and API_PORT must be set in the environment (or .env)")
    # See scripts/run_dev.py for why --reload is off (hangs on this stack/OS combo).
    uvicorn.run("seleric_swarm.main:app", host=host, port=port, reload=False)
