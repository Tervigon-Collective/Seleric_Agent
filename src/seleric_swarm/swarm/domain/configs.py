"""Declarative config for all seven domain agents (architecture sec. 18-24).

Only Performance / Funnel / Technical are exercised by the reference mission; the
rest are wired through the same ``DomainConfig`` so filling them in is data, not
new orchestration. Owned metrics mirror ``config/agent_registry.yaml`` intent.
"""

from __future__ import annotations

from seleric_swarm.swarm.domain.base import DomainConfig

PERFORMANCE = DomainConfig(
    agent_id="performance_agent",
    domain="performance",
    owned_metrics=["metric.cac", "metric.spend", "metric.cpm", "metric.cpc", "metric.ctr", "metric.roas"],
    frontier_metrics=["metric.cpm", "metric.cpc", "metric.ctr"],
    probe_metrics=["metric.cac", "metric.spend", "metric.cpm", "metric.cpc", "metric.ctr"],
    sentinels={"metric.purchase_cvr": "funnel"},
    downstream={
        "metric.purchase_cvr": "funnel_agent",
        "metric.atc_rate": "funnel_agent",
        "metric.checkout_rate": "funnel_agent",
    },
    handoff_targets=["funnel_agent", "commerce_agent", "finance_agent"],
    ontology=["campaign", "adset", "ad", "creative", "audience", "attribution", "delivery"],
)

FUNNEL = DomainConfig(
    agent_id="funnel_agent",
    domain="funnel",
    owned_metrics=[
        "metric.sessions",
        "metric.pdp_view_rate",
        "metric.atc_rate",
        "metric.checkout_rate",
        "metric.purchase_cvr",
    ],
    frontier_metrics=["metric.sessions", "metric.pdp_view_rate", "metric.atc_rate", "metric.checkout_rate"],
    probe_metrics=[
        "metric.sessions",
        "metric.pdp_view_rate",
        "metric.atc_rate",
        "metric.checkout_rate",
        "metric.purchase_cvr",
    ],
    probe_dimensions=[{}, {"device": "mobile"}, {"device": "desktop"}],
    sentinels={"metric.mobile_lcp_seconds": "technical", "metric.js_error_rate": "technical"},
    downstream={
        "metric.mobile_lcp_seconds": "technical_agent",
        "metric.js_error_rate": "technical_agent",
        "metric.api_error_rate": "technical_agent",
    },
    handoff_targets=["technical_agent", "commerce_agent"],
    ontology=["session", "landing_page", "pdp", "atc", "checkout", "payment", "device", "journey"],
)

TECHNICAL = DomainConfig(
    agent_id="technical_agent",
    domain="technical",
    owned_metrics=["metric.mobile_lcp_seconds", "metric.js_error_rate", "metric.api_error_rate"],
    frontier_metrics=[],  # terminal: technical diagnoses, it does not hand off
    probe_metrics=["metric.mobile_lcp_seconds", "metric.js_error_rate", "metric.api_error_rate"],
    downstream={},
    handoff_targets=["funnel_agent"],
    ontology=["latency", "web_vitals", "javascript_error", "deployment", "incident", "api"],
)

COMMERCE = DomainConfig(
    agent_id="commerce_agent",
    domain="commerce",
    owned_metrics=["metric.net_sales", "metric.gross_sales", "metric.return_rate", "metric.orders"],
    frontier_metrics=["metric.gross_sales", "metric.orders"],
    probe_metrics=["metric.net_sales", "metric.gross_sales", "metric.return_rate"],
    downstream={"metric.purchase_cvr": "funnel_agent", "metric.mobile_lcp_seconds": "technical_agent"},
    handoff_targets=["funnel_agent", "finance_agent", "inventory_agent"],
    ontology=["order", "sku", "product", "pricing", "discount", "return", "marketplace"],
)

FINANCE = DomainConfig(
    agent_id="finance_agent",
    domain="finance",
    owned_metrics=["metric.net_profit", "metric.contribution_margin", "metric.cogs", "metric.payment_fees"],
    frontier_metrics=["metric.cogs", "metric.payment_fees"],
    probe_metrics=["metric.net_profit", "metric.contribution_margin", "metric.cogs"],
    downstream={"metric.return_rate": "commerce_agent"},
    handoff_targets=["commerce_agent", "inventory_agent", "procurement_agent"],
    ontology=["gross_profit", "net_profit", "margin", "cogs", "rto", "cash"],
)

INVENTORY = DomainConfig(
    agent_id="inventory_agent",
    domain="inventory",
    owned_metrics=["metric.days_cover", "metric.stockout_probability", "metric.sell_through"],
    frontier_metrics=["metric.days_cover"],
    probe_metrics=["metric.days_cover", "metric.stockout_probability"],
    downstream={},
    handoff_targets=["procurement_agent", "commerce_agent"],
    ontology=["stock_cover", "reorder_point", "ageing", "velocity", "dead_stock"],
)

PROCUREMENT = DomainConfig(
    agent_id="procurement_agent",
    domain="procurement",
    owned_metrics=["metric.vendor_lead_time", "metric.landed_cost", "metric.supplier_reliability"],
    frontier_metrics=["metric.vendor_lead_time"],
    probe_metrics=["metric.vendor_lead_time", "metric.landed_cost"],
    downstream={},
    handoff_targets=["inventory_agent", "finance_agent"],
    ontology=["vendor", "po", "moq", "lead_time", "capacity", "inbound"],
)

ALL_DOMAIN_CONFIGS: dict[str, DomainConfig] = {
    c.agent_id: c
    for c in (PERFORMANCE, FUNNEL, TECHNICAL, COMMERCE, FINANCE, INVENTORY, PROCUREMENT)
}
