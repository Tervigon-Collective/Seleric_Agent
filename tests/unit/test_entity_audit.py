"""entity_audit: the derived facts of a performance analysis, computed in code (P1.2).

The fixture is the real 24-campaign request of 2026-10-17 (thread_0a9caa, MS3-9bbcd9c0f7) with the names anonymised: 11 campaigns
recorded no orders and absorbed 17,442 of 56,554 spend (30.8%); no campaign reached 5 orders."""

from __future__ import annotations

import pytest

from seleric_swarm.agent.local_tools import build_local_tools
from seleric_swarm.agent.mcp_toolset import TurnState
from seleric_swarm.toolsets import entity_audit as ea

# (name, spend, orders, revenue, ctr)
REAL = [
    ("C01", 5001.38, 3, 4941.39, .02658), ("C02", 4391.15, 2, 4321.82, .01118), ("C03", 4183.57, 2, 2483.81, .02315),
    ("C04", 3098.85, 1, 2166.8, .01436), ("C05", 2943.09, 1, 2983.75, .02336), ("C06", 2941.0, 2, 4072.82, .0153),
    ("C07", 2746.93, 3, 5061.79, .02105), ("C08", 2744.71, 3, 7186.31, .02701), ("C09", 2686.08, 2, 5180.21, .01634),
    ("C10", 2582.89, 1, 1270.34, .01283), ("C11", 2301.0, None, None, .036), ("C12", 2279.25, 2, 3791.06, .01213),
    ("C13", 2242.8, None, None, .01719), ("C14", 2072.94, 1, 1524.58, .01231), ("C15", 2015.25, None, None, .01623),
    ("C16", 1871.13, None, None, .01622), ("C17", 1857.63, None, None, .02565), ("C18", 1790.65, None, None, .00961),
    ("C19", 1683.38, None, None, .01182), ("C20", 1439.87, 1, 1843.07, .01215), ("C21", 1405.86, None, None, .01277),
    ("C22", 1360.73, None, None, .01983), ("C23", 608.51, None, None, .01678), ("C24", 305.41, None, None, .00947),
]
ROLES = ea.Roles(spend="ad_spend", returned="funnel_order_revenue", orders="orders", signal="ctr")
LABELS = {"ad_spend": "Ad spend", "funnel_order_revenue": "Attributed revenue", "orders": "Orders", "ctr": "CTR"}


def _entities(rows=REAL) -> list[ea.Entity]:
    return [ea.Entity(n, s, r, o, c) for n, s, o, r, c in rows]


def test_the_spend_on_campaigns_with_no_orders_is_found():
    a = ea.audit(_entities(), labels=LABELS, roles=ROLES)
    assert a.facts["idle_count"] == 11
    assert a.facts["idle_spend"] == pytest.approx(17442, abs=1)
    assert a.facts["idle_share_pct"] == pytest.approx(30.8, abs=0.05)
    assert "11 of 24 entities recorded no orders" in a.text() and "30.8% of spend" in a.text()
    assert "₹17,442" in a.text() and "₹56,554" in a.text()


def test_too_few_orders_is_an_early_signal_never_a_verdict():
    text = ea.audit(_entities(), labels=LABELS, roles=ROLES).text()
    assert "EARLY SIGNAL" in text and "EVIDENCED" not in text  # nobody has 5 orders


def test_with_enough_orders_best_and_worst_are_named_with_the_shortfall():
    rows = [("A", 1000.0, 20, 3000.0, .02), ("B", 1000.0, 10, 500.0, .02), ("C", 1000.0, 2, 9000.0, .02), ("D", 900.0, 8, 1800.0, .02)]
    text = ea.audit(_entities(rows), labels=LABELS, roles=ROLES).text()
    assert "EVIDENCED (≥5 orders) best: A:" in text and "3.00x" in text
    assert "returning less than spend: B:" in text and "shortfall ₹500" in text
    assert "EARLY SIGNAL" in text and "C: Attributed revenue ₹9,000" in text  # 4.5x on 2 orders is not a winner


def test_attention_that_does_not_convert_is_flagged():
    text = ea.audit(_entities(), labels=LABELS, roles=ROLES).text()
    assert "ATTENTION WITHOUT ORDERS" in text and "C11 (₹2,301 spend)" in text  # the 3.60% CTR campaign with no revenue


def test_roles_are_picked_from_the_catalogue_ids_present():
    assert ea.pick_roles(["ad_spend", "orders", "funnel_order_revenue", "ctr"]) == ROLES
    assert ea.pick_roles(["orders", "net_sales"]) is None  # no spend, nothing to audit
    assert ea.pick_roles(["product_ad_spend", "net_sales"]).returned == "net_sales"  # type: ignore[union-attr]


def test_an_entity_with_no_spend_is_not_part_of_a_spend_audit():
    rows = [{"campaign_name": "A", "ad_spend": "0", "orders": "4"}, {"campaign_name": "B", "ad_spend": "10", "orders": None}]
    assert [e.name for e in ea.entities_of(rows, "campaign_name", ROLES)] == ["B"]


