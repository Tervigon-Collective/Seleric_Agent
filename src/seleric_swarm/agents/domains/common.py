"""Shared Seleric catalogue capabilities + ontology context for domain agents."""

from __future__ import annotations

from typing import Any

from seleric_swarm.agents.base import AgentContext
from seleric_swarm.protocols.mcp.servers.seleric_remote import TOOLS as SELERIC_TOOLS

SELERIC_CATALOGUE_CAPABILITIES = {f"seleric.{tool}" for tool in SELERIC_TOOLS}


async def ontology_context_for(runtime: Any, agent_id: str) -> dict[str, Any]:
    """Module-scoped OM snapshot for this domain agent, or {} offline."""
    ontology = getattr(runtime, "ontology", None) if runtime is not None else None
    if ontology is None:
        return {}
    return await ontology.for_agent(agent_id)


async def domain_mission_update(
    runtime: Any,
    *,
    agent_id: str,
    domain: str,
    ctx: AgentContext,
) -> dict[str, Any]:
    """Owned metrics stay here; other hinted metrics are handed to their owner."""
    allowed = set(runtime.metrics.ids_for_domain(domain))
    hints = list(ctx.payload.get("metric_hints") or [])
    fetched = {m for m in (ctx.payload.get("fetched_metrics") or []) if m}
    owned = [h for h in hints if h in allowed]
    foreign = [
        h
        for h in hints
        if str(h).startswith("metric.") and h not in allowed and h not in fetched
    ]
    requested = ctx.payload.get("metric_id")
    if requested in allowed:
        metric_id = requested
    elif owned:
        metric_id = owned[0]
    else:
        metric_id = requested
    if metric_id and metric_id not in allowed:
        label = domain.capitalize()
        return {
            "error_code": "ROUTING_UNSUPPORTED",
            "error_message": f"{label} agent does not own {metric_id}",
            "unsupported_reason": f"Metric {metric_id} is outside {domain} allowlist",
            "active_specialist": "observer_agent",
        }
    return {
        "mission_lead": agent_id,
        "active_specialist": "observer_agent",
        "metric_id": metric_id,
        "allowed_metrics": sorted(allowed),
        "mcp_capabilities": sorted(SELERIC_CATALOGUE_CAPABILITIES),
        "handoff_needed_metrics": foreign,
        "ontology_context": await ontology_context_for(runtime, agent_id),
    }
