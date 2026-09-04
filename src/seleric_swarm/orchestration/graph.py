"""LangGraph lookup_v1 orchestration.

Internal workflow only. A2A remains a typed envelope at process boundaries.
"""

from __future__ import annotations

import importlib
from collections.abc import Hashable
from typing import Any, Literal
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from seleric_swarm.agents.base import AgentContext, SwarmAgent
from seleric_swarm.agents.coordinator import Agent as CoordinatorAgent
from seleric_swarm.agents.intelligence.observer import Agent as ObserverAgent
from seleric_swarm.coordinator import ControlPlane
from seleric_swarm.coordinator.execution.lookup_dag import (
    dag_progress_summary,
    mark_ready_tasks_done,
    mark_tasks,
    next_domain_lead,
    route_from_plan,
)
from seleric_swarm.leadership.manager import LeadershipManager
from seleric_swarm.observability.tracing import mission_metadata, traced_span
from seleric_swarm.orchestration.state import MissionState
from seleric_swarm.orchestration.synthesize import synthesize_response
from seleric_swarm.runtime import SwarmRuntime
from seleric_swarm.services.claim_gate import validate_claim

QUERY_CLASSES_SUPPORTED = {"lookup", "comparison"}
AfterObserver = Literal["propose_handoff", "claim_gate"]


def _emit_lookup_event(state: MissionState | dict[str, Any], kind: str, **data: Any) -> dict[str, Any]:
    """Append a structured event onto lookup MissionState (for GET .../events)."""
    from seleric_swarm.coordinator.observability.events import canonical_kind, family_of, now_iso

    events = list(state.get("events") or [])
    canon = canonical_kind(kind)
    payload = {k: v for k, v in data.items() if v is not None}
    if kind != canon:
        payload["legacy_kind"] = kind
    events.append(
        {
            "kind": canon,
            "ts": now_iso(),
            "seq": len(events) + 1,
            "mission_id": state.get("mission_id"),
            "workflow_name": "lookup_v1",
            "workflow_version": state.get("workflow_version") or "1.0.0",
            "family": family_of(canon),
            **payload,
        }
    )
    return {"events": events}


def _load_domain_agent(agent_id: str, runtime: SwarmRuntime) -> SwarmAgent:
    """Import agents/domains/<name>.py by convention -- adding a domain agent to
    config/agent_registry.yaml with enabled: true is enough to wire it in."""

    module_name = agent_id.removesuffix("_agent")
    module = importlib.import_module(f"seleric_swarm.agents.domains.{module_name}")
    return module.Agent(runtime)


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


