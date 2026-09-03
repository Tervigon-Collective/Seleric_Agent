from seleric_swarm.llm.adapters.fake import classify_lookup_query
from seleric_swarm.orchestration.graph import V1_SUPPORTED


def test_v1_routing_matrix():
    assert ("lookup", "commerce_agent") in V1_SUPPORTED
    assert ("comparison", "commerce_agent") in V1_SUPPORTED
    assert ("lookup", "performance_agent") in V1_SUPPORTED


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
