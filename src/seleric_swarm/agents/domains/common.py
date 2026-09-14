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


def assigned_work_for_domain(
    *,
    domain: str,
    owned: set[str],
    payload: dict[str, Any],
) -> tuple[list[str], list[str], list[str]]:
    """Assigned owned metrics, foreign metrics to hand off, and this row's grain.

    Prefers DomainQuestion rows (registry partition). Falls back to
    ``metric_hints ∩ owned``. Never returns the whole domain dump.
    """

    fetched = {m for m in (payload.get("fetched_metrics") or []) if m}
    dqs = [dq for dq in (payload.get("domain_questions") or []) if isinstance(dq, dict)]
    grain: list[str] = []
    if dqs:
        assigned: list[str] = []
        foreign: list[str] = []
        for dq in dqs:
            metrics = [str(m) for m in (dq.get("metrics") or []) if m]
            if dq.get("domain") == domain:
                assigned = [m for m in metrics if m in owned]
                grain = [str(g) for g in (dq.get("grain") or []) if g]
            else:
                for mid in metrics:
                    if mid not in owned and mid not in fetched and mid not in foreign:
                        foreign.append(mid)
        return assigned, foreign, grain

    hints = list(payload.get("metric_hints") or [])
    assigned = [h for h in hints if h in owned]
    foreign = [
        h
        for h in hints
        if str(h).startswith("metric.") and h not in owned and h not in fetched
    ]
    return assigned, foreign, grain


async def domain_mission_update(
    runtime: Any,
    *,
    agent_id: str,
    domain: str,
    ctx: AgentContext,
) -> dict[str, Any]:
    """Owned assigned metrics stay here; other assigned metrics are handed off."""
    owned = set(runtime.metrics.ids_for_domain(domain))
    assigned, foreign, grain = assigned_work_for_domain(
        domain=domain, owned=owned, payload=ctx.payload
    )
    requested = ctx.payload.get("metric_id")
    if not assigned and requested in owned:
        assigned = [requested]
    metric_id = assigned[0] if assigned else requested
    if not assigned or (metric_id and metric_id not in owned):
        label = domain.capitalize()
        unknown = metric_id or "the requested metric"
        return {
            "error_code": "ROUTING_UNSUPPORTED",
            "error_message": f"{label} agent does not own {unknown}",
            "unsupported_reason": f"Metric {unknown} is outside {domain} allowlist",
            "active_specialist": "observer_agent",
        }
    return {
        "mission_lead": agent_id,
        "active_specialist": "observer_agent",
        "metric_id": metric_id,
        "allowed_metrics": list(assigned),
        "assigned_grain": grain,
        "mcp_capabilities": sorted(SELERIC_CATALOGUE_CAPABILITIES),
        "handoff_needed_metrics": foreign,
        "ontology_context": await ontology_context_for(runtime, agent_id),
    }
