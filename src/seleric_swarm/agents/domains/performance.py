"""Performance marketing domain expertise and governed data access."""

from __future__ import annotations

from typing import Any

from seleric_swarm.agents.base import AgentContext, SwarmAgent
from seleric_swarm.agents.domains.commerce import ALLOWED_METRICS as COMMERCE_METRICS
from seleric_swarm.runtime import SwarmRuntime

AGENT_VERSION = "0.1.0"
ALLOWED_METRICS = {"metric.cac"}
ALLOWED_CAPABILITIES = {"performance.daily_cac"}


class Agent(SwarmAgent):
    agent_id = "performance_agent"

    def __init__(self, runtime: SwarmRuntime | None = None) -> None:
        self.runtime = runtime

    async def run(self, ctx: AgentContext) -> dict[str, Any]:
        hints = list(ctx.payload.get("metric_hints") or [])
        owned = [h for h in hints if h in ALLOWED_METRICS] or ["metric.cac"]
        foreign = [h for h in hints if h in COMMERCE_METRICS]
        return {
            "mission_lead": self.agent_id,
            "active_specialist": "observer_agent",
            "metric_id": owned[0],
            "allowed_metrics": sorted(ALLOWED_METRICS),
            "mcp_capabilities": sorted(ALLOWED_CAPABILITIES),
            "handoff_needed_metrics": foreign,
        }
