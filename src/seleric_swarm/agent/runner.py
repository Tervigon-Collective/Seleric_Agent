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
from seleric_swarm.services.metrics import MetricDefinition, MetricRegistry
from seleric_swarm.services.time_range import as_of_date
from seleric_swarm.state.missions import Mission
from seleric_swarm.toolsets.semantic import query_metrics

_LOOKUP_STATUSES = {
    "completed",
    "partial",
    "failed",
    "running",
    "cancelled",
    "blocked",
    "prototype_completed",
}

_ALIAS_REGISTRY: MetricRegistry | None = None


def _alias_registry() -> MetricRegistry:
    global _ALIAS_REGISTRY
    if _ALIAS_REGISTRY is None:
        _ALIAS_REGISTRY = MetricRegistry("config/metric_registry.yaml")
    return _ALIAS_REGISTRY


def _lookup_alias(query: str) -> MetricDefinition | None:
    """Exact YAML overlay only (``ns``/``np``/``adsp``/``gs``).

    Lives on the runner, not ``query_metrics``: Profile B requires two
    spellings of one metric to stay independently attributed at the tool.
    """
    return _alias_registry().resolve_alias(query)


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


def _mission_prompt(
    query: str,
    as_of_dt: datetime,
    timezone: str,
    context: ContextBundle | None = None,
) -> str:
    as_of_day = as_of_dt.date()
    yesterday = as_of_day.fromordinal(as_of_day.toordinal() - 1)
    thread = _thread_context_block(context)
    return (
        f"{thread}{query}\n\n"
        f"[system: mission as_of={as_of_day.isoformat()} timezone={timezone}. "
        f"'today' is {as_of_day.isoformat()}. "
        f"'yesterday' is {yesterday.isoformat()}. "
        f"Use only this calendar; do not invent another year from training data. "
        f"Use thread context for follow-ups; do not treat this as a brand-new chat.]"
    )


def _part_text(message: dict[str, Any]) -> str:
    chunks: list[str] = []
    for part in message.get("parts") or []:
        if not isinstance(part, dict):
            continue
        if str(part.get("type") or "").upper() != "TEXT":
            continue
        content = part.get("content")
        if isinstance(content, str) and content.strip():
            chunks.append(content.strip())
    return "\n".join(chunks)


def _thread_context_block(bundle: ContextBundle | None) -> str:
    """Prior turns / summary / memories already stored by conversations."""
    if bundle is None:
        return ""
    lines: list[str] = []
    summary = bundle.latest_summary.summary.strip() if bundle.latest_summary else ""
    if summary:
        lines.append(f"Summary: {summary[:500]}")
    for mem in bundle.memories[:6]:
        content = mem.content if isinstance(mem.content, str) else str(mem.content)
        text = content.strip()
        if not text:
            continue
        kind = getattr(mem.type, "value", mem.type)
        lines.append(f"Memory ({kind}): {text[:240]}")
    turns: list[str] = []
    for raw in bundle.recent_messages[-8:]:
        message = raw if isinstance(raw, dict) else {}
        text = _part_text(message)
        if not text:
            continue
        who = "User" if str(message.get("role") or "").upper() == "USER" else "Seleric"
        turns.append(f"{who}: {text[:400]}")
    if turns:
        lines.append("Recent turns:")
        lines.extend(turns)
    if not lines:
        return ""
    return "[thread context]\n" + "\n".join(lines) + "\n\n"


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


class _ToolCtx:
    def __init__(self, deps: SelericDeps) -> None:
        self.deps = deps


async def _alias_lookup_result(
    deps: SelericDeps,
    *,
    query: str,
    definition: Any,
    request_id: str,
    thread_id: str,
) -> V3MissionResult:
    """Live Cube lookup for an exact YAML alias — no LLM, no invented metric id."""
    catalogue_id = str(definition.catalogue_metric or definition.id.removeprefix("metric."))
    tool = await query_metrics(_ToolCtx(deps), metric_id=catalogue_id)
    value = None
    if tool.artifact_ids:
        artifact = deps.artifact_store.get(tool.artifact_ids[0])
        if artifact is not None and isinstance(artifact.payload, dict):
            value = artifact.payload.get("value")
    label = next(iter(definition.aliases), definition.id.removeprefix("metric."))
    unit = str(getattr(definition, "unit", "") or "").strip()
    if tool.success and value is not None:
        amount = f"{value:,.0f}" if isinstance(value, (int, float)) else str(value)
        answer = f"{label}: {amount} {unit}".strip()
        status = "completed"
        error_code = None
    else:
        answer = tool.summary or f"No live data for {label}."
        status = "failed"
        error_code = tool.error_code or "INSUFFICIENT_EVIDENCE"
    return V3MissionResult(
        mission_id=deps.mission_id,
        status=status,
        query=query,
        as_of=deps.as_of,
        final_response=answer,
        evidence_ids=list(tool.artifact_ids),
        limitations=[] if tool.success else [error_code or "INSUFFICIENT_EVIDENCE"],
        error_code=error_code,
        trace={"request_id": request_id, "session_id": thread_id, "lookup": "alias"},
    )


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
    ``run_mission_job`` / ``create_mission`` can call this with their full
    historical argument set after the swarm_v2 dispatcher was deleted.
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
    alias_def = _lookup_alias(query)
    with mission_trace(mission_id, query=query, route="v3"):
        try:
            if alias_def is not None:
                result = await _alias_lookup_result(
                    deps,
                    query=query,
                    definition=alias_def,
                    request_id=request_id,
                    thread_id=thread_id,
                )
            else:
                agent = build_seleric_agent(model=resolve_v3_model(runtime.settings))
                prompt = _mission_prompt(query, as_of_dt, timezone, context=deps.context)
                v3_result = await asyncio.wait_for(
                    run_validated_mission(agent, deps, prompt),
                    timeout=deps.limits.max_runtime_seconds,
                )
                result = v3_result.model_copy(
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
