from seleric_swarm.llm.adapters.fake import classify_lookup_query


def test_v1_routing_matrix(runtime):
    # Domain support is data-driven: config/agent_registry.yaml's `enabled: true`
    # is the single source of truth, not a hardcoded (query_class, agent) set.
    enabled = {a["id"] for a in runtime.agents.domain_agents(enabled_only=True)}
    assert {
        "commerce_agent",
        "performance_agent",
        "finance_agent",
        "funnel_agent",
        "attribution_agent",
        "product_agent",
        "customer_agent",
        "operations_agent",
    } <= enabled
    assert "inventory_agent" not in enabled
    assert "procurement_agent" not in enabled
    assert "technical_agent" not in enabled


def test_classify_unsupported_does_not_select_commerce_for_cac():
    result = classify_lookup_query("Why did CAC increase?", "Asia/Kolkata", "2026-09-03")
    assert result["query_class"] == "unsupported"
    assert result["domain_lead"] == "performance_agent"


def test_classify_finance_lookup():
    result = classify_lookup_query("What was net profit yesterday?", "Asia/Kolkata", "2026-09-03")
    assert result["query_class"] == "lookup"
    assert result["domain_lead"] == "finance_agent"
    assert "metric.net_profit" in result["metric_hints"]


def test_classify_combined_lookup_starts_with_performance():
    result = classify_lookup_query(
        "What were CAC and net sales on 2026-08-01?",
        "Asia/Kolkata",
        "2026-09-03",
    )
    assert result["query_class"] == "lookup"
    assert result["domain_lead"] == "performance_agent"
    assert "metric.cac" in result["metric_hints"]
    assert "metric.net_sales" in result["metric_hints"]


def test_classify_top_selling_products_goes_to_product_not_commerce():
    result = classify_lookup_query(
        "What is top seeling products for today",
        "Asia/Kolkata",
        "2026-09-04",
    )
    assert result["query_class"] == "lookup"
    assert result["domain_lead"] == "product_agent"
    assert result["metric_hints"] == ["metric.units_sold"]


def test_classify_gross_and_net_keeps_both_hints():
    result = classify_lookup_query(
        "What is gross sale and net sale for today",
        "Asia/Kolkata",
        "2026-09-04",
    )
    assert result["query_class"] == "lookup"
    assert result["domain_lead"] == "commerce_agent"
    assert "metric.gross_sales" in result["metric_hints"]
    assert "metric.net_sales" in result["metric_hints"]


def test_classify_net_profit_and_net_sales_starts_with_finance():
    result = classify_lookup_query(
        "What were net profit and net sales on 2026-08-01?",
        "Asia/Kolkata",
        "2026-09-03",
    )
    assert result["query_class"] == "lookup"
    assert result["domain_lead"] == "finance_agent"
    assert "metric.net_profit" in result["metric_hints"]
    assert "metric.net_sales" in result["metric_hints"]


def test_classify_units_sold_and_net_sales_starts_with_product():
    result = classify_lookup_query(
        "How many units sold and what were net sales on 2026-08-01?",
        "Asia/Kolkata",
        "2026-09-03",
    )
    assert result["query_class"] == "lookup"
    assert result["domain_lead"] == "product_agent"
    assert "metric.units_sold" in result["metric_hints"]
    assert "metric.net_sales" in result["metric_hints"]


def test_classify_attributed_revenue_and_cac_starts_with_performance():
    result = classify_lookup_query(
        "What were attributed net revenue and CAC on 2026-08-01?",
        "Asia/Kolkata",
        "2026-09-03",
    )
    assert result["query_class"] == "lookup"
    assert result["domain_lead"] == "performance_agent"
    assert "metric.attributed_net_revenue" in result["metric_hints"]
    assert "metric.cac" in result["metric_hints"]


def test_classify_best_performing_channel_is_attribution_lookup():
    result = classify_lookup_query(
        "What is the best performing channel is the last 3 days",
        "Asia/Kolkata",
        "2026-09-04",
    )
    assert result["query_class"] == "lookup"
    assert result["domain_lead"] == "attribution_agent"
    assert "metric.attributed_net_revenue" in result["metric_hints"]
    assert result["time_range"]["relative_token"] == "last_3d"


def test_last_3_days_window_is_inclusive_of_as_of():
    from seleric_swarm.services.time_range import window_from_query

    window = window_from_query(
        "What is the best performing channel is the last 3 days",
        "Asia/Kolkata",
        "2026-09-03",
    )
    assert window is not None
    assert window.start == "2026-09-01"
    assert window.end == "2026-09-03"
    assert window.relative_token == "last_3d"


# ---------------------------------------------------------------------------
# Registry authority: domain_lead override when canonical hints are resolved
# ---------------------------------------------------------------------------

def test_classify_best_selling_product_5_months_routes_to_product():
    """Fake LLM returns last_5m token; product_agent must be the initial domain lead.

    This validates that the registry overrides any LLM-suggested domain_lead when
    canonical metric hints are present — the root cause of the wasted commerce
    observer wave in the 'best selling product in last 5 months' trace.
    """
    result = classify_lookup_query(
        "What is the best selling product in the last 5 months",
        "Asia/Kolkata",
        "2026-09-03",
    )
    assert result["query_class"] == "lookup"
    # Registry owns units_sold → product_agent; must not be commerce_agent
    assert result["domain_lead"] == "product_agent", (
        f"expected product_agent (registry authority), got {result['domain_lead']}"
    )
    # Only the ranking metric — no gross_sales / orders / net_sales
    assert result["metric_hints"] == ["metric.units_sold"], (
        f"expected only metric.units_sold, got {result['metric_hints']}"
    )
    # 5-month relative token
    assert result["time_range"]["relative_token"] == "last_5m", (
        f"expected last_5m token, got {result['time_range']['relative_token']}"
    )


def test_classify_best_selling_last_n_months_resolves_correct_window():
    """window_from_query must resolve 'last 5 months' with as_of 2026-09-03 to 2026-04-03."""
    from seleric_swarm.services.time_range import window_from_query

    window = window_from_query(
        "What is the best selling product in the last 5 months",
        "Asia/Kolkata",
        "2026-09-03",
    )
    assert window is not None
    assert window.start == "2026-04-03"
    assert window.end == "2026-09-03"
    assert window.relative_token == "last_5m"


def test_classify_ranking_metric_hints_are_minimal():
    """Ranking queries must not include revenue/order metrics alongside the rank metric."""
    result = classify_lookup_query(
        "What is the best selling product yesterday",
        "Asia/Kolkata",
        "2026-09-04",
    )
    assert result["query_class"] == "lookup"
    assert result["domain_lead"] == "product_agent"
    # Strictly only the ranking metric — not gross_sales/orders/net_sales
    for hint in result["metric_hints"]:
        assert hint in {"metric.units_sold"}, (
            f"unexpected metric hint {hint!r} in ranking query"
        )
