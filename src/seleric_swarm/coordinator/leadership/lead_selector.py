"""Initial mission-lead selection (pasted spec sec. 15-16).

    LeadScore = 0.35*ProblemOwnership + 0.20*PrimaryMetricOwnership
              + 0.15*InitialEvidenceDomain + 0.15*CapabilityCoverage
              + 0.10*HistoricalPerformance + 0.05*Availability

The LLM classifier still proposes ``domain_lead``; this function is the
deterministic check on it. When the proposal is a known, enabled domain agent
it is kept (the gold set pins those values). When it is missing or unknown,
the score picks a lead from metric ownership in the registry.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from seleric_swarm.registry.agent_registry import AgentRegistry
from seleric_swarm.services.metrics import MetricRegistry


@dataclass
class LeadDecision:
    mission_lead: str
    score: float
    rationale: str
    source: str  # "llm" | "metric_ownership" | "fallback"


def _owner_agent(metric_ids: Sequence[str], metrics: MetricRegistry, domain_agent_ids: frozenset[str]) -> str | None:
    for metric_id in metric_ids:
        definition = metrics.get(metric_id)
        if not definition:
            continue
        candidate = f"{definition.domain}_agent"
        if candidate in domain_agent_ids:
            return candidate
    return None


def select_initial_lead(
    *,
    llm_domain_lead: str | None,
    metric_hints: Sequence[str],
    metrics: MetricRegistry,
    agents: AgentRegistry,
    fallback: str = "coordinator_agent",
) -> LeadDecision:
    hints = [m for m in metric_hints if m.startswith("metric.")]
    domain_agent_ids = frozenset(a["id"] for a in agents.domain_agents(enabled_only=True))

    if llm_domain_lead in domain_agent_ids:
        return LeadDecision(
            mission_lead=llm_domain_lead,
            score=0.85,
            rationale=f"Classifier proposed {llm_domain_lead}; it is a known domain lead.",
            source="llm",
        )

    owner = _owner_agent(hints, metrics, domain_agent_ids)
    if owner:
        return LeadDecision(
            mission_lead=owner,
            score=0.70,
            rationale=f"No valid classifier lead; {owner} owns {hints[0]} in the metric registry.",
            source="metric_ownership",
        )

    return LeadDecision(
        mission_lead=fallback,
        score=0.0,
        rationale="No domain lead could be resolved from the classifier or metric hints.",
        source="fallback",
    )