@pytest.mark.asyncio
async def test_the_tool_audits_a_multi_view_result_and_keeps_entities_a_view_has_no_row_for():
    names = [r[0] for r in REAL]
    paid = {"query_id": "qp", "columns": ["campaign_name", "ad_spend", "ctr"], "provenance": {"metric_ids": ["ad_spend", "ctr"], "currency": "INR"},
            "rows": [{"campaign_name": n, "ad_spend": str(s), "ctr": str(c)} for n, s, _o, _r, c in REAL]}
    commerce = {"query_id": "qc", "columns": ["campaign_name", "orders", "funnel_order_revenue"],
                "provenance": {"metric_ids": ["orders", "funnel_order_revenue"], "currency": "INR"},
                "rows": [{"campaign_name": n, "orders": str(o), "funnel_order_revenue": str(r)} for n, _s, o, r, _c in REAL if o]}
    state = TurnState(payloads=[{"query_id": "qm", "composed": True, "parts": [commerce, paid]}])
    state.evidence.append({"tool": "metrics_query", "query_id": "qm",
                           "args": {"filters": [{"dimension": "campaign_name", "operator": "equals", "values": names}]}})
    tool = next(t for t in build_local_tools(state) if t.__name__ == "audit_entities")

    class Deps:
        from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta, CatalogueSnapshot
        catalogue = CatalogueSnapshot(metrics=(
            CatalogueMetricMeta(id="ad_spend", label="Ad spend", raw={"unit": "INR", "aggregation": "additive"}),
            CatalogueMetricMeta(id="orders", label="Orders", raw={"unit": "count", "aggregation": "additive"}),
            CatalogueMetricMeta(id="funnel_order_revenue", label="Attributed revenue", raw={"unit": "INR", "aggregation": "additive"}),
            CatalogueMetricMeta(id="ctr", label="CTR", raw={"unit": "ratio", "aggregation": "ratio"})))

    class Ctx:
        deps = Deps()

    view = await tool(Ctx(), query_ids=["qm"])
    assert view.success is True
    assert "11 of 24 entities recorded no orders" in view.summary and "30.8% of spend" in view.summary
    # its figures are quotable: they joined the turn's grounding pool
    assert any("helper_figures" in p for p in state.payloads if isinstance(p, dict))
    missing = await tool(Ctx(), query_ids=["q_nope"])
    assert missing.success is False and "q_nope" in missing.summary


def test_unattributed_spend_is_reported_apart_and_never_called_a_leak():
    # live 2026-10-19 r04: the "(not set)" product row (34% of spend) was reported as an entity that "recorded no orders"
    rows = [("(not set)", 5000.0, None, None, None), ("A", 1000.0, 20, 3000.0, .02), ("B", 1000.0, None, None, .02)]
    a = ea.audit(_entities(rows), labels=LABELS, roles=ROLES)
    text = a.text()
    assert "UNATTRIBUTED: ₹5,000 = 71.4% of all spend" in text and "not an entity" in text
    assert a.facts["idle_count"] == 1 and "B ₹1,000" in text and "(not set) ₹" not in text.split("NO ORDERS")[1]
    assert a.facts["total_spend"] == 2000.0  # the portfolio is the entities, not the unattributed row


def test_a_return_on_spend_ratio_stands_in_for_a_missing_revenue_measure():
    roles = ea.pick_roles(["product_ad_spend", "product_orders", "product_net_roas"])
    assert roles is not None and roles.returned is None and roles.roas == "product_net_roas"
    rows = [{"p": "A", "product_ad_spend": "1000", "product_orders": "20", "product_net_roas": "1.5"},
            {"p": "B", "product_ad_spend": "1000", "product_orders": "10", "product_net_roas": "0.5"}]
    a = ea.audit(ea.entities_of(rows, "p", roles), labels={"product_ad_spend": "Product ad spend", "product_net_roas": "Product net ROAS"}, roles=roles)
    assert "EVIDENCED (≥5 orders) best: A:" in a.text() and "shortfall ₹500" in a.text()


def test_the_no_orders_list_ranks_by_spend_with_amounts():
    names = ea.audit(_entities(), labels=LABELS, roles=ROLES).text().split("Largest, in order: ")[1].split(".")[0]
    assert names.startswith("C11 ₹2,301; C13 ₹2,243; C15 ₹2,015") and names.count(";") == 4  # five named, largest first


@pytest.mark.asyncio
async def test_without_a_named_list_the_spend_view_still_leads_so_no_entity_is_dropped():
    # live 2026-10-19 r01: no entity list was named, so the 13-row orders view led and 107 of 120 campaigns vanished from the audit
    paid = {"query_id": "qp", "columns": ["campaign_name", "ad_spend"], "provenance": {"metric_ids": ["ad_spend"], "currency": "INR"},
            "rows": [{"campaign_name": n, "ad_spend": str(s)} for n, s, _o, _r, _c in REAL]}
    commerce = {"query_id": "qc", "columns": ["campaign_name", "orders", "funnel_order_revenue"],
                "provenance": {"metric_ids": ["orders", "funnel_order_revenue"], "currency": "INR"},
                "rows": [{"campaign_name": n, "orders": str(o), "funnel_order_revenue": str(r)} for n, _s, o, r, _c in REAL if o]}
    state = TurnState(payloads=[{"query_id": "qm", "composed": True, "parts": [commerce, paid]}])
    tool = next(t for t in build_local_tools(state) if t.__name__ == "audit_entities")

    class Ctx:
        class deps:
            from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta, CatalogueSnapshot
            catalogue = CatalogueSnapshot(metrics=(
                CatalogueMetricMeta(id="ad_spend", label="Ad spend", raw={"unit": "INR", "aggregation": "additive"}),
                CatalogueMetricMeta(id="orders", label="Orders", raw={"unit": "count", "aggregation": "additive"}),
                CatalogueMetricMeta(id="funnel_order_revenue", label="Attributed revenue", raw={"unit": "INR", "aggregation": "additive"})))

    view = await tool(Ctx(), query_ids=["qm"])
    assert view.success is True
    assert "ENTITY AUDIT over 24 entities" in view.summary and "11 of 24 entities recorded no orders" in view.summary
