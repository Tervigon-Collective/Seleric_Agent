"""Control-plane facade (pasted spec sec. 2, 46).

One object the graph's coordinator node calls after the LLM classification. It
runs the deterministic planes - planning, routing, governance - and returns a
patch of plan/governance fields for ``MissionState``. It performs no LLM or MCP
calls itself; the truth stays with evidence and the specialist agents.
"""

from __future__ import annotations

from typing import Any

from seleric_swarm.coordinator.governance.budget import (
    MissionLimits,
    check_budget,
    check_hard_stops,
)
from seleric_swarm.coordinator.governance.completion import assess_completion
from seleric_swarm.coordinator.leadership.lead_selector import select_initial_lead
from seleric_swarm.coordinator.models import ComplexityLevel, TaskGraph
from seleric_swarm.coordinator.planning.complexity import classify_complexity
from seleric_swarm.coordinator.planning.dag_builder import build_task_dag
from seleric_swarm.coordinator.routing.capability_resolver import CapabilityResolver
from seleric_swarm.coordinator.routing.dispatchability import DispatchGuard
from seleric_swarm.runtime import SwarmRuntime


class ControlPlane:
    def __init__(self, runtime: SwarmRuntime) -> None:
        self.runtime = runtime
        self.limits = MissionLimits.from_settings(runtime.settings)
        self.resolver = CapabilityResolver(runtime.agents)
        self.guard = DispatchGuard(
            self.resolver,
            runtime.metrics,
            set(getattr(runtime.mcp, "capabilities", set())),
        )

    # -- planning ---------------------------------------------------------------
    def plan(
        self,
        *,
        query: str,
        query_class: str,
        llm_domain_lead: str | None,
        metric_hints: list[str] | None,
        entities: list[str] | None,
    ) -> dict[str, Any]:
        metric_hints = metric_hints or []
        entities = entities or []

        complexity = classify_complexity(
            query_class=query_class,
            query=query,
            metric_hints=metric_hints,
            entities=entities,
        )
        lead = select_initial_lead(
            llm_domain_lead=llm_domain_lead,
            metric_hints=metric_hints,
            metrics=self.runtime.metrics,
        )
        graph: TaskGraph = build_task_dag(
            query_class=query_class,
            mission_lead=lead.mission_lead,
            complexity=complexity,
            metric_hints=metric_hints,
            guard=self.guard,
        )
        return {
            "complexity": int(complexity),
            "complexity_label": complexity.label,
            "decomposed_questions": [
                {
                    "id": t.id,
                    "question": t.objective,
                    "type": t.type,
                    "depends_on": list(t.depends_on),
                    "answerable": t.dispatchable,
                    "blocked_reason": t.blocked_reason,
                }
                for t in graph.tasks
            ],
            "task_graph": graph.to_dict(),
            "plan_dispatchable": graph.dispatchable,
            "plan_blocked_reasons": [
                t.blocked_reason for t in graph.blocked_tasks if t.blocked_reason
            ],
            "lead_selection": {
                "mission_lead": lead.mission_lead,
                "score": lead.score,
                "source": lead.source,
                "rationale": lead.rationale,
            },
        }

    # -- governance -----------------------------------------------------------
    def budget_verdict(
        self, state: dict[str, Any], *, llm_needed: int = 0, tool_needed: int = 0
    ) -> tuple[bool, str | None, str | None]:
        """Per-node spend check: LLM/tool ceilings only (legacy ``_budget_ok``)."""

        verdict = check_budget(state, self.limits, llm_needed=llm_needed, tool_needed=tool_needed)
        return (verdict.ok, verdict.error_code, verdict.reason)

    def hard_stop_verdict(self, state: dict[str, Any]) -> tuple[bool, str | None, str | None]:
        """Cycle-owner check: iteration / leadership-transfer / agent-call ceilings."""

        verdict = check_hard_stops(state, self.limits)
        return (verdict.ok, verdict.error_code, verdict.reason)

    def completion(self, state: dict[str, Any]) -> dict[str, Any]:
        assessment = assess_completion(state, state.get("task_graph"))
        return {
            "completion_score": assessment.score,
            "completion_decision": assessment.decision,
            "completion_components": assessment.components,
            "unresolved_questions": assessment.unresolved,
        }

    def unsupported_reason(self, plan: dict[str, Any], fallback: str) -> str:
        band = ComplexityLevel(int(plan.get("complexity") or 0)).label
        parts = [f"{fallback} [complexity {band}]"]
        uniq = list(dict.fromkeys(plan.get("plan_blocked_reasons") or []))
        if uniq:
            parts.append("Blocked: " + "; ".join(uniq[:3]))
        return " ".join(parts)
