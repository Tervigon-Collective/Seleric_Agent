"""Plan validator — re-export + structural checks."""

from __future__ import annotations

from seleric_swarm.coordinator.contracts import MissionPlan
from seleric_swarm.coordinator.planning.mission_planner import validate_plan


def validate_mission_plan(plan: MissionPlan) -> list[str]:
    return validate_plan(plan)


__all__ = ["validate_mission_plan", "validate_plan"]
