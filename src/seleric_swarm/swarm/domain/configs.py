"""Domain wiring for swarm agents (architecture sec. 18-24).

Metric ownership and handoff peers are discovered — not coded:
- owned / probe / frontier ← ``config/metric_registry.yaml``
- handoff targets ← every other domain agent in this wiring
- seleric_module ← ``config/agent_registry.yaml`` when present

Cross-domain RCA uses evidence + registry ownership at runtime
(``DomainAgent.evaluate_handoff``), not a static neighbor list.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from seleric_swarm.registry.agent_registry import AgentRegistry
from seleric_swarm.services.metrics import MetricRegistry
from seleric_swarm.swarm.domain.base import DomainConfig


@dataclass(frozen=True)
class DomainWiring:
    """Non-metric structure for a swarm domain agent."""

    agent_id: str
    domain: str
    # Empty frontier => terminal domain (diagnoses; does not hand off on quiet frontier).
    terminal: bool = False
    probe_dimensions: tuple[dict[str, str], ...] = ({},)
    ontology: tuple[str, ...] = ()
    # Used only when agent_registry has no entry / no seleric_module yet.
    seleric_module: str | None = None


DOMAIN_WIRING: tuple[DomainWiring, ...] = (
    DomainWiring(
        agent_id="performance_agent",
        domain="performance",
        ontology=("campaign", "adset", "ad", "creative", "audience", "attribution", "delivery"),
        seleric_module="paidmedia",
    ),
    DomainWiring(
        agent_id="attribution_agent",
        domain="attribution",
        ontology=("channel", "last_touch", "first_touch", "campaign", "placement", "organic"),
        seleric_module="attribution",
    ),
    DomainWiring(
        agent_id="funnel_agent",
        domain="funnel",
        probe_dimensions=({}, {"device": "mobile"}, {"device": "desktop"}),
        ontology=("session", "landing_page", "pdp", "atc", "checkout", "payment", "device", "journey"),
        seleric_module="webanalytics",
    ),
    DomainWiring(
        agent_id="technical_agent",
        domain="technical",
        terminal=True,
        ontology=("latency", "web_vitals", "javascript_error", "deployment", "incident", "api"),
    ),
    DomainWiring(
        agent_id="commerce_agent",
        domain="commerce",
        ontology=("order", "sku", "product", "pricing", "discount", "return", "marketplace"),
        seleric_module="commerce",
    ),
    DomainWiring(
        agent_id="product_agent",
        domain="product",
        ontology=("sku", "variant", "collection", "margin", "velocity", "assortment"),
        seleric_module="product",
    ),
    DomainWiring(
        agent_id="customer_agent",
        domain="customer",
        ontology=("cohort", "ltv", "repeat", "acquisition", "retention"),
        seleric_module="customer",
    ),
    DomainWiring(
        agent_id="operations_agent",
        domain="operations",
        ontology=("refund", "return", "fulfillment", "sla", "ops"),
        seleric_module="operations",
    ),
    DomainWiring(
        agent_id="finance_agent",
        domain="finance",
        ontology=("gross_profit", "net_profit", "margin", "cogs", "rto", "cash"),
        seleric_module="finance",
    ),
    DomainWiring(
        agent_id="inventory_agent",
        domain="inventory",
        ontology=("stock_cover", "reorder_point", "ageing", "velocity", "dead_stock"),
    ),
    DomainWiring(
        agent_id="procurement_agent",
        domain="procurement",
        ontology=("vendor", "po", "moq", "lead_time", "capacity", "inbound"),
    ),
)


def build_domain_configs(
    metrics: MetricRegistry | None = None,
    agents: AgentRegistry | None = None,
) -> dict[str, DomainConfig]:
    """Resolve metrics from the registry; handoff peers = all other domain agents."""
    metrics = metrics or MetricRegistry("config/metric_registry.yaml")
    agents = agents or AgentRegistry("config/agent_registry.yaml")
    peer_ids = [w.agent_id for w in DOMAIN_WIRING]
    out: dict[str, DomainConfig] = {}
    for wire in DOMAIN_WIRING:
        owned = metrics.ids_for_domain(wire.domain)
        probe = [m for m in owned if _metric_flag(metrics, m, "probe", True)]
        frontier = (
            []
            if wire.terminal
            else [m for m in owned if _metric_flag(metrics, m, "frontier", False)]
        )
        reg = agents.get(wire.agent_id) or {}
        module = reg.get("seleric_module")
        if module is None:
            module = wire.seleric_module
        out[wire.agent_id] = DomainConfig(
            agent_id=wire.agent_id,
            domain=wire.domain,
            owned_metrics=list(owned),
            frontier_metrics=frontier,
            probe_metrics=probe or list(owned),
            probe_dimensions=[dict(d) for d in wire.probe_dimensions],
            handoff_targets=[p for p in peer_ids if p != wire.agent_id],
            ontology=list(wire.ontology),
            seleric_module=module,
            terminal=wire.terminal,
        )
    return out


def _metric_flag(metrics: MetricRegistry, metric_id: str, name: str, default: bool) -> bool:
    m = metrics.get(metric_id)
    if m is None:
        return default
    raw: dict[str, Any] = getattr(m, "raw", None) or {}
    if name in raw:
        return bool(raw[name])
    return default


# Default snapshot for imports / tests; prefer build_domain_configs(runtime.metrics).
ALL_DOMAIN_CONFIGS: dict[str, DomainConfig] = build_domain_configs()

PERFORMANCE = ALL_DOMAIN_CONFIGS["performance_agent"]
ATTRIBUTION = ALL_DOMAIN_CONFIGS["attribution_agent"]
FUNNEL = ALL_DOMAIN_CONFIGS["funnel_agent"]
TECHNICAL = ALL_DOMAIN_CONFIGS["technical_agent"]
COMMERCE = ALL_DOMAIN_CONFIGS["commerce_agent"]
PRODUCT = ALL_DOMAIN_CONFIGS["product_agent"]
CUSTOMER = ALL_DOMAIN_CONFIGS["customer_agent"]
OPERATIONS = ALL_DOMAIN_CONFIGS["operations_agent"]
FINANCE = ALL_DOMAIN_CONFIGS["finance_agent"]
INVENTORY = ALL_DOMAIN_CONFIGS["inventory_agent"]
PROCUREMENT = ALL_DOMAIN_CONFIGS["procurement_agent"]