def build_graph(runtime: SwarmRuntime):
    coordinator = CoordinatorAgent(runtime)
    observer = ObserverAgent(runtime)
    leadership = LeadershipManager()
    plane = ControlPlane(runtime)
    tracing = runtime.settings.langsmith_tracing

    domain_ids = [a["id"] for a in runtime.agents.domain_agents(enabled_only=True)]
    domain_agents: dict[str, SwarmAgent] = {aid: _load_domain_agent(aid, runtime) for aid in domain_ids}
    domain_node_names: dict[str, str] = {aid: f"domain_{aid}" for aid in domain_ids}

    def _budget_ok(
        _runtime: SwarmRuntime, state: MissionState, llm_needed: int = 0, tool_needed: int = 0
    ) -> str | None:
        ok, code, _reason = plane.budget_verdict(
            dict(state), llm_needed=llm_needed, tool_needed=tool_needed
        )
        return None if ok else (code or "BUDGET_EXCEEDED")

    async def coordinator_node(state: MissionState) -> dict[str, Any]:
        with traced_span(
            "node.coordinator",
            _meta(runtime, state, "coordinator_agent"),
            tracing,
            inputs={"query": state["user_query"], "timezone": state.get("timezone"), "as_of": state.get("as_of")},
        ) as coord_span:
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
            # The coordinator node owns the (future) DECIDE -> EXECUTE cycle, so it
            # is the one place that checks the iteration / transfer / agent-call
            # hard stops. Per-node checks stay spend-only.
            hs_ok, hs_code, hs_reason = plane.hard_stop_verdict(dict(state))
            if not hs_ok:
                coord_span.set_outputs({"error_code": hs_code, "reason": hs_reason})
                return {"error_code": hs_code, "error_message": hs_reason, "status": "failed"}
            over = _budget_ok(runtime, state, llm_needed=1)
            if over:
                coord_span.set_outputs({"error_code": over, "reason": "LLM call budget exceeded"})
                return {"error_code": over, "error_message": "LLM call budget exceeded", "status": "failed"}

            # --- Understand: classify the business question -------------------
            with traced_span(
                "coordinator.classify",
                _meta(runtime, state, "coordinator_agent"),
                tracing,
                inputs={"query": state["user_query"]},
            ) as classify_span:
                result = await coordinator.classify(
                    query=state["user_query"],
                    timezone=state.get("timezone") or "Asia/Kolkata",
                    as_of=state.get("as_of"),
                    mission_id=state["mission_id"],
                    request_id=str(state.get("request_id")),
                    session_id=str(state.get("session_id")),
                    task_id=ctx.task_id,
                )
                classify_span.set_outputs(
                    {
                        "query_class": result.get("query_class"),
                        "mission_lead": result.get("mission_lead"),
                        "metric_hints": result.get("metric_hints"),
                        "entities": result.get("entities"),
                        "time_range": result.get("time_range"),
                        "unsupported_reason": result.get("unsupported_reason"),
                    }
                )
            result["task_id"] = ctx.task_id
            result["llm_calls"] = int(state.get("llm_calls") or 0) + int(result.get("llm_calls") or 1)
            result["coordinator_iterations"] = int(state.get("coordinator_iterations") or 0) + 1

            # --- Plan: complexity, decomposition, DAG, lead, dispatchability --
            # Deterministic control plane; never overrides the classifier's lead.
            if not result.get("error_code"):
                with traced_span(
                    "coordinator.plan",
                    _meta(runtime, state, "coordinator_agent"),
                    tracing,
                    inputs={
                        "query_class": result.get("query_class"),
                        "metric_hints": result.get("metric_hints"),
                        "llm_domain_lead": result.get("mission_lead"),
                    },
                ) as plan_span:
                    plan = plane.plan(
                        query=state["user_query"],
                        query_class=str(result.get("query_class") or "unsupported"),
                        llm_domain_lead=result.get("mission_lead"),
                        metric_hints=list(result.get("metric_hints") or []),
                        entities=list(result.get("entities") or []),
                    )
                    result.update(plan)
                    # Authoritative lead comes from ControlPlane lead_selection.
                    lead_sel = plan.get("lead_selection") or {}
                    if lead_sel.get("mission_lead"):
                        result["mission_lead"] = lead_sel["mission_lead"]
                        result.setdefault("initial_mission_lead", lead_sel["mission_lead"])
                    if result.get("query_class") == "unsupported":
                        result["unsupported_reason"] = plane.unsupported_reason(
                            plan,
                            result.get("unsupported_reason")
                            or "Query class or domain is not enabled in V1",
                        )
                    # Non-dispatchable supported-class plans also need an explicit reason.
                    elif plan.get("plan_dispatchable") is False and not result.get("unsupported_reason"):
                        result["unsupported_reason"] = plane.unsupported_reason(
                            plan,
                            "Mission plan is not dispatchable",
                        )
                    # Human-readable decomposition here; full per-task detail is
                    # on the coordinator.plan.task.* child spans, so don't repeat
                    # the whole DAG dict (keeps trace volume down).
                    plan_span.set_outputs(
                        {
                            "complexity": plan["complexity_label"],
                            "lead_selection": plan["lead_selection"],
                            "decomposed_questions": plan["decomposed_questions"],
                            "plan_dispatchable": plan["plan_dispatchable"],
                            "blocked_reasons": plan["plan_blocked_reasons"],
                            "dag_notes": plan["task_graph"].get("notes"),
                        }
                    )
                    # One span per planned task so the run tree shows every
                    # decomposed question and its dispatch verdict.
                    for task in plan["task_graph"]["tasks"]:
                        with traced_span(
                            f"coordinator.plan.task.{task['id']}",
                            {**_meta(runtime, state, "coordinator_agent"), "task_type": task["type"]},
                            tracing,
                            run_type="tool",
                            inputs={
                                "question": task["objective"],
                                "depends_on": task["depends_on"],
                                "required_capabilities": task["required_capabilities"],
                                "metric_ids": task["metric_ids"],
                            },
                        ) as task_span:
                            task_span.set_outputs(
                                {
                                    "assigned_agent": task["assigned_agent"],
                                    "dispatchable": task["dispatchable"],
                                    "blocked_reason": task["blocked_reason"],
                                    "status": task["status"],
                                }
                            )

            coord_span.set_outputs(
                {
                    "query_class": result.get("query_class"),
                    "mission_lead": result.get("mission_lead"),
                    "complexity": result.get("complexity_label"),
                    "plan_dispatchable": result.get("plan_dispatchable"),
                    "dag_authority": True,
                    "route_hint": (result.get("query_class"), result.get("mission_lead")),
                    "unsupported_reason": result.get("unsupported_reason"),
                }
            )
            result.update(
                _emit_lookup_event(
                    {**dict(state), **result},
                    "mission_created",
                    query_class=result.get("query_class"),
                    mission_lead=result.get("mission_lead"),
                    plan_dispatchable=result.get("plan_dispatchable"),
                )
            )
            return result

    def route_after_coordinator(state: MissionState) -> str:
        return route_from_plan(
            dict(state),
            supported_classes=QUERY_CLASSES_SUPPORTED,
            domain_node_names=domain_node_names,
        )

    def _make_domain_node(agent_id: str, agent: SwarmAgent):
        async def _domain_node(state: MissionState) -> dict[str, Any]:
            with traced_span(
                f"node.{agent_id}",
                _meta(runtime, state, agent_id),
                tracing,
                inputs={"metric_hints": state.get("metric_hints"), "metric_id": state.get("metric_id")},
            ) as span:
                ctx = AgentContext(
                    mission_id=state["mission_id"],
                    task_id=state.get("task_id") or "",
                    question=state["user_query"],
                    mission_lead=agent_id,
                    payload={
                        "metric_hints": state.get("metric_hints") or [],
                        "metric_id": state.get("metric_id"),
                        "fetched_metrics": [
                            row.get("metric_or_fact")
                            for row in (state.get("evidence") or [])
                            if row.get("metric_or_fact")
                        ],
                    },
                )
                result = await agent.run(ctx)
                span.set_outputs(
                    {
                        "metric_id": result.get("metric_id"),
                        "allowed_metrics": result.get("allowed_metrics"),
                        "handoff_needed_metrics": result.get("handoff_needed_metrics"),
                        "error_code": result.get("error_code"),
                    }
                )
                return result

        return _domain_node

    async def observer_node(state: MissionState) -> dict[str, Any]:
        with traced_span(
            "node.observer",
            _meta(runtime, state, "observer_agent"),
            tracing,
            inputs={
                "mission_lead": state.get("mission_lead"),
                "metric_id": state.get("metric_id"),
                "time_range": state.get("time_range"),
            },
        ) as obs_span:
            over = _budget_ok(runtime, state, llm_needed=1, tool_needed=1)
            if over:
                obs_span.set_outputs({"error_code": over, "reason": "Observer budget exceeded"})
                return {"error_code": over, "error_message": "Observer budget exceeded", "status": "failed"}
            lead = state.get("mission_lead") or (domain_ids[0] if domain_ids else "observer_agent")
            _mid = state.get("metric_id")
            metric_def = runtime.metrics.get(_mid) if _mid else None
            tool_name = "seleric.metrics_query" if metric_def else "unresolved"
            with traced_span(
                f"tool.mcp.{tool_name}",
                {**_meta(runtime, state, "observer_agent"), "tool_name": tool_name},
                tracing,
                run_type="tool",
                inputs={"capability": tool_name, "time_range": state.get("time_range")},
            ) as tool_span:
                ctx = AgentContext(
                    mission_id=state["mission_id"],
                    task_id=state.get("task_id") or "",
                    question=state["user_query"],
                    mission_lead=lead,
                    payload={
                        "request_id": state.get("request_id"),
                        "session_id": state.get("session_id"),
                        "allowed_metrics": state.get("allowed_metrics") or [],
                        "metric_hints": state.get("metric_hints") or [],
                        "metric_id": state.get("metric_id"),
                        "entities": state.get("entities") or [],
                        "time_range": state.get("time_range") or {},
                        "query_class": state.get("query_class"),
                    },
                )
                result = await observer.observe(ctx)
                tool_span.set_outputs(
                    {
                        "metric_id": result.get("metric_id"),
                        "evidence_rows": [
                            {"metric": r.get("metric_or_fact"), "value": r.get("value"), "day": (r.get("time_range") or {}).get("start")}
                            for r in (result.get("evidence") or [])
                        ],
                        "mcp_called": result.get("mcp_called"),
                        "tool_calls": result.get("tool_calls"),
                        "error_code": result.get("error_code"),
                        "missing": result.get("limitations"),
                    }
                )
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
            else:
                # Authoritative DAG: mark observe_metric tasks done after a successful wave.
                result.update(
                    mark_ready_tasks_done(
                        dict(state) | {"evidence": merged},
                        task_types={"observe_metric"},
                    )
                )
            obs_span.set_outputs(
                {
                    "evidence_count": len(merged),
                    "evidence_refs": result.get("evidence_refs"),
                    "error_code": result.get("error_code"),
                    "status": result.get("status"),
                    "dag": dag_progress_summary(dict(state) | result),
                }
            )
            return result

    def _owning_domain_agent(metric_id: str) -> str | None:
        definition = runtime.metrics.get(metric_id)
        if definition is None:
            return None
        candidate = f"{definition.domain}_agent"
        return candidate if candidate in domain_agents else None

    def _unresolved_foreign(state: MissionState) -> list[str]:
        have = {row.get("metric_or_fact") for row in (state.get("evidence") or [])}
        return [
            metric_id
            for metric_id in (state.get("handoff_needed_metrics") or [])
            if metric_id not in have and _owning_domain_agent(metric_id)
        ]

    def route_after_observer(state: MissionState) -> AfterObserver:
        needed = _unresolved_foreign(state)
        if needed and state.get("evidence") and state.get("error_code") != "INSUFFICIENT_EVIDENCE":
            return "propose_handoff"
        return "claim_gate"

    def propose_handoff_node(state: MissionState) -> dict[str, Any]:
        from_agent = state.get("mission_lead") or "coordinator_agent"
        with traced_span(
            "node.leadership_transfer",
            _meta(runtime, state, from_agent),
            tracing,
            inputs={
                "current_lead": from_agent,
                "handoff_needed_metrics": state.get("handoff_needed_metrics"),
                "evidence_refs": state.get("evidence_refs"),
            },
        ) as span:
            needed = _unresolved_foreign(state)
            to_agent = _owning_domain_agent(needed[0]) if needed else from_agent
            refs = [row["evidence_id"] for row in (state.get("evidence") or [])]
            transfer = {
                "mission_id": state.get("mission_id"),
                "from_agent": from_agent,
                "to_agent": to_agent,
                "requested_target": to_agent,
                "reason": (
                    f"{from_agent} does not own the unresolved question, which requires "
                    f"{', '.join(needed)} -- a {to_agent} capability."
                ),
                "evidence_refs": refs,
                "unresolved_question": f"Retrieve {', '.join(needed)} for the same time range",
                "requested_output": f"EvidenceBundle for {to_agent} metrics",
            }
            span.set_outputs({"pending_transfer": transfer})
            return {"pending_transfer": transfer}

    def coordinator_arbitrate_node(state: MissionState) -> dict[str, Any]:
        with traced_span(
            "node.coordinator_arbitrate",
            _meta(runtime, state, "coordinator_agent"),
            tracing,
            inputs={"proposal": dict(state.get("pending_transfer") or {})},
        ) as span:
            proposal = dict(state.get("pending_transfer") or {})
            decision = leadership.decide(dict(state), proposal)
            if not decision.get("accepted"):
                span.set_outputs(
                    {
                        "accepted": False,
                        "error_code": decision.get("error_code"),
                        "reason": decision.get("error_message"),
                    }
                )
                return {
                    "error_code": decision.get("error_code") or "HANDOFF_REJECTED",
                    "error_message": decision.get("error_message") or "Handoff rejected",
                    "status": "failed",
                    "pending_transfer": None,
                }
            span.set_outputs(
                {
                    "accepted": True,
                    "new_mission_lead": decision["mission_lead"],
                    "leadership_epoch": decision["leadership_epoch"],
                }
            )
            needed = _unresolved_foreign(state)
            new_lead = decision["mission_lead"]
            lead_sel = dict(state.get("lead_selection") or {})
            if lead_sel:
                lead_sel["mission_lead"] = new_lead
                lead_sel["source"] = "handoff"
            patch = {
                "mission_lead": new_lead,
                "lead_selection": lead_sel or state.get("lead_selection"),
                "leadership_epoch": decision["leadership_epoch"],
                "handoff_history": decision["handoff_history"],
                "handoff_needed_metrics": [],
                "pending_transfer": None,
                "metric_id": needed[0] if needed else state.get("metric_id"),
                "error_code": None,
            }
            patch.update(
                _emit_lookup_event(
                    {**dict(state), **patch},
                    "leadership_transfer",
                    **(decision["handoff_history"][-1] if decision.get("handoff_history") else {}),
                )
            )
            return patch

    def route_after_arbitrate(state: MissionState) -> str:
        lead = next_domain_lead(dict(state))
        if state.get("error_code") or lead not in domain_node_names:
            return "finalize_error"
        return domain_node_names[lead]

    def claim_gate_node(state: MissionState) -> dict[str, Any]:
        with traced_span(
            "node.claim_gate",
            _meta(runtime, state, "claim_gate"),
            tracing,
            inputs={"evidence_count": len(state.get("evidence") or [])},
        ) as span:
            if state.get("error_code") == "INSUFFICIENT_EVIDENCE" and not state.get("evidence"):
                span.set_outputs({"claims": [], "gate": "skipped_no_evidence"})
                return {"claims": [], "status": "failed"}
            claims: list[dict[str, Any]] = []
            for ev in state.get("evidence") or []:
                day = (ev.get("time_range") or {}).get("start")
                end = (ev.get("time_range") or {}).get("end")
                unit = ev.get("unit") or ""
                metric = ev.get("metric_or_fact")
                value = ev.get("value")
                text = f"{metric} was {value} {unit}".strip()
                dims = ev.get("dimensions") or {}
                label = dims.get("product_title") or dims.get("sku") or dims.get("lt_channel") or dims.get("channel")
                if label:
                    text = f"{metric} for {label} was {value} {unit}".strip()
                if day and end and end != day:
                    text += f" from {day} to {end}"
                elif day:
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
                    span.set_outputs({"gate": "rejected", "problems": problems, "claim": claim})
                    return {
                        "claims": [claim],
                        "error_code": "CLAIM_REJECTED",
                        "error_message": "; ".join(problems),
                        "status": "failed",
                    }
                claims.append(claim)
            if not claims:
                span.set_outputs({"gate": "empty", "claims": []})
                return {
                    "claims": [],
                    "error_code": "INSUFFICIENT_EVIDENCE",
                    "error_message": "No claims passed the provenance gate",
                    "status": "failed",
                }
            span.set_outputs(
                {
                    "gate": "passed",
                    "claims": [
                        {"text": c["text"], "gate_status": c["gate_status"], "support_refs": c["support_refs"]}
                        for c in claims
                    ],
                }
            )
            patch = {"claims": claims}
            patch.update(mark_tasks(dict(state), task_types={"claim_gate"}, status="done"))
            patch.update(
                _emit_lookup_event(
                    {**dict(state), **patch},
                    "claim_validated",
                    claim_count=len(claims),
                )
            )
            return patch

    async def synthesize_node(state: MissionState) -> dict[str, Any]:
        with traced_span(
            "node.synthesizer",
            _meta(runtime, state, "response_synthesizer"),
            tracing,
            inputs={
                "claim_count": len(state.get("claims") or []),
                "gated_claims": [c.get("text") for c in state.get("claims") or []],
            },
        ) as span:
            if state.get("error_code") and not state.get("claims"):
                span.set_outputs({"skipped": True, "reason": state.get("error_code")})
                return {}
            over = _budget_ok(runtime, state, llm_needed=1)
            if over:
                from seleric_swarm.orchestration.synthesize import _table_fallback

                fallback = _table_fallback(state.get("evidence") or [], state.get("claims") or [])
                span.set_outputs({"synthesis_fallback": True, "final_response": fallback})
                return {
                    "final_response": fallback,
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
            result.update(mark_tasks(dict(state), task_types={"synthesize"}, status="done"))
            # Completion score now reads live task statuses from the authoritative DAG.
            completion = plane.completion(dict(state) | result)
            result.update(completion)
            span.set_outputs(
                {
                    "status": result.get("status"),
                    "final_response": result.get("final_response"),
                    "limitations": result.get("limitations"),
                    "completion_decision": result.get("completion_decision"),
                    "dag": dag_progress_summary(dict(state) | result),
                }
            )
            return result

    def finalize_unsupported_node(state: MissionState) -> dict[str, Any]:
        with traced_span(
            "node.finalize",
            {**_meta(runtime, state, "coordinator_agent"), "outcome": "unsupported"},
            tracing,
            inputs={"query_class": state.get("query_class"), "complexity": state.get("complexity_label")},
        ) as span:
            reason = state.get("unsupported_reason") or "Query class or domain is not enabled in V1"
            span.set_outputs(
                {
                    "status": "failed",
                    "error_code": state.get("error_code") or "ROUTING_UNSUPPORTED",
                    "reason": reason,
                    "blocked_reasons": state.get("plan_blocked_reasons"),
                }
            )
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
        with traced_span(
            "node.finalize",
            {**_meta(runtime, state, "coordinator_agent"), "outcome": "error"},
            tracing,
        ) as span:
            code = state.get("error_code") or "LLM_UNAVAILABLE"
            message = state.get("error_message") or "Mission failed"
            span.set_outputs({"status": "failed", "error_code": code, "error_message": message})
            return {
                "status": "failed",
                "error_code": code,
                "error_message": message,
                "final_response": message,
                "limitations": list(state.get("limitations") or [message]),
            }

    def finalize_success_node(state: MissionState) -> dict[str, Any]:
        with traced_span(
            "node.finalize",
            {**_meta(runtime, state, "coordinator_agent"), "outcome": "success"},
            tracing,
        ) as span:
            status = state.get("status") or "completed"
            if state.get("error_code") == "INSUFFICIENT_EVIDENCE" and not state.get("claims"):
                status = "failed"
            patch: dict[str, Any] = {"status": status}
            completion = plane.completion(dict(state))
            patch.update(completion)
            kind = "mission_completed" if status == "completed" else "mission_partial"
            if status == "failed":
                kind = "mission_partial"
            patch.update(
                _emit_lookup_event(
                    {**dict(state), **patch},
                    kind,
                    status=status,
                    completion_decision=completion.get("completion_decision"),
                )
            )
            span.set_outputs(
                {
                    "status": status,
                    "completion_score": completion["completion_score"],
                    "completion_decision": completion["completion_decision"],
                    "completion_components": completion["completion_components"],
                    "unresolved_questions": completion["unresolved_questions"],
                }
            )
            return patch

    graph = StateGraph(MissionState)
    graph.add_node("coordinator", coordinator_node)
    for agent_id, node_name in domain_node_names.items():
        graph.add_node(node_name, _make_domain_node(agent_id, domain_agents[agent_id]))
    graph.add_node("observer", observer_node)
    graph.add_node("propose_handoff", propose_handoff_node)
    graph.add_node("coordinator_arbitrate", coordinator_arbitrate_node)
    graph.add_node("claim_gate", claim_gate_node)
    graph.add_node("synthesize", synthesize_node)
    graph.add_node("finalize_unsupported", finalize_unsupported_node)
    graph.add_node("finalize_error", finalize_error_node)
    graph.add_node("finalize", finalize_success_node)
    graph.add_edge(START, "coordinator")
    domain_route_map: dict[Hashable, str] = {name: name for name in domain_node_names.values()}
    coordinator_routes: dict[Hashable, str] = dict(domain_route_map)
    coordinator_routes["finalize_unsupported"] = "finalize_unsupported"
    coordinator_routes["finalize_error"] = "finalize_error"
    graph.add_conditional_edges("coordinator", route_after_coordinator, coordinator_routes)
    for node_name in domain_node_names.values():
        graph.add_edge(node_name, "observer")
    graph.add_conditional_edges(
        "observer",
        route_after_observer,
        {"propose_handoff": "propose_handoff", "claim_gate": "claim_gate"},
    )
    graph.add_edge("propose_handoff", "coordinator_arbitrate")
    arbitrate_routes: dict[Hashable, str] = dict(domain_route_map)
    arbitrate_routes["finalize_error"] = "finalize_error"
    graph.add_conditional_edges("coordinator_arbitrate", route_after_arbitrate, arbitrate_routes)
    graph.add_edge("claim_gate", "synthesize")
    graph.add_edge("synthesize", "finalize")
    graph.add_edge("finalize", END)
    graph.add_edge("finalize_unsupported", END)
    graph.add_edge("finalize_error", END)
    return graph.compile()
