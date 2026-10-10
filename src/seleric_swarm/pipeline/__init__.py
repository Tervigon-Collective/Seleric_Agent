"""Stage wiring (L4). `run_mission` lands as modules move out of `agent/runner.py`.

Today: the v2 stages (understand → spec → plan → execute → answer) run in shadow beside the
legacy mission (``start_shadow`` / ``collect_shadow``) behind ``UNDERSTAND_V2_MODE`` /
``PLANNER_V2_MODE`` / ``ANSWER_V2_MODE``. ``run_plan`` executes an ``AnalysisPlan``
(queries, derivations, the ``investigate`` engine).
"""

from seleric_swarm.pipeline._engines import ExplorationMap, continue_exploration, follow_up_steps
from seleric_swarm.pipeline._run_plan import PlanReader, PlanRun, findings_as_derivations, run_plan
from seleric_swarm.pipeline._shadow import (
    LegacyDecision,
    ShadowInputs,
    ShadowModes,
    collect_shadow,
    start_shadow,
    summarise,
)

__all__ = [
    "ExplorationMap",
    "LegacyDecision",
    "PlanReader",
    "PlanRun",
    "ShadowInputs",
    "ShadowModes",
    "collect_shadow",
    "continue_exploration",
    "findings_as_derivations",
    "follow_up_steps",
    "run_plan",
    "start_shadow",
    "summarise",
]
