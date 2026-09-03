"""Capability -> agent resolution (pasted spec sec. 12-13).

The coordinator selects agents by *capability*, not by name. This module turns a
required capability into the set of agents that advertise it in the registry,
and flags which of those actually have a wired execution path in this build.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from seleric_swarm.coordinator.models import Task
from seleric_swarm.registry.agent_registry import AgentRegistry


@dataclass
class CapabilityResolution:
    capability: str
    candidates: list[str] = field(default_factory=list)
    wired_candidates: list[str] = field(default_factory=list)

    @property
    def resolvable(self) -> bool:
        return bool(self.wired_candidates)


class CapabilityResolver:
    def __init__(self, registry: AgentRegistry, wired: frozenset[str] | None = None) -> None:
        self._registry = registry
        # Agents with a real execution path in this build: config/agent_registry.yaml's
        # `enabled: true` is the single source of truth, not a hand-maintained list here.
        self._wired = wired if wired is not None else registry.wired_agent_ids()

    def resolve(self, capability: str) -> CapabilityResolution:
        candidates = [str(a["id"]) for a in self._registry.find_by_capability(capability) if a.get("id")]
        wired = [a for a in candidates if a in self._wired]
        return CapabilityResolution(capability=capability, candidates=candidates, wired_candidates=wired)

    def resolve_task(self, task: Task) -> dict[str, CapabilityResolution]:
        return {cap: self.resolve(cap) for cap in task.required_capabilities}

    def unresolved_capabilities(self, task: Task) -> list[str]:
        return [cap for cap, res in self.resolve_task(task).items() if not res.resolvable]
