"""Ecommerce/marketplace domain expertise and governed data access."""

from __future__ import annotations

from typing import Any

from seleric_swarm.agents.base import AgentContext, SwarmAgent
from seleric_swarm.runtime import SwarmRuntime

AGENT_VERSION = "0.1.0"
DOMAIN = "commerce"
ALLOWED_CAPABILITIES = {
    "commerce.daily_sales",
    "seleric.catalogue_search_metrics",
    "seleric.catalogue_resolve_term",
    "seleric.catalogue_get_metric",
    "seleric.catalogue_list_dimensions",
    "seleric.metrics_query",
    "seleric.metrics_drilldown",
    "seleric.insights_explain",
}


class Agent(SwarmAgent):
    agent_id = "commerce_agent"

    def __init__(self, runtime: SwarmRuntime | None = None) -> None:
        self.runtime = runtime

    async def run(self, ctx: AgentContext) -> dict[str, Any]:
        assert self.runtime is not None, "domain agent requires a runtime"
        allowed_metrics = set(self.runtime.metrics.ids_for_domain(DOMAIN))
        hints = list(ctx.payload.get("metric_hints") or [])
        requested = ctx.payload.get("metric_id")
        commerce_hints = [h for h in hints if h in allowed_metrics]
        if requested in allowed_metrics:
            metric_id = requested
        elif commerce_hints:
            metric_id = commerce_hints[0]
        else:
            metric_id = requested
        if metric_id and metric_id not in allowed_metrics:
            return {
                "error_code": "ROUTING_UNSUPPORTED",
                "error_message": f"Commerce agent does not own {metric_id}",
                "unsupported_reason": f"Metric {metric_id} is outside commerce allowlist",
                "active_specialist": "observer_agent",
            }
        return {
            "mission_lead": self.agent_id,
            "active_specialist": "observer_agent",
            "metric_id": metric_id,
            "allowed_metrics": sorted(allowed_metrics),
            "mcp_capabilities": sorted(ALLOWED_CAPABILITIES),
            "handoff_needed_metrics": [],
        }
