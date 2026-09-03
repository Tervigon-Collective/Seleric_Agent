"""Deterministic agent scoring (pasted spec sec. 14).

    AgentScore = 0.35*CapabilityMatch + 0.20*DomainMatch + 0.15*HistoricalAccuracy
               + 0.10*DataAccessMatch + 0.10*Availability + 0.05*Latency + 0.05*Cost

With one candidate per capability in V1 this mostly decorates the choice, but it
is a pure, tested function ready for the point where multiple agents advertise
the same skill.
"""

from __future__ import annotations

from dataclasses import dataclass

WEIGHTS = {
    "capability_match": 0.35,
    "domain_match": 0.20,
    "historical_accuracy": 0.15,
    "data_access_match": 0.10,
    "availability": 0.10,
    "latency": 0.05,
    "cost": 0.05,
}

_LATENCY_SCORE = {"low": 1.0, "medium": 0.6, "high": 0.3}
_COST_SCORE = {"low": 1.0, "medium": 0.6, "high": 0.3}


@dataclass
class AgentScore:
    agent_id: str
    score: float
    components: dict[str, float]


def _ratio(have: set[str], want: set[str]) -> float:
    if not want:
        return 1.0
    return len(have & want) / len(want)


def score_agent(
    agent: dict,
    *,
    required_capabilities: set[str],
    domain_hints: set[str] | None = None,
    required_mcp: set[str] | None = None,
) -> AgentScore:
    domain_hints = domain_hints or set()
    required_mcp = required_mcp or set()

    caps = set(agent.get("capabilities") or [])
    domains = set(agent.get("domains") or [])
    mcp = set(agent.get("mcp_capabilities") or [])

    components = {
        "capability_match": _ratio(caps, set(required_capabilities)),
        "domain_match": _ratio(domains, domain_hints) if domain_hints else 1.0,
        "historical_accuracy": float(agent.get("trust_score", 0.8)),
        "data_access_match": _ratio(mcp, required_mcp) if required_mcp else 1.0,
        "availability": 0.0 if agent.get("status") == "unavailable" else 1.0,
        "latency": _LATENCY_SCORE.get(str(agent.get("latency_class", "medium")), 0.6),
        "cost": _COST_SCORE.get(str(agent.get("cost_class", "medium")), 0.6),
    }
    total = round(sum(WEIGHTS[k] * v for k, v in components.items()), 6)
    return AgentScore(agent_id=str(agent.get("id", "")), score=total, components=components)


def select_agent(
    candidates: list[dict],
    *,
    required_capabilities: set[str],
    domain_hints: set[str] | None = None,
    required_mcp: set[str] | None = None,
) -> AgentScore | None:
    if not candidates:
        return None
    ranked = sorted(
        (
            score_agent(
                agent,
                required_capabilities=required_capabilities,
                domain_hints=domain_hints,
                required_mcp=required_mcp,
            )
            for agent in candidates
        ),
        key=lambda s: (-s.score, s.agent_id),
    )
    return ranked[0]
