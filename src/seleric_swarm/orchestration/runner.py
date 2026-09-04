from __future__ import annotations

import asyncio
from uuid import uuid4

from seleric_swarm.contracts.lookup import (
    ClaimView,
    EvidenceView,
    HandoffView,
    MissionError,
    MissionResult,
    TraceInfo,
)
from seleric_swarm.observability.tracing import langsmith_run_url, mission_metadata, traced_span
from seleric_swarm.orchestration.graph import build_graph
from seleric_swarm.orchestration.state import MissionState
from seleric_swarm.runtime import SwarmRuntime


def _result_from_state(runtime: SwarmRuntime, state: MissionState) -> MissionResult:
    error = None
    code = state.get("error_code")
    if code:
        error = MissionError(code=code, message=state.get("error_message") or code)
    run_id = state.get("langsmith_run_id")
    url = None
    if runtime.settings.app_env != "production":
        url = langsmith_run_url(
            runtime.settings.langsmith_project, run_id, runtime.settings.langsmith_org
        )
    return MissionResult(
        mission_id=state["mission_id"],
        status=state.get("status") or "failed",
        query_class=state.get("query_class"),
        mission_lead=state.get("mission_lead"),
        initial_mission_lead=state.get("initial_mission_lead"),
        active_specialist=state.get("active_specialist"),
        leadership_epoch=int(state.get("leadership_epoch") or 0),
        handoff_history=[HandoffView.model_validate(h) for h in state.get("handoff_history") or []],
        claims=[ClaimView.model_validate(c) for c in state.get("claims") or []],
        evidence=[
            EvidenceView(
                evidence_id=row["evidence_id"],
                metric_or_fact=row["metric_or_fact"],
                value=row.get("value"),
                unit=row.get("unit"),
                time_range=row.get("time_range") or {},
                source=row.get("source", ""),
                freshness=row.get("freshness"),
                dimensions=row.get("dimensions") or {},
                provenance=row.get("provenance") or {},
            )
            for row in state.get("evidence") or []
        ],
        limitations=list(state.get("limitations") or []),
        final_response=state.get("final_response"),
        error=error,
        trace=TraceInfo(
            request_id=str(state.get("request_id")),
            session_id=str(state.get("session_id")),
            langsmith_run_id=run_id,
            langsmith_run_url=url,
        ),
    )


async def run_mission(
    runtime: SwarmRuntime,
    *,
    query: str,
    timezone: str = "Asia/Kolkata",
    as_of: str | None = None,
    mode: str = "read_only",
    session_id: str | None = None,
    request_id: str | None = None,
) -> MissionResult:
    mission_id = f"M-{uuid4().hex[:10]}"
    rid = request_id or uuid4().hex
    sid = session_id or uuid4().hex
    if mode != "read_only":
        result = MissionResult(
            mission_id=mission_id,
            status="failed",
            error=MissionError(code="INVALID_REQUEST", message="Only read_only mode is allowed in V1"),
            trace=TraceInfo(request_id=rid, session_id=sid),
        )
        runtime.store.put(result, {"user_query": query})
        return result

    initial: MissionState = {
        "mission_id": mission_id,
        "request_id": rid,
        "session_id": sid,
        "user_query": query,
        "timezone": timezone,
        "as_of": as_of,
        "mode": mode,
        "workflow_name": runtime.settings.workflow_name,
        "workflow_version": runtime.settings.workflow_version,
        "status": "running",
        "leadership_epoch": 0,
        "llm_calls": 0,
        "tool_calls": 0,
        "mcp_called": False,
        "evidence": [],
        "claims": [],
        "limitations": [],
        "handoff_history": [],
        "handoff_needed_metrics": [],
    }
    graph = build_graph(runtime)
    metadata = mission_metadata(
        request_id=rid,
        session_id=sid,
        mission_id=mission_id,
        workflow_name=runtime.settings.workflow_name,
        workflow_version=runtime.settings.workflow_version,
        agent_name="coordinator_agent",
        agent_version=runtime.agents.version("coordinator_agent"),
        extra={"model": runtime.settings.azure_openai_model},
    )
    final_state: MissionState = initial
    try:
        with traced_span(
            "mission.lookup_v1",
            metadata,
            runtime.settings.langsmith_tracing,
            inputs={"query": query, "timezone": timezone, "as_of": as_of, "mode": mode},
        ) as mission_span:
            try:
                from langsmith.run_helpers import get_current_run_tree

                tree = get_current_run_tree()
                if tree is not None and getattr(tree, "id", None):
                    initial["langsmith_run_id"] = str(tree.id)
            except Exception:
                pass
            try:
                final_state = await asyncio.wait_for(
                    graph.ainvoke(initial),
                    timeout=runtime.settings.mission_timeout_s,
                )
            except TimeoutError:
                final_state = {
                    **initial,
                    "status": "failed",
                    "error_code": "TIMEOUT",
                    "error_message": "Mission exceeded the configured timeout",
                    "final_response": "Mission exceeded the configured timeout",
                    "limitations": ["Mission exceeded the configured timeout"],
                }
            mission_span.set_outputs(
                {
                    "status": final_state.get("status"),
                    "query_class": final_state.get("query_class"),
                    "mission_lead": final_state.get("mission_lead"),
                    "initial_mission_lead": final_state.get("initial_mission_lead"),
                    "leadership_epoch": final_state.get("leadership_epoch"),
                    "complexity": final_state.get("complexity_label"),
                    "completion_score": final_state.get("completion_score"),
                    "completion_decision": final_state.get("completion_decision"),
                    "evidence_refs": final_state.get("evidence_refs"),
                    "error_code": final_state.get("error_code"),
                    "final_response": final_state.get("final_response"),
                }
            )
    except Exception as exc:
        final_state = {
            **initial,
            "status": "failed",
            "error_code": "LLM_UNAVAILABLE",
            "error_message": f"Mission failed without crashing the API: {exc}",
            "final_response": "Mission failed due to an internal error",
            "limitations": [str(exc)],
        }

    result = _result_from_state(runtime, final_state)
    runtime.store.put(result, dict(final_state))
    return result
