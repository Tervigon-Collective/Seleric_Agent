"""Dispatchability predicate (advisor guidance; pasted spec sec. 3 "governance").

A task is dispatchable only when *every* piece needed to execute it exists today:

1. each required capability resolves to at least one wired agent, and
2. every metric the task reads has an ``mcp_capability`` that is live in the
   MCP gateway for this process.

The coordinator uses this at plan time so it emits an honest plan: unreachable
work is marked ``blocked`` with a specific reason instead of being dispatched
into a stub agent or a missing data path.
"""

from __future__ import annotations

from dataclasses import dataclass

from seleric_swarm.coordinator.models import Task
from seleric_swarm.coordinator.routing.capability_resolver import CapabilityResolver
from seleric_swarm.services.metrics import MetricRegistry


@dataclass
class Dispatchability:
    dispatchable: bool
    agent: str | None = None
    reason: str | None = None


class DispatchGuard:
    def __init__(
        self,
        resolver: CapabilityResolver,
        metrics: MetricRegistry,
        mcp_capabilities: set[str],
    ) -> None:
        self._resolver = resolver
        self._metrics = metrics
        self._mcp = set(mcp_capabilities)

    def check(self, task: Task) -> Dispatchability:
        # 1. capabilities must resolve to a wired agent
        wired: list[str] = []
        for cap in task.required_capabilities:
            res = self._resolver.resolve(cap)
            if not res.resolvable:
                return Dispatchability(
                    dispatchable=False,
                    reason=f"No wired agent provides capability '{cap}'",
                )
            wired.append(res.wired_candidates[0])

        # 2. metric reads need a live MCP capability
        for metric_id in task.metric_ids:
            definition = self._metrics.get(metric_id)
            if definition is None:
                return Dispatchability(
                    dispatchable=False,
                    reason=f"Metric '{metric_id}' is not in the metric registry",
                )
            mcp_cap = definition.mcp_capability
            if not mcp_cap:
                return Dispatchability(
                    dispatchable=False,
                    reason=f"Metric '{metric_id}' has no mcp_capability defined",
                )
            if mcp_cap not in self._mcp:
                return Dispatchability(
                    dispatchable=False,
                    reason=f"MCP capability '{mcp_cap}' for metric '{metric_id}' is not live in this build",
                )

        agent = task.assigned_agent or (wired[0] if wired else None)
        return Dispatchability(dispatchable=True, agent=agent)

    def annotate(self, task: Task) -> Task:
        verdict = self.check(task)
        task.dispatchable = verdict.dispatchable
        task.blocked_reason = verdict.reason
        if verdict.agent and not task.assigned_agent:
            task.assigned_agent = verdict.agent
        if not verdict.dispatchable:
            task.status = "blocked"
        return task
