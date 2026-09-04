"""Performance marketing domain expertise and governed data access."""

from __future__ import annotations

from typing import Any

from seleric_swarm.agents.base import AgentContext, SwarmAgent
from seleric_swarm.agents.domains.commerce import DOMAIN as COMMERCE_DOMAIN
from seleric_swarm.runtime import SwarmRuntime

AGENT_VERSION = "0.1.0"
DOMAIN = "performance"
ALLOWED_CAPABILITIES = {
    "performance.daily_cac",
    "seleric.catalogue_search_metrics",
    "seleric.catalogue_resolve_term",
    "seleric.catalogue_get_metric",
    "seleric.catalogue_list_dimensions",
    "seleric.metrics_query",
    "seleric.metrics_drilldown",
    "seleric.insights_explain",
}


class Agent(SwarmAgent):
    agent_id = "performance_agent"

    def __init__(self, runtime: SwarmRuntime | None = None) -> None:
        self.runtime = runtime

    async def run(self, ctx: AgentContext) -> dict[str, Any]:
        allowed_metrics = self.runtime.metrics.ids_for_domain(DOMAIN)
        commerce_metrics = set(self.runtime.metrics.ids_for_domain(COMMERCE_DOMAIN))
        hints = list(ctx.payload.get("metric_hints") or [])
        owned = [h for h in hints if h in allowed_metrics] or allowed_metrics
        foreign = [h for h in hints if h in commerce_metrics]
        return {
            "mission_lead": self.agent_id,
            "active_specialist": "observer_agent",
            "metric_id": owned[0],
            "allowed_metrics": sorted(allowed_metrics),
            "mcp_capabilities": sorted(ALLOWED_CAPABILITIES),
            "handoff_needed_metrics": foreign,
        }
