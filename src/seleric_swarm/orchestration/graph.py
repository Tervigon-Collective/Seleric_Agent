"""LangGraph lookup_v1 orchestration.

Internal workflow only. A2A remains a typed envelope at process boundaries.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from seleric_swarm.agents.base import AgentContext
from seleric_swarm.agents.coordinator import Agent as CoordinatorAgent
from seleric_swarm.agents.domains.commerce import Agent as CommerceAgent
from seleric_swarm.agents.domains.performance import Agent as PerformanceAgent
from seleric_swarm.agents.intelligence.observer import Agent as ObserverAgent
from seleric_swarm.leadership.manager import LeadershipManager
from seleric_swarm.observability.tracing import mission_metadata, traced_span
from seleric_swarm.orchestration.state import MissionState
from seleric_swarm.orchestration.synthesize import synthesize_response
from seleric_swarm.runtime import SwarmRuntime
from seleric_swarm.services.claim_gate import validate_claim

V1_SUPPORTED = {
    ("lookup", "commerce_agent"),
    ("comparison", "commerce_agent"),
    ("lookup", "performance_agent"),
}
Route = Literal["domain_commerce", "domain_performance", "finalize_unsupported", "finalize_error"]
AfterObserver = Literal["propose_handoff", "claim_gate"]
AfterArbitrate = Literal["domain_commerce", "finalize_error"]


def _meta(runtime: SwarmRuntime, state: MissionState, agent_name: str) -> dict[str, Any]:
    return mission_metadata(
        request_id=str(state.get("request_id") or ""),
        session_id=str(state.get("session_id") or ""),
        mission_id=str(state.get("mission_id") or ""),
        workflow_name=runtime.settings.workflow_name,
        workflow_version=runtime.settings.workflow_version,
        agent_name=agent_name,
        agent_version=runtime.agents.version(agent_name),
        extra={
            "task_id": state.get("task_id"),
            "query_class": state.get("query_class"),
            "model": runtime.settings.azure_openai_model,
            "mission_lead": state.get("mission_lead"),
            "leadership_epoch": state.get("leadership_epoch"),
        },
    )


def _budget_ok(runtime: SwarmRuntime, state: MissionState, llm_needed: int = 0, tool_needed: int = 0) -> str | None:
    if int(state.get("llm_calls") or 0) + llm_needed > runtime.settings.max_llm_calls:
        return "BUDGET_EXCEEDED"
    if int(state.get("tool_calls") or 0) + tool_needed > runtime.settings.max_tool_calls:
        return "BUDGET_EXCEEDED"
    return None


def build_graph(runtime: SwarmRuntime):
    coordinator = CoordinatorAgent(runtime)
    commerce = CommerceAgent(runtime)
    performance = PerformanceAgent(runtime)
    observer = ObserverAgent(runtime)
    leadership = LeadershipManager()
    tracing = runtime.settings.langsmith_tracing

    async def coordinator_node(state: MissionState) -> dict[str, Any]:
        with traced_span("node.coordinator", _meta(runtime, state, "coordinator_agent"), tracing):
            ctx = AgentContext(
                mission_id=state["mission_id"],
                task_id=state.get("task_id") or f"T-{uuid4().hex[:8]}",
                question=state["user_query"],
                mission_lead="coordinator_agent",
                payload={
                    "timezone": state.get("timezone"),
                    "as_of": state.get("as_of"),
                    "request_id": state.get("request_id"),
                    "session_id": state.get("session_id"),
                },
            )
            over = _budget_ok(runtime, state, llm_needed=1)
            if over:
                return {"error_code": over, "error_message": "LLM call budget exceeded", "status": "failed"}
            result = await coordinator.classify(
                query=state["user_query"],
                timezone=state.get("timezone") or "Asia/Kolkata",
                as_of=state.get("as_of"),
                mission_id=state["mission_id"],
                request_id=str(state.get("request_id")),
                session_id=str(state.get("session_id")),
                task_id=ctx.task_id,
            )
            result["task_id"] = ctx.task_id
            result["llm_calls"] = int(state.get("llm_calls") or 0) + int(result.get("llm_calls") or 1)
            return result

    def route_after_coordinator(state: MissionState) -> Route:
        if state.get("error_code") in {"LLM_UNAVAILABLE", "BUDGET_EXCEEDED", "TIMEOUT"}:
            return "finalize_error"
        pair = (state.get("query_class"), state.get("mission_lead"))
        if pair == ("lookup", "performance_agent"):
            return "domain_performance"
        if pair in V1_SUPPORTED:
            return "domain_commerce"
        return "finalize_unsupported"

    async def domain_performance_node(state: MissionState) -> dict[str, Any]:
        with traced_span("node.performance", _meta(runtime, state, "performance_agent"), tracing):
            ctx = AgentContext(
                mission_id=state["mission_id"],
                task_id=state.get("task_id") or "",
                question=state["user_query"],
                mission_lead="performance_agent",
                payload={
                    "metric_hints": state.get("metric_hints") or [],
                    "metric_id": state.get("metric_id"),
                },
            )
            return await performance.run(ctx)

    async def domain_commerce_node(state: MissionState) -> dict[str, Any]:
        with traced_span("node.commerce", _meta(runtime, state, "commerce_agent"), tracing):
            ctx = AgentContext(
                mission_id=state["mission_id"],
                task_id=state.get("task_id") or "",
                question=state["user_query"],
                mission_lead="commerce_agent",
                payload={
                    "metric_hints": state.get("metric_hints") or [],
                    "metric_id": state.get("metric_id"),
                },
            )
            result = await commerce.run(ctx)
            if result.get("error_code"):
                return result
            return result

    async def observer_node(state: MissionState) -> dict[str, Any]:
        with traced_span("node.observer", _meta(runtime, state, "observer_agent"), tracing):
            over = _budget_ok(runtime, state, llm_needed=1, tool_needed=1)
            if over:
                return {"error_code": over, "error_message": "Observer budget exceeded", "status": "failed"}
            lead = state.get("mission_lead") or "commerce_agent"
            tool_name = (
                "performance.daily_cac" if lead == "performance_agent" else "commerce.daily_sales"
            )
            with traced_span(
                f"tool.mcp.{tool_name}",
                {**_meta(runtime, state, "observer_agent"), "tool_name": tool_name},
                tracing,
            ):
                ctx = AgentContext(
                    mission_id=state["mission_id"],
                    task_id=state.get("task_id") or "",
                    question=state["user_query"],
                    mission_lead=lead,
                    payload={
                        "request_id": state.get("request_id"),
                        "session_id": state.get("session_id"),
                        "allowed_metrics": state.get("allowed_metrics")
                        or ["metric.net_sales", "metric.gross_sales"],
                        "metric_hints": state.get("metric_hints") or [],
                        "metric_id": state.get("metric_id"),
                        "time_range": state.get("time_range") or {},
                        "query_class": state.get("query_class"),
                    },
                )
                result = await observer.observe(ctx)
            prior = list(state.get("evidence") or [])
            new_rows = list(result.get("evidence") or [])
            merged = prior + new_rows
            result["evidence"] = merged
            result["evidence_refs"] = [row["evidence_id"] for row in merged]
            result["llm_calls"] = int(state.get("llm_calls") or 0) + int(result.get("llm_calls") or 0)
            result["tool_calls"] = int(state.get("tool_calls") or 0) + int(result.get("tool_calls") or 0)
            result["mcp_called"] = bool(result.get("mcp_called") or state.get("mcp_called"))
            prior_limits = list(state.get("limitations") or [])
            new_limits = list(result.get("limitations") or [])
            result["limitations"] = prior_limits + [item for item in new_limits if item not in prior_limits]
            if result.get("error_code") == "INSUFFICIENT_EVIDENCE" and not new_rows:
                result["status"] = "failed"
            return result

    def route_after_observer(state: MissionState) -> AfterObserver:
        needed = list(state.get("handoff_needed_metrics") or [])
        if (
            needed
            and state.get("mission_lead") == "performance_agent"
            and state.get("evidence")
            and state.get("error_code") != "INSUFFICIENT_EVIDENCE"
        ):
            return "propose_handoff"
        return "claim_gate"

    def propose_handoff_node(state: MissionState) -> dict[str, Any]:
        with traced_span("node.leadership_transfer", _meta(runtime, state, "performance_agent"), tracing):
            needed = list(state.get("handoff_needed_metrics") or [])
            refs = [row["evidence_id"] for row in (state.get("evidence") or [])]
            return {
                "pending_transfer": {
                    "mission_id": state.get("mission_id"),
                    "from_agent": "performance_agent",
                    "to_agent": "commerce_agent",
                    "requested_target": "commerce_agent",
                    "reason": (
                        "Performance owns CAC but the unresolved question requires "
                        f"{', '.join(needed)}, which is a commerce capability."
                    ),
                    "evidence_refs": refs,
                    "unresolved_question": f"Retrieve {', '.join(needed)} for the same time range",
                    "requested_output": "EvidenceBundle for commerce metrics",
                }
            }

    def coordinator_arbitrate_node(state: MissionState) -> dict[str, Any]:
        with traced_span("node.coordinator_arbitrate", _meta(runtime, state, "coordinator_agent"), tracing):
            proposal = dict(state.get("pending_transfer") or {})
            decision = leadership.decide(dict(state), proposal)
            if not decision.get("accepted"):
                return {
                    "error_code": decision.get("error_code") or "HANDOFF_REJECTED",
                    "error_message": decision.get("error_message") or "Handoff rejected",
                    "status": "failed",
                    "pending_transfer": None,
                }
            return {
                "mission_lead": decision["mission_lead"],
                "leadership_epoch": decision["leadership_epoch"],
                "handoff_history": decision["handoff_history"],
                "handoff_needed_metrics": [],
                "pending_transfer": None,
                "metric_id": (state.get("handoff_needed_metrics") or ["metric.net_sales"])[0],
                "error_code": None,
            }

    def route_after_arbitrate(state: MissionState) -> AfterArbitrate:
        if state.get("error_code") or state.get("mission_lead") != "commerce_agent":
            return "finalize_error"
        return "domain_commerce"

    def claim_gate_node(state: MissionState) -> dict[str, Any]:
        with traced_span("node.claim_gate", _meta(runtime, state, "claim_gate"), tracing):
            if state.get("error_code") == "INSUFFICIENT_EVIDENCE" and not state.get("evidence"):
                return {"claims": [], "status": "failed"}
            claims: list[dict[str, Any]] = []
            for ev in state.get("evidence") or []:
                day = (ev.get("time_range") or {}).get("start")
                unit = ev.get("unit") or ""
                metric = ev.get("metric_or_fact")
                value = ev.get("value")
                text = f"{metric} was {value} {unit}".strip()
                if day:
                    text += f" on {day}"
                claim = {
                    "claim_id": f"CL-{uuid4().hex[:10]}",
                    "claim_type": "numeric",
                    "text": text,
                    "support_refs": [ev["evidence_id"]],
                    "contradiction_refs": [],
                    "trust_label": "VERIFIED",
                    "gate_status": "pending",
                }
                ok, problems = validate_claim(claim)
                claim["gate_status"] = "passed" if ok else "rejected"
                if not ok:
                    return {
                        "claims": [claim],
                        "error_code": "CLAIM_REJECTED",
                        "error_message": "; ".join(problems),
                        "status": "failed",
                    }
                claims.append(claim)
            if not claims:
                return {
                    "claims": [],
                    "error_code": "INSUFFICIENT_EVIDENCE",
                    "error_message": "No claims passed the provenance gate",
                    "status": "failed",
                }
            return {"claims": claims}

    async def synthesize_node(state: MissionState) -> dict[str, Any]:
        with traced_span("node.synthesizer", _meta(runtime, state, "response_synthesizer"), tracing):
            if state.get("error_code") and not state.get("claims"):
                return {}
            over = _budget_ok(runtime, state, llm_needed=1)
            if over:
                from seleric_swarm.orchestration.synthesize import _table_fallback

                return {
                    "final_response": _table_fallback(state.get("evidence") or [], state.get("claims") or []),
                    "synthesis_fallback": True,
                    "error_code": None,
                }
            result = await synthesize_response(runtime, dict(state))
            result["llm_calls"] = int(state.get("llm_calls") or 0) + int(result.get("llm_calls") or 0)
            if result.get("limitations"):
                merged = list(state.get("limitations") or []) + [
                    item for item in result["limitations"] if item not in (state.get("limitations") or [])
                ]
                result["limitations"] = merged
            if state.get("status") == "partial":
                result["status"] = "partial"
            else:
                result["status"] = "completed" if state.get("claims") else state.get("status") or "failed"
            return result

    def finalize_unsupported_node(state: MissionState) -> dict[str, Any]:
        with traced_span("node.finalize", _meta(runtime, state, "coordinator_agent"), tracing):
            reason = state.get("unsupported_reason") or "Query class or domain is not enabled in V1"
            return {
                "status": "failed",
                "error_code": state.get("error_code") or "ROUTING_UNSUPPORTED",
                "error_message": reason,
                "mcp_called": False,
                "final_response": reason,
                "limitations": [reason],
                "active_specialist": None,
            }

    def finalize_error_node(state: MissionState) -> dict[str, Any]:
        with traced_span("node.finalize", _meta(runtime, state, "coordinator_agent"), tracing):
            code = state.get("error_code") or "LLM_UNAVAILABLE"
            message = state.get("error_message") or "Mission failed"
            return {
                "status": "failed",
                "error_code": code,
                "error_message": message,
                "final_response": message,
                "limitations": list(state.get("limitations") or [message]),
            }

    def finalize_success_node(state: MissionState) -> dict[str, Any]:
        with traced_span("node.finalize", _meta(runtime, state, "coordinator_agent"), tracing):
            status = state.get("status") or "completed"
            if state.get("error_code") == "INSUFFICIENT_EVIDENCE" and not state.get("claims"):
                status = "failed"
            return {"status": status}

    graph = StateGraph(MissionState)
    graph.add_node("coordinator", coordinator_node)
    graph.add_node("domain_performance", domain_performance_node)
    graph.add_node("domain_commerce", domain_commerce_node)
    graph.add_node("observer", observer_node)
    graph.add_node("propose_handoff", propose_handoff_node)
    graph.add_node("coordinator_arbitrate", coordinator_arbitrate_node)
    graph.add_node("claim_gate", claim_gate_node)
    graph.add_node("synthesize", synthesize_node)
    graph.add_node("finalize_unsupported", finalize_unsupported_node)
    graph.add_node("finalize_error", finalize_error_node)
    graph.add_node("finalize", finalize_success_node)
    graph.add_edge(START, "coordinator")
    graph.add_conditional_edges(
        "coordinator",
        route_after_coordinator,
        {
            "domain_commerce": "domain_commerce",
            "domain_performance": "domain_performance",
            "finalize_unsupported": "finalize_unsupported",
            "finalize_error": "finalize_error",
        },
    )
    graph.add_edge("domain_performance", "observer")
    graph.add_edge("domain_commerce", "observer")
    graph.add_conditional_edges(
        "observer",
        route_after_observer,
        {"propose_handoff": "propose_handoff", "claim_gate": "claim_gate"},
    )
    graph.add_edge("propose_handoff", "coordinator_arbitrate")
    graph.add_conditional_edges(
        "coordinator_arbitrate",
        route_after_arbitrate,
        {"domain_commerce": "domain_commerce", "finalize_error": "finalize_error"},
    )
    graph.add_edge("claim_gate", "synthesize")
    graph.add_edge("synthesize", "finalize")
    graph.add_edge("finalize", END)
    graph.add_edge("finalize_unsupported", END)
    graph.add_edge("finalize_error", END)
    return graph.compile()
