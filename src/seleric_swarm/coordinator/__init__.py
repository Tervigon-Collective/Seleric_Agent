"""Seleric Swarm Coordinator control plane.

The coordinator is a control plane, not the smartest agent: it plans, routes,
schedules, governs budget, detects gaps, scores completion and synthesizes.
Analytical truth stays with the specialist agents and evidence.

Layout:

    planning/       complexity, dag_builder, mission_planner, plan_validator
    routing/        capability_resolver, dispatchability, agent_selector, invocation
    leadership/     lead_selector, frontier, hysteresis, loop_detector
    governance/     budget, completion, completion_gate, remediation, skeptic_gate,
                    conflicts, synthetic_guard
    evidence/       gap_detector
    intake/         normalize / resolve
    decomposition/  progressive problem decomposition
    artifacts/      dedup, lineage, claims
    execution/      scheduler, dispatcher, parallel, retry, execution_engine
    synthesis/      claim selector, response builder, provenance
    plane.py        ControlPlane facade the lookup graph node calls
    graph.py        swarm_v2 LangGraph + runner
    agent.py        system prompt + classify bridge
    state.py        MissionState helpers
"""

from seleric_swarm.coordinator.agent import COORDINATOR_SYSTEM_PROMPT
from seleric_swarm.coordinator.contracts import (
    CompletionDecision,
    MissionPlan,
    MissionRequest,
    NormalizedQuery,
    ProblemDecomposition,
    SubQuestion,
    TaskSpec,
)
from seleric_swarm.coordinator.models import ComplexityLevel, Task, TaskGraph
from seleric_swarm.coordinator.plane import ControlPlane

__all__ = [
    "COORDINATOR_SYSTEM_PROMPT",
    "CompletionDecision",
    "ComplexityLevel",
    "ControlPlane",
    "MissionPlan",
    "MissionRequest",
    "NormalizedQuery",
    "ProblemDecomposition",
    "SubQuestion",
    "Task",
    "TaskGraph",
    "TaskSpec",
]
