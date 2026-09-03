"""Website funnel/journey domain expertise and governed data access."""

from __future__ import annotations

from typing import Any

from seleric_swarm.agents.base import AgentContext, SwarmAgent
from seleric_swarm.runtime import SwarmRuntime

AGENT_VERSION = "0.1.0"
ALLOWED_METRICS = {
    "metric.sessions",
    "metric.pdp_view_rate",
    "metric.atc_rate",
    "metric.checkout_rate",
    "metric.purchase_cvr",
}
ALLOWED_CAPABILITIES = {
    "seleric.catalogue_search_metrics",
    "seleric.catalogue_resolve_term",
    "seleric.catalogue_get_metric",
    "seleric.catalogue_list_dimensions",
    "seleric.metrics_query",
    "seleric.metrics_drilldown",
    "seleric.insights_explain",
}


class Agent(SwarmAgent):
    agent_id = "funnel_agent"

    def __init__(self, runtime: SwarmRuntime | None = None) -> None:
        self.runtime = runtime

    async def run(self, ctx: AgentContext) -> dict[str, Any]:
        hints = list(ctx.payload.get("metric_hints") or [])
        requested = ctx.payload.get("metric_id")
        owned_hints = [h for h in hints if h in ALLOWED_METRICS]
        if requested in ALLOWED_METRICS:
            metric_id = requested
        elif owned_hints:
            metric_id = owned_hints[0]
        else:
            metric_id = requested
        if metric_id and metric_id not in ALLOWED_METRICS:
            return {
                "error_code": "ROUTING_UNSUPPORTED",
                "error_message": f"Funnel agent does not own {metric_id}",
                "unsupported_reason": f"Metric {metric_id} is outside funnel allowlist",
                "active_specialist": "observer_agent",
            }
        return {
            "mission_lead": self.agent_id,
            "active_specialist": "observer_agent",
            "metric_id": metric_id,
            "allowed_metrics": sorted(ALLOWED_METRICS),
            "mcp_capabilities": sorted(ALLOWED_CAPABILITIES),
            "handoff_needed_metrics": [],
        }
