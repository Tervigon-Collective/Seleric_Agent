from seleric_swarm.llm.adapters.fake import classify_lookup_query


def test_v1_routing_matrix(runtime):
    # Domain support is data-driven: config/agent_registry.yaml's `enabled: true`
    # is the single source of truth, not a hardcoded (query_class, agent) set.
    enabled = {a["id"] for a in runtime.agents.domain_agents(enabled_only=True)}
    assert {"commerce_agent", "performance_agent", "finance_agent", "funnel_agent"} <= enabled
    assert "inventory_agent" not in enabled
    assert "procurement_agent" not in enabled
    assert "technical_agent" not in enabled


def test_classify_unsupported_does_not_select_commerce_for_cac():
    result = classify_lookup_query("Why did CAC increase?", "Asia/Kolkata", "2026-09-03")
    assert result["query_class"] == "unsupported"
    assert result["domain_lead"] == "performance_agent"


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
