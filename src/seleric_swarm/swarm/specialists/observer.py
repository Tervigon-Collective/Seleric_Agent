"""Observer - "What is actually happening?" (architecture sec. 4).

The grounding agent. It does not interpret; it delegates governed data retrieval
to the Domain Agent that currently leads and posts EvidenceArtifacts.
"""

from __future__ import annotations

from seleric_swarm.swarm.blackboard import Blackboard
from seleric_swarm.swarm.domain.base import DomainAgent
from seleric_swarm.swarm.mission import SwarmMission
from seleric_swarm.swarm.providers.base import ProviderBundle
from seleric_swarm.swarm.specialists.base import SpecialistAgent


class ObserverAgent(SpecialistAgent):
    agent_id = "observer_agent"
    capability = "metric_observation"
    produces = "evidence"

    def __init__(self, providers: ProviderBundle, domains: dict[str, DomainAgent]) -> None:
        super().__init__(providers)
        self._domains = domains

    async def run(self, blackboard: Blackboard, mission: SwarmMission) -> list[str]:
        lead = blackboard.mission_lead or mission.initial_lead
        domain = self._domains.get(lead)
        if domain is None:
            blackboard.record_event("observe_no_domain", lead=lead)
            return []
        return await domain.observe(blackboard, time_range=mission.time_range)
