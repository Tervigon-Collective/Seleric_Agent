"""Seleric Swarm Coordinator — classification/grounding bridge for V3.

The "Coordinator V1" control plane (``plane.py``'s ``ControlPlane`` facade
plus ``execution/``, ``routing/``, ``governance/``, ``evidence/``,
``artifacts/``, ``planning/{mission_planner,plan_validator,dag_builder}.py``,
``leadership/lead_selector.py``) was deleted in Sprint 5: it had no real
caller left once ``coordinator/graph.py`` and ``orchestration/graph.py`` (the
only things that ever invoked it) were deleted.

Layout:

    planning/       complexity
    intake/         normalize / resolve
    decomposition/  progressive problem decomposition
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

__all__ = [
    "COORDINATOR_SYSTEM_PROMPT",
    "CompletionDecision",
    "ComplexityLevel",
    "MissionPlan",
    "MissionRequest",
    "NormalizedQuery",
    "ProblemDecomposition",
    "SubQuestion",
    "Task",
    "TaskGraph",
    "TaskSpec",
]
