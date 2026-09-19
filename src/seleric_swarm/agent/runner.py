"""Run one V3 mission and persist it where the UI already reads.

Conversations (``api/conversations.py``) pick ``final_response`` off the
swarm_v2 mission store after ``run_mission_job``. The Office UI reads
``GET /v1/office/missions*``. Both stay unchanged: this runner writes the
V3 result into those existing shapes (via ``v3_adapter``) and into the V3
stores the office gateway falls back to.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from seleric_swarm.agent.agent import build_seleric_agent
from seleric_swarm.agent.dependencies import ExecutionLimits, NullMcpClient, SelericDeps
from seleric_swarm.agent.model import resolve_v3_model
from seleric_swarm.agent.output import MissionResult as V3MissionResult
from seleric_swarm.agent.validation import run_validated_mission
from seleric_swarm.api.office.registry import register_mission
from seleric_swarm.api.office.v3_adapter import v3_raw_snapshot
from seleric_swarm.api.v3_state import get_v3_artifact_store, get_v3_mission_store
from seleric_swarm.contracts.lookup import (
    EvidenceView,
    MissionError,
    TraceInfo,
)
from seleric_swarm.contracts.lookup import (
    MissionResult as LookupMissionResult,
)
from seleric_swarm.conversations.contracts import ContextBundle, Principal, PrincipalAuthMethod
from seleric_swarm.observability.traces import mission_trace
from seleric_swarm.runtime import SwarmRuntime
from seleric_swarm.services.time_range import as_of_date
from seleric_swarm.state.missions import Mission

_LOOKUP_STATUSES = {
    "completed",
    "partial",
    "failed",
    "running",
    "cancelled",
    "blocked",
    "prototype_completed",
}


def _user_facing_agent_failure(exc: BaseException) -> tuple[str, str]:
    """Chat-safe failure text — never dump provider HTTP bodies to the UI."""
    text = str(exc)
    lowered = text.lower()
    if "429" in text or "ratelimit" in lowered or "rate limit" in lowered:
        return (
            "The language model is rate-limited right now. Please retry in a moment.",
            "LLM_RATE_LIMITED",
        )
    if "timeout" in lowered:
        return ("The agent timed out. Please retry.", "V3_AGENT_TIMEOUT")
    return (
        "The agent could not complete this question. Please retry.",
        "V3_AGENT_FAILED",
    )


def _as_of_datetime(as_of: str | None, timezone: str = "Asia/Kolkata") -> datetime:
    """Anchor as_of on the mission calendar day, not UTC-now (which can lag IST)."""
    day = as_of_date(as_of, timezone)
    try:
        tz = ZoneInfo(timezone)
    except ZoneInfoNotFoundError:
        tz = UTC
    return datetime(day.year, day.month, day.day, tzinfo=tz)


def _mission_prompt(query: str, as_of_dt: datetime, timezone: str) -> str:
    as_of_day = as_of_dt.date()
    yesterday = as_of_day.fromordinal(as_of_day.toordinal() - 1)
    return (
        f"{query}\n\n"
        f"[system: mission as_of={as_of_day.isoformat()} timezone={timezone}. "
        f"'today' is {as_of_day.isoformat()}. "
        f"'yesterday' is {yesterday.isoformat()}. "
        f"Use only this calendar; do not invent another year from training data.]"
    )


def _context_bundle(raw: dict[str, Any] | None) -> ContextBundle:
    if not raw:
        return ContextBundle()
    try:
        return ContextBundle.model_validate(raw)
    except Exception:
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
    return status if status in _LOOKUP_STATUSES else "partial"


def _to_lookup(
    result: V3MissionResult,
    *,
    request_id: str,
    session_id: str,
    evidence: list[EvidenceView],
) -> LookupMissionResult:
    error = None
    if result.error_code:
        error = MissionError(code=result.error_code, message=result.final_response or result.error_code)
    return LookupMissionResult(
        mission_id=result.mission_id,
        status=_lookup_status(result.status),  # type: ignore[arg-type]
        mission_lead="coordinator",
        initial_mission_lead="coordinator",
        evidence=evidence,
        limitations=list(result.limitations),
        final_response=result.final_response,
        error=error,
        trace=TraceInfo(
            request_id=str(result.trace.get("request_id") or request_id),
            session_id=str(result.trace.get("session_id") or session_id),
        ),
    )


async def run_v3_mission(
    runtime: SwarmRuntime,
    *,
    query: str,
    timezone: str = "Asia/Kolkata",
    as_of: str | None = None,
    session_id: str | None = None,
    request_id: str | None = None,
    mission_id: str | None = None,
    context_bundle: dict[str, Any] | None = None,
    workspace_id: str | None = None,
    owner_user_id: str | None = None,
    thread_id: str | None = None,
    run_id: str | None = None,
    **_ignored: Any,
) -> dict[str, Any]:
    """Execute the V3 agent and persist for conversations + Office UI.

    Extra kwargs (``full_diagnostic`` etc.) are accepted and ignored so
    ``run_mission_job`` / ``create_mission`` can call this with the same
    argument set they pass ``run_any_mission``.
    """
    mission_id = mission_id or f"MS3-{uuid4().hex[:10]}"
    request_id = request_id or uuid4().hex
    thread_id = thread_id or session_id or uuid4().hex
    run_id = run_id or request_id
    workspace_id = workspace_id or getattr(runtime.settings, "default_workspace_id", "default")
    owner_user_id = owner_user_id or getattr(runtime.settings, "default_user_id", "default")
    as_of_dt = _as_of_datetime(as_of, timezone)
    register_mission(mission_id)

    v3_store = get_v3_mission_store()
    if v3_store.get(mission_id) is None:
        v3_store.create(
            Mission(
                mission_id=mission_id,
                query=query,
                as_of=as_of_dt,
                workspace_id=workspace_id,
                owner_user_id=owner_user_id,
                thread_id=thread_id,
                run_id=run_id,
            )
        )

    mcp = getattr(runtime, "mcp", None) or NullMcpClient()
    deps = SelericDeps(
        mission_id=mission_id,
        as_of=as_of_dt,
        principal=_principal(workspace_id=workspace_id, user_id=owner_user_id),
        thread_id=thread_id,
        run_id=run_id,
        trace_id=request_id,
        context=_context_bundle(context_bundle),
        mcp_client=mcp,
        artifact_store=get_v3_artifact_store(),
        limits=ExecutionLimits(
            max_tool_calls=int(getattr(runtime.settings, "max_tool_calls", 8)),
            max_runtime_seconds=min(
                45.0, float(getattr(runtime.settings, "mission_timeout_s", 120.0))
            ),
        ),
    )
    agent = build_seleric_agent(model=resolve_v3_model(runtime.settings))
    prompt = _mission_prompt(query, as_of_dt, timezone)
    with mission_trace(mission_id, query=query, route="v3"):
        try:
            # run_validated_mission (not a bare agent.run()) so the bounded
            # 1-revision retry loop (agent/validation.py, non-negotiable
            # rule 11) and the execution-limit tracker (agent/limits.py) are
            # actually exercised on this live path — without it, a
            # status="completed" mission with an empty final_response can
            # reach a real user (observed 2026-09-19, see TASK_SHEET.md).
            v3_result = await asyncio.wait_for(
                run_validated_mission(agent, deps, prompt),
                timeout=deps.limits.max_runtime_seconds,
            )
            result: V3MissionResult = v3_result.model_copy(
                update={
                    "mission_id": mission_id,
                    "query": query,
                    "as_of": as_of_dt,
                    "trace": {"request_id": request_id, "session_id": thread_id},
                }
            )
        except TimeoutError:
            result = V3MissionResult(
                mission_id=mission_id,
                status="failed",
                query=query,
                as_of=as_of_dt,
                final_response="The agent took too long to answer. Please retry.",
                limitations=["EXECUTION_LIMIT_EXCEEDED"],
                error_code="EXECUTION_LIMIT_EXCEEDED",
                trace={"request_id": request_id, "session_id": thread_id},
            )
        except Exception as exc:
            message, error_code = _user_facing_agent_failure(exc)
            result = V3MissionResult(
                mission_id=mission_id,
                status="failed",
                query=query,
                as_of=as_of_dt,
                final_response=message,
                limitations=[error_code],
                error_code=error_code,
                trace={"request_id": request_id, "session_id": thread_id},
            )

    v3_store.finish(
        mission_id,
        status=result.status if result.status in {"completed", "partial", "failed", "cancelled"} else "partial",
        final_response=result.final_response,
        error_code=result.error_code,
    )
    raw = v3_raw_snapshot(mission_id) or {}
    evidence_views = [
        EvidenceView.model_validate(row)
        for row in (raw.get("evidence") or [])
        if isinstance(row, dict)
    ]
    lookup = _to_lookup(
        result, request_id=request_id, session_id=thread_id, evidence=evidence_views
    )
    runtime.store.put(
        lookup,
        {
            **raw,
            "workspace_id": workspace_id,
            "owner_user_id": owner_user_id,
            "thread_id": thread_id,
            "run_id": run_id,
        },
    )
    return {"route": "v3", "result": lookup.model_dump()}
