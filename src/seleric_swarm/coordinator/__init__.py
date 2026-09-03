"""Seleric Swarm Coordinator control plane.

The coordinator is a control plane, not the smartest agent: it plans, routes,
schedules, governs budget, detects gaps, scores completion and synthesizes.
Analytical truth stays with the specialist agents and evidence.

Layout mirrors the pasted architecture (planning / control / governance planes):

    planning/    complexity, dag_builder
    routing/     capability_resolver, dispatchability, agent_selector
    leadership/  lead_selector
    governance/  budget, completion
    evidence/    gap_detector
    plane.py     ControlPlane facade the graph node calls
"""

from seleric_swarm.coordinator.models import ComplexityLevel, Task, TaskGraph
from seleric_swarm.coordinator.plane import ControlPlane

__all__ = ["ComplexityLevel", "ControlPlane", "Task", "TaskGraph"]
