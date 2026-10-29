"""Run one mission through the single reasoning loop and persist it where the UI already reads.

Conversations (``api/conversations.py``) pick ``final_response`` off the mission store after ``run_mission_job``; the
Office UI reads ``GET /v1/office/missions*``. Both stay unchanged: this runner writes the loop's result into those
existing shapes (via ``v3_adapter``) and into the V3 stores the office gateway falls back to.

Flow: context (history, memories, the thread's earlier queries and figures) -> ``loop.run_turn`` -> persist.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime, tzinfo
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from openai import APITimeoutError as OpenAIAPITimeoutError
from pydantic_ai.exceptions import ModelHTTPError

from seleric_swarm.agent import analysis_context
from seleric_swarm.agent.dependencies import ExecutionLimits, NullMcpClient, SelericDeps
from seleric_swarm.agent.loop import LoopResult, history_messages, run_turn
from seleric_swarm.agent.mcp_toolset import compact_value, tool_defs
from seleric_swarm.agent.model import resolve_loop_model
from seleric_swarm.agent.output import MissionResult as V3MissionResult
from seleric_swarm.agent.progress import emit_progress
from seleric_swarm.api.office.registry import register_mission
from seleric_swarm.api.office.v3_adapter import v3_raw_snapshot
from seleric_swarm.api.status import is_terminal_status
from seleric_swarm.api.v3_state import get_v3_artifact_store, get_v3_mission_store
from seleric_swarm.cancellation import MissionCancelledError
from seleric_swarm.contracts.lookup import EvidenceView, MissionError, TraceInfo
from seleric_swarm.contracts.lookup import MissionResult as LookupMissionResult
from seleric_swarm.conversations.contracts import (
    Artifact,
    ContextBundle,
    Principal,
    PrincipalAuthMethod,
)
from seleric_swarm.runtime import SwarmRuntime
from seleric_swarm.services.catalogue_bootstrap import CatalogueSnapshot
from seleric_swarm.services.time_range import as_of_date
from seleric_swarm.state.missions import Mission

_log = logging.getLogger("seleric.agent.runner")

_LOOKUP_STATUSES = {"completed", "partial", "failed", "running", "cancelled", "blocked", "prototype_completed"}

HISTORY_TURNS = 6  # (user, assistant) pairs of the thread sent to the model
KEEP_PAYLOADS = 8  # the thread's most recent tool results stay in the grounding pool, so follow-ups are checked too
MAX_MEMORIES = 5
_TOOL_DEFS_TTL_S = 3600.0
_tool_defs_cache: dict[int, tuple[float, list[dict[str, Any]]]] = {}
_guide_cache: dict[int, tuple[float, str]] = {}


# --- failure text -------------------------------------------------------------------------------
def _flatten_exceptions(exc: BaseException) -> list[BaseException]:
    """Unwrap ``BaseExceptionGroup`` (e.g. ``FallbackModel``'s all-candidates-failed group) into its leaf exceptions
    so failure classification can inspect the actual provider errors."""
    if isinstance(exc, BaseExceptionGroup):
        flat: list[BaseException] = []
        for sub in exc.exceptions:
            flat.extend(_flatten_exceptions(sub))
        return flat
    return [exc]


def _user_facing_agent_failure(exc: BaseException) -> tuple[str, str]:
    """Chat-safe failure text — never dump provider HTTP bodies to the UI. Classifies by exception type/attributes."""
    causes = _flatten_exceptions(exc)
    if any(
        (isinstance(c, ModelHTTPError) and c.status_code == 429) or getattr(c, "status_code", None) == 429
        or "ratelimit" in type(c).__name__.lower()
        for c in causes
    ):
        return ("The language model is rate-limited right now. Please retry in a moment.", "LLM_RATE_LIMITED")
    if any(isinstance(c, (OpenAIAPITimeoutError, TimeoutError)) or "timeout" in type(c).__name__.lower() for c in causes):
        return ("The agent timed out. Please retry.", "V3_AGENT_TIMEOUT")
    return ("The agent could not complete this question. Please retry.", "V3_AGENT_FAILED")


def _failure_detail(exc: BaseException) -> list[str]:
    """What actually failed, kept on the mission so a V3_AGENT_FAILED is diagnosable after the logs rotate. Exception
    types and HTTP status codes only — never a provider response body, which can reach the UI."""
    out = []
    for cause in _flatten_exceptions(exc)[:6]:
        status = getattr(cause, "status_code", None)
        out.append(f"{type(cause).__name__}{f' {status}' if status else ''}")
    return out


# --- small builders -----------------------------------------------------------------------------
def _as_of_datetime(as_of: str | None, timezone: str = "Asia/Kolkata") -> datetime:
    """Anchor as_of on the mission calendar day, not UTC-now (which can lag IST)."""
    day = as_of_date(as_of, timezone)
    tz: tzinfo
    try:
        tz = ZoneInfo(timezone)
    except ZoneInfoNotFoundError:
        tz = UTC
    return datetime(day.year, day.month, day.day, tzinfo=tz)


def _context_bundle(raw: dict[str, Any] | None) -> ContextBundle:
    if not raw:
        return ContextBundle()
    try:
        return ContextBundle.model_validate(raw)
    except Exception:
        _log.warning("context_bundle_validation_failed", exc_info=True)
        return ContextBundle()


def _principal(*, workspace_id: str, user_id: str) -> Principal:
    return Principal(
        principal_id=uuid4().hex,
        workspace_id=workspace_id or "default",
        user_id=user_id or "default",
        authenticated=False,
        auth_method=PrincipalAuthMethod.ANONYMOUS,
    )


def _lookup_status(status: str) -> str:
    """Map a MissionResult status onto the stored mission status. Only reached on the terminal persistence path, so a
    non-terminal status must not survive (a 'running' row stuck forever while the run reported COMPLETED)."""
    if not is_terminal_status(status):
        return "partial"
    return status if status in _LOOKUP_STATUSES else "partial"


def _to_lookup(
    result: V3MissionResult, *, request_id: str, session_id: str, evidence: list[EvidenceView]
) -> LookupMissionResult:
    error = None
    if result.error_code:
        error = MissionError(code=result.error_code, message=result.final_response or result.error_code)
    intent = result.trace.get("intent")
    # TraceInfo allows extras; forward the full trace so stage telemetry is not dropped when the record is persisted.
    trace_payload = {
        **dict(result.trace or {}),
        "request_id": str(result.trace.get("request_id") or request_id),
        "session_id": str(result.trace.get("session_id") or session_id),
    }
    return LookupMissionResult(
        mission_id=result.mission_id,
        status=_lookup_status(result.status),  # type: ignore[arg-type]
        query_class=str(intent) if intent else None,
        mission_lead="seleric_agent",
        initial_mission_lead="seleric_agent",
        evidence=evidence,
        limitations=list(result.limitations),
        final_response=result.final_response,
        error=error,
        trace=TraceInfo.model_validate(trace_payload),
    )


# --- context ------------------------------------------------------------------------------------
async def _catalogue_snapshot(runtime: SwarmRuntime) -> CatalogueSnapshot:
    """Warm the live catalogue cache and snapshot it. Fail-open: an empty snapshot leaves the model to the catalogue tools."""
    bootstrap = getattr(runtime, "bootstrap", None)
    if bootstrap is None:
        return CatalogueSnapshot()
    try:
        await bootstrap.refresh_if_stale()
        return bootstrap.snapshot()
    except Exception:
        _log.warning("catalogue_snapshot_failed", exc_info=True)
        return CatalogueSnapshot()


async def _tool_definitions(mcp: Any) -> list[dict[str, Any]]:
    """The MCP server's read tools (schemas as published), cached for an hour per gateway."""
    key = id(mcp)
    cached = _tool_defs_cache.get(key)
    if cached is not None and time.monotonic() - cached[0] < _TOOL_DEFS_TTL_S:
        return cached[1]
    lister = getattr(mcp, "list_tools", None)
    if lister is None:
        return []
    defs = tool_defs(await lister())
    if defs:
        _tool_defs_cache[key] = (time.monotonic(), defs)
    return defs


async def _server_guide(mcp: Any) -> str:
    """The MCP server's own instructions (data rules), cached for an hour per gateway."""
    key = id(mcp)
    cached = _guide_cache.get(key)
    if cached is not None and time.monotonic() - cached[0] < _TOOL_DEFS_TTL_S:
        return cached[1]
    getter = getattr(mcp, "server_instructions", None)
    if getter is None:
        return ""
    try:
        text = str(await getter() or "")
    except Exception:  # the guide is an enrichment: the loop still works without it
        return ""
    if text:
        _guide_cache[key] = (time.monotonic(), text)
    return text


def _part_text(message: dict[str, Any]) -> str:
    chunks = []
    for part in message.get("parts") or []:
        if isinstance(part, dict) and str(part.get("type") or "").upper() == "TEXT":
            content = part.get("content")
            if isinstance(content, str) and content.strip():
                chunks.append(content.strip())
    return "\n".join(chunks)


def _history(bundle: ContextBundle, question: str) -> list[tuple[str, str]]:
    """The thread's last turns as (role, text), without the question being asked now."""
    turns = []
    for raw in bundle.recent_messages:
        message = raw if isinstance(raw, dict) else {}
        text = _part_text(message)
        if text:
            turns.append(("user" if str(message.get("role") or "").upper() == "USER" else "assistant", text))
    if turns and turns[-1] == ("user", question.strip()):
        turns.pop()
    return turns[-2 * HISTORY_TURNS:]


def _memories(bundle: ContextBundle) -> list[str]:
    out = []
    for mem in bundle.memories[:MAX_MEMORIES]:
        text = (mem.content if isinstance(mem.content, str) else str(mem.content)).strip()
        if text:
            out.append(f"({getattr(mem.type, 'value', str(mem.type))}) {text[:300]}")
    return out


def _thread_summary(bundle: ContextBundle) -> str:
    summary = bundle.latest_summary
    if summary is None:
        return ""
    text = (summary.summary if isinstance(summary.summary, str) else str(summary.summary)).strip()
    return text[:2000]


def _workspace_notes(bundle: ContextBundle) -> str:
    config = bundle.workspace_config
    if not config:
        return ""
    try:
        return json.dumps(config, ensure_ascii=False, sort_keys=True, default=str)[:800]
    except (TypeError, ValueError):
        return str(config)[:800]


def _shipped_token_count(
    *,
    question: str,
    history: list[tuple[str, str]],
    memories: list[str],
    prior_queries: list[str],
    thread_summary: str,
    workspace_notes: str,
) -> int:
    from seleric_swarm.conversations.context import approximate_token_count

    rendered = "\n".join(
        [
            question,
            thread_summary,
            workspace_notes,
            "\n".join(memories),
            "\n".join(prior_queries),
            "\n".join(f"{role}: {text}" for role, text in history),
        ]
    )
    return approximate_token_count(rendered)


def _payload_ref(payload: dict[str, Any], *, index: int) -> dict[str, Any]:
    """Short, retrievable handle for a tool payload — not the payload itself."""
    rows = payload.get("data") if isinstance(payload.get("data"), list) else None
    return {
        "index": index,
        "keys": sorted(str(k) for k in payload.keys())[:16],
        "row_count": len(rows) if rows is not None else None,
        "preview": json.dumps(compact_value(payload, max_rows=3, max_chars=400), separators=(",", ":"), default=str)[:400],
    }


def _thread_memory(
    store: Any, workspace_id: str, thread_id: str
) -> tuple[list[str], list[Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Prior queries, compact payloads for grounding, payload refs for the composition audit, and the analysis
    records of the latest turn (newest artifacts come first)."""
    queries: list[str] = []
    payloads: list[Any] = []
    refs: list[dict[str, Any]] = []
    context: list[dict[str, Any]] | None = None
    list_fn = getattr(store, "list_for_context", None)
    if not callable(list_fn):
        return queries, payloads, refs, []
    try:
        for artifact in list_fn(workspace_id, thread_id):
            body = getattr(artifact, "payload", None)
            if not isinstance(body, dict):
                continue
            kind = getattr(artifact, "artifact_type", None)
            artifact_id = getattr(artifact, "id", None) or getattr(artifact, "artifact_id", None)
            if kind == "turn_record" and context is None:
                context = [e for e in body.get("context") or [] if isinstance(e, dict)]
            if kind == "turn_record" and len(queries) < 40:
                queries += [str(q) for q in body.get("queries") or []]
            elif kind == "turn_payload_refs" and len(refs) < KEEP_PAYLOADS * 2:
                for ref in body.get("refs") or []:
                    if isinstance(ref, dict):
                        refs.append({**ref, "artifact_id": artifact_id})
            elif kind == "turn_payloads" and len(payloads) < KEEP_PAYLOADS:
                # Prefer refs for the prompt audit; keep compact payloads only for figure grounding.
                for payload in body.get("payloads") or []:
                    if isinstance(payload, dict):
                        payloads.append(payload)
    except Exception:
        _log.warning("thread_memory_load_failed", exc_info=True)
    return queries[:40], payloads[:KEEP_PAYLOADS], refs[: KEEP_PAYLOADS * 2], context or []


def _persist_turn(
    store: Any, *, loop_result: LoopResult, query: str, workspace_id: str, thread_id: str, mission_id: str, as_of_dt: datetime
) -> None:
    """Remember queries, compact payloads (grounding), and payload refs (selection/audit). Non-fatal."""
    try:
        digest = [
            f"{e['query_id']}: {e['tool']} {json.dumps(e['args'], separators=(',', ':'), default=str)}"
            for e in loop_result.state.evidence
            if e.get("query_id")
        ]
        store.put(Artifact(
            workspace_id=workspace_id, artifact_type="turn_record", classification="ui", mission_id=mission_id,
            thread_id=thread_id,
            payload={"query": query, "mission_id": mission_id, "as_of": as_of_dt.date().isoformat(),
                     "evidence_ids": loop_result.state.evidence_ids[:8], "queries": digest,
                     "context": list(getattr(loop_result.state, "context", []))[-analysis_context.MAX_ENTRIES:]}))
        raw = [p for p in loop_result.state.payloads if isinstance(p, dict)]
        keep = [compact_value(p) for p in raw][-KEEP_PAYLOADS:]
        if keep:
            store.put(Artifact(
                workspace_id=workspace_id, artifact_type="turn_payloads", classification="ui", mission_id=mission_id,
                thread_id=thread_id, payload={"payloads": keep}))
            store.put(Artifact(
                workspace_id=workspace_id, artifact_type="turn_payload_refs", classification="ui", mission_id=mission_id,
                thread_id=thread_id,
                payload={
                    "refs": [
                        {
                            **_payload_ref(p, index=i),
                            "evidence_ids": list(loop_result.state.evidence_ids[:8]),
                            "mission_id": mission_id,
                        }
                        for i, p in enumerate(keep)
                    ]
                },
            ))
    except Exception:
        _log.warning("turn_persist_failed", exc_info=True)


def _compose_runner_context(
    *,
    bundle: ContextBundle,
    question: str,
    history: list[tuple[str, str]],
    memories: list[str],
    prior_queries: list[str],
    prior_payloads: list[Any],
    prior_payload_refs: list[dict[str, Any]],
    thread_summary: str,
    workspace_notes: str,
) -> dict[str, Any]:
    shipped = _shipped_token_count(
        question=question,
        history=history,
        memories=memories,
        prior_queries=prior_queries,
        thread_summary=thread_summary,
        workspace_notes=workspace_notes,
    )
    composition = dict(bundle.composition or {})
    composition["runner"] = {
        "shipped_tokens": shipped,
        "bundle_token_estimate": bundle.token_estimate,
        "delta_tokens": shipped - int(bundle.token_estimate or 0),
        "history_turns": len(history),
        "memories_used": len(memories),
        "prior_query_count": len(prior_queries),
        "prior_payload_count": len(prior_payloads),
        "prior_payload_refs": prior_payload_refs[:16],
        "thread_summary_chars": len(thread_summary),
        "workspace_notes_chars": len(workspace_notes),
    }
    _log.info(
        "context_budget mission_bundle_tokens=%s runner_shipped_tokens=%s delta=%s budget=%s",
        bundle.token_estimate,
        shipped,
        shipped - int(bundle.token_estimate or 0),
        bundle.token_budget,
    )
    return composition


def _snapshot_loader(runtime: SwarmRuntime) -> Callable[[], Any]:
    async def load() -> dict[str, Any] | None:
        from seleric_swarm.services.business_state.formatter import (
            _snapshot_context,
            _units,
            is_stale,
        )
        from seleric_swarm.services.domain_health.snapshot_store import SnapshotStore

        snap = await SnapshotStore().aget_latest("business")
        if snap is None or snap.status == "UNAVAILABLE" or is_stale(snap):
            return None
        return _snapshot_context(snap, _units(runtime, snap))

    return load


# --- the mission --------------------------------------------------------------------------------
async def run_v3_mission(
    runtime: SwarmRuntime,
    *,
    query: str,
    timezone: str = "Asia/Kolkata",
    as_of: str | None = None,
    date_axis: str | None = None,
    session_id: str | None = None,
    request_id: str | None = None,
    mission_id: str | None = None,
    context_bundle: dict[str, Any] | None = None,
    workspace_id: str | None = None,
    owner_user_id: str | None = None,
    thread_id: str | None = None,
    run_id: str | None = None,
    on_stream: Callable[[str, str], None] | None = None,
    **_ignored: Any,
) -> dict[str, Any]:
    """Execute the reasoning loop and persist for conversations + Office UI.

    Extra kwargs are accepted and ignored so ``run_mission_job`` / ``create_mission`` can call this with their full
    historical argument set.
    """
    mission_id = mission_id or f"MS3-{uuid4().hex[:10]}"
    request_id = request_id or uuid4().hex
    thread_id = thread_id or session_id or uuid4().hex
    run_id = run_id or request_id
    workspace_id = str(workspace_id or getattr(runtime.settings, "default_workspace_id", "default"))
    owner_user_id = str(owner_user_id or getattr(runtime.settings, "default_user_id", "default"))
    as_of_dt = _as_of_datetime(as_of, timezone)
    register_mission(mission_id)

    v3_store = get_v3_mission_store()
    if v3_store.get(mission_id) is None:
        v3_store.create(Mission(
            mission_id=mission_id, query=query, as_of=as_of_dt, workspace_id=workspace_id,
            owner_user_id=owner_user_id, thread_id=thread_id, run_id=run_id))

    from seleric_swarm.api.async_missions import (
        is_cancel_requested,  # imports this module: loaded late
    )

    mcp = getattr(runtime, "mcp", None) or NullMcpClient()
    artifact_store = get_v3_artifact_store()
    emit_progress(mission_id, "agent.stage", "Reading your question", {"stage": "understand"})
    started = time.perf_counter()
    stages: dict[str, int] = {}
    base_trace = {"request_id": request_id, "session_id": thread_id, "intent": "agent"}
    loop_result: LoopResult | None = None

    from seleric_swarm.observability.traces import mission_trace

    with mission_trace(
        mission_id, query=query, route="v3", request_id=request_id, session_id=thread_id, thread_id=thread_id,
        workspace_id=workspace_id, user_id=owner_user_id,
    ) as m_trace:
        try:
            bundle = _context_bundle(context_bundle)
            catalogue, defs, guide = await asyncio.gather(
                _catalogue_snapshot(runtime), _tool_definitions(mcp), _server_guide(mcp)
            )
            prior_queries, prior_payloads, prior_payload_refs, prior_context = _thread_memory(
                artifact_store, workspace_id, thread_id
            )
            history_turns = _history(bundle, query)
            memory_lines = _memories(bundle)
            thread_summary = _thread_summary(bundle)
            workspace_notes = _workspace_notes(bundle)
            context_composition = _compose_runner_context(
                bundle=bundle,
                question=query,
                history=history_turns,
                memories=memory_lines,
                prior_queries=prior_queries,
                prior_payloads=prior_payloads,
                prior_payload_refs=prior_payload_refs,
                thread_summary=thread_summary,
                workspace_notes=workspace_notes,
            )
            stages["context_ms"] = round((time.perf_counter() - started) * 1000)
            from seleric_swarm.agent.prompt import system_prompt

            deps = SelericDeps(
                mission_id=mission_id, as_of=as_of_dt,
                principal=_principal(workspace_id=workspace_id, user_id=owner_user_id),
                thread_id=thread_id, run_id=run_id, trace_id=request_id, context=bundle, mcp_client=mcp,
                artifact_store=artifact_store,
                limits=ExecutionLimits(
                    max_runtime_seconds=float(getattr(runtime.settings, "mission_timeout_s", 600.0)),
                    mcp_call_timeout_s=float(getattr(runtime.settings, "mcp_call_timeout_s", 15.0)),
                    agent_retries=int(getattr(runtime.settings, "agent_retries", 2)),
                ),
                catalogue=catalogue, metrics=getattr(runtime, "metrics", None), date_axis=date_axis,
                context_window=analysis_context.last_window(prior_context),
            )
            loop_result = await asyncio.wait_for(
                run_turn(
                    deps=deps,
                    model=resolve_loop_model(runtime.settings),
                    gateway=mcp,
                    tool_defs=defs,
                    system_prompt=system_prompt(catalogue, datetime.now(as_of_dt.tzinfo or UTC), guide),
                    question=query,
                    history=history_messages(history_turns),
                    memories=memory_lines,
                    prior_queries=prior_queries,
                    prior_payloads=prior_payloads,
                    prior_context=prior_context,
                    thread_summary=thread_summary,
                    workspace_notes=workspace_notes,
                    is_cancelled=lambda: is_cancel_requested(mission_id, runtime),
                    snapshot_loader=_snapshot_loader(runtime),
                    on_stream=on_stream,
                ),
                timeout=deps.limits.max_runtime_seconds,
            )
            stages["agent_loop_ms"] = round((time.perf_counter() - started) * 1000) - stages["context_ms"]
            result = V3MissionResult(
                mission_id=mission_id, status=loop_result.status,  # type: ignore[arg-type]
                query=query, as_of=as_of_dt, final_response=loop_result.answer,
                evidence_ids=loop_result.state.evidence_ids, limitations=loop_result.limitations,
                parts=loop_result.state.parts,
                error_code=None if loop_result.status != "failed" else "V3_AGENT_FAILED",
                trace={
                    **base_trace,
                    "elapsed_seconds": round(time.perf_counter() - started, 3),
                    "steps": loop_result.state.steps,
                    "stats": loop_result.stats,
                    "grounding": {**loop_result.grounding, "problems": loop_result.issues},
                    "forced_final": loop_result.forced_final,
                    "stages_ms": dict(stages),
                    "context_composition": context_composition,
                },
            )
        except MissionCancelledError:
            result = V3MissionResult(
                mission_id=mission_id, status="failed", query=query, as_of=as_of_dt,
                final_response="This question was cancelled.", limitations=["CANCELLED"], error_code="CANCELLED",
                trace={**base_trace, "elapsed_seconds": round(time.perf_counter() - started, 3)})
        except TimeoutError:
            result = V3MissionResult(
                mission_id=mission_id, status="failed", query=query, as_of=as_of_dt,
                final_response="The agent took too long to answer. Please retry.",
                limitations=["EXECUTION_LIMIT_EXCEEDED"], error_code="EXECUTION_LIMIT_EXCEEDED",
                trace={**base_trace, "elapsed_seconds": round(time.perf_counter() - started, 3)})
        except Exception as exc:
            message, error_code = _user_facing_agent_failure(exc)
            _log.warning("v3_agent_failed", exc_info=True, extra={"mission_id": mission_id, "error_code": error_code})
            result = V3MissionResult(
                mission_id=mission_id, status="failed", query=query, as_of=as_of_dt, final_response=message,
                limitations=[error_code], error_code=error_code,
                trace={**base_trace, "elapsed_seconds": round(time.perf_counter() - started, 3),
                       "failure": _failure_detail(exc)})
        if hasattr(m_trace, "set_output"):
            try:
                m_trace.set_output(result.final_response)
            except Exception:  # noqa: S110 - tracing must never fail the mission
                pass

    v3_store.finish(
        mission_id,
        status=result.status if result.status in {"completed", "partial", "failed", "cancelled"} else "partial",
        final_response=result.final_response,
        error_code=result.error_code,
    )
    if loop_result is not None and result.status in {"completed", "partial"}:
        _persist_turn(artifact_store, loop_result=loop_result, query=query, workspace_id=workspace_id,
                      thread_id=thread_id, mission_id=mission_id, as_of_dt=as_of_dt)

    raw = v3_raw_snapshot(mission_id) or {}
    evidence_views = [EvidenceView.model_validate(row) for row in (raw.get("evidence") or []) if isinstance(row, dict)]
    lookup = _to_lookup(result, request_id=request_id, session_id=thread_id, evidence=evidence_views)
    # Structured report parts (CHART / TABLE / KPI_GRID) ride on the persisted raw snapshot.
    extra_parts = list(getattr(result, "parts", None) or [])
    extra_charts = list(getattr(result, "charts", None) or raw.get("charts") or [])
    # Prefer the loop's full trace (stats / steps / grounding) over the stub
    # v3_raw_snapshot leaves — the Debug console and /trace endpoint read this.
    full_trace = dict(result.trace or {}) if isinstance(result.trace, dict) else {}
    runtime.store.put(
        lookup,
        {
            **raw,
            "workspace_id": workspace_id,
            "owner_user_id": owner_user_id,
            "thread_id": thread_id,
            "run_id": run_id,
            "parts": extra_parts or raw.get("parts") or [],
            "charts": extra_charts,
            "final_response": result.final_response or raw.get("final_response") or "",
            "status": result.status,
            "limitations": list(result.limitations or []),
            "trace": full_trace or raw.get("trace") or {},
        },
    )
    return {"route": "v3", "result": lookup.model_dump()}
