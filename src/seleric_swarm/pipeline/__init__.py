"""Stage wiring (L4). `run_mission` lands as modules move out of `agent/runner.py`.

Today: the v2 stages (understand → spec → plan) run in shadow beside the legacy mission
(``start_shadow`` / ``collect_shadow``) behind ``UNDERSTAND_V2_MODE`` / ``PLANNER_V2_MODE``.
"""

from seleric_swarm.pipeline._shadow import (
    LegacyDecision,
    ShadowInputs,
    ShadowModes,
    collect_shadow,
    start_shadow,
    summarise,
)

__all__ = [
    "LegacyDecision",
    "ShadowInputs",
    "ShadowModes",
    "collect_shadow",
    "start_shadow",
    "summarise",
]
