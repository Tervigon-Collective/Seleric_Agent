"""Intelligence Specialist base (architecture sec. 3).

A specialist is a small analytical system, not a prompt: context builder ->
policy (should I run?) -> service router (which provider) -> artifact builder ->
validator. The LLM (absent in this prototype) would be one component among these.

One reusable capability works with whichever Domain Agent currently leads - there
is no "Performance Diagnostic Agent", only Diagnostic + the active domain.
"""

from __future__ import annotations

from typing import Any

from seleric_swarm.swarm.blackboard import Blackboard
from seleric_swarm.swarm.mission import SwarmMission
from seleric_swarm.swarm.providers.base import ProviderBundle


class SpecialistAgent:
    agent_class = "specialist"
    agent_id: str = "specialist_agent"
    capability: str = ""
    produces: str = ""

    def __init__(self, providers: ProviderBundle) -> None:
        self.providers = providers

    # -- policy: does this mission need me right now? ----------------------
    def policy(self, blackboard: Blackboard, mission: SwarmMission) -> bool:
        return True

    # -- context builder --------------------------------------------------
    def build_context(self, blackboard: Blackboard, mission: SwarmMission) -> dict[str, Any]:
        return {
            "mission_lead": blackboard.mission_lead,
            "time_range": mission.time_range,
            "evidence": blackboard.by_type("evidence"),
            "anomalies": blackboard.by_type("anomaly"),
        }

    # -- execution ------------------------------------------------------
    async def run(self, blackboard: Blackboard, mission: SwarmMission) -> list[str]:
        raise NotImplementedError

    # -- validator ----------------------------------------------------
    def validate(self, payload: dict[str, Any]) -> tuple[bool, list[str]]:
        problems: list[str] = []
        if not payload.get("artifact_id"):
            problems.append("artifact missing id")
        if payload.get("synthetic") and "SYNTHETIC" not in (payload.get("quality_flags") or []):
            problems.append("synthetic artifact not flagged SYNTHETIC")
        return (not problems, problems)
