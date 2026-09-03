"""Swarm mission state and result (architecture sec. 37, 40)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class SwarmMission:
    mission_id: str
    query: str
    time_range: dict[str, Any]
    intents: set[str] = field(default_factory=set)  # diagnostic | predictive | prescriptive
    complexity: str = "L4"
    initial_lead: str = "coordinator_agent"
    context: dict[str, Any] = field(default_factory=dict)

    def wants(self, intent: str) -> bool:
        return intent in self.intents


@dataclass
class TeamMember:
    agent_id: str
    axis: str  # "domain" | "specialist"
    role: str  # "lead" | "active_specialist" | "support"


@dataclass
class SwarmMissionResult:
    mission_id: str
    status: str  # completed | partial | failed
    query: str
    complexity: str
    initial_mission_lead: str
    mission_lead: str
    leadership_epoch: int
    team: list[dict[str, Any]]
    handoff_history: list[dict[str, Any]]
    artifacts: dict[str, list[str]]  # type -> ids
    final_response: str
    limitations: list[str] = field(default_factory=list)
    synthetic: bool = True
    events: list[dict[str, Any]] = field(default_factory=list)
    error_code: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
