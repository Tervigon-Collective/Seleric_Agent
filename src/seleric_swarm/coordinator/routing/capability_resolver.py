"""Capability -> agent resolution (pasted spec sec. 12-13).

The coordinator selects agents by *capability*, not by name. This module turns a
required capability into the set of agents that advertise it in the registry,
and flags which of those actually have a wired execution path in this build.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from seleric_swarm.coordinator.models import Task
from seleric_swarm.registry.agent_registry import AgentRegistry

# Agents that currently have a node in orchestration/graph.py. Everything else in
# config/agent_registry.yaml is a not_implemented stub, so a task routed to it can
# never dispatch - the coordinator must know that at plan time, not run time.
WIRED_AGENTS: frozenset[str] = frozenset(
    {"coordinator_agent", "commerce_agent", "performance_agent", "observer_agent"}
)


@dataclass
class CapabilityResolution:
    capability: str
    candidates: list[str] = field(default_factory=list)
    wired_candidates: list[str] = field(default_factory=list)

    @property
    def resolvable(self) -> bool:
        return bool(self.wired_candidates)


class CapabilityResolver:
    def __init__(self, registry: AgentRegistry, wired: frozenset[str] = WIRED_AGENTS) -> None:
        self._registry = registry
        self._wired = wired

    def resolve(self, capability: str) -> CapabilityResolution:
        candidates = [str(a["id"]) for a in self._registry.find_by_capability(capability) if a.get("id")]
        wired = [a for a in candidates if a in self._wired]
        return CapabilityResolution(capability=capability, candidates=candidates, wired_candidates=wired)

    def resolve_task(self, task: Task) -> dict[str, CapabilityResolution]:
        return {cap: self.resolve(cap) for cap in task.required_capabilities}

    def unresolved_capabilities(self, task: Task) -> list[str]:
        return [cap for cap, res in self.resolve_task(task).items() if not res.resolvable]
