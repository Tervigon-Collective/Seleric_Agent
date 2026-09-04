import pytest

from seleric_swarm.orchestration.runner import run_mission


@pytest.mark.asyncio
async def test_cac_and_net_sales_transfers_leadership_with_evidence(runtime):
    result = await run_mission(
        runtime,
        query="What were CAC and net sales on 2026-09-02?",
        timezone="Asia/Kolkata",
        as_of="2026-09-03",
    )
    raw = runtime.store.get_raw(result.mission_id) or {}

    assert result.status == "completed"
    assert result.query_class == "lookup"
    assert result.initial_mission_lead == "performance_agent"
    assert result.mission_lead == "commerce_agent"
    assert result.leadership_epoch >= 1
    assert len(result.handoff_history) == 1

    handoff = result.handoff_history[0]
    assert handoff.from_agent == "performance_agent"
    assert handoff.to_agent == "commerce_agent"
    assert handoff.evidence_refs
    assert handoff.unresolved_question
    assert handoff.reason

    values = {row.metric_or_fact: row.value for row in result.evidence}
    assert isinstance(values.get("metric.cac"), (int, float))
    assert isinstance(values.get("metric.net_sales"), (int, float))
    assert all(claim.support_refs and claim.gate_status == "passed" for claim in result.claims)

    capabilities = [call["capability"] for call in runtime.mcp.invocations]
    assert "seleric.metrics_query" in capabilities
    assert raw.get("mcp_called") is True


@pytest.mark.asyncio
async def test_performance_only_cac_is_now_a_supported_lookup(runtime):
    result = await run_mission(
        runtime,
        query="What is CAC on 2026-09-02?",
        timezone="Asia/Kolkata",
        as_of="2026-09-03",
    )
    raw = runtime.store.get_raw(result.mission_id) or {}
    assert result.status == "completed"
    assert result.query_class == "lookup"
    assert result.mission_lead == "performance_agent"
    assert result.handoff_history == []
    assert raw.get("mcp_called") is True

    values = {row.metric_or_fact: row.value for row in result.evidence}
    assert isinstance(values.get("metric.cac"), (int, float))

    capabilities = [call["capability"] for call in runtime.mcp.invocations]
    assert "seleric.metrics_query" in capabilities


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query,initial_lead,final_lead,metrics",
    [
        (
            "What were CAC and net sales on 2026-08-01?",
            "performance_agent",
            "commerce_agent",
            ("metric.cac", "metric.net_sales"),
        ),
        (
            "What were net profit and net sales on 2026-08-01?",
            "finance_agent",
            "commerce_agent",
            ("metric.net_profit", "metric.net_sales"),
        ),
        (
            "How many units sold and what were net sales on 2026-08-01?",
            "product_agent",
            "commerce_agent",
            ("metric.units_sold", "metric.net_sales"),
        ),
        (
            "What was the ATC rate and net sales on 2026-08-01?",
            "funnel_agent",
            "commerce_agent",
            ("metric.atc_rate", "metric.net_sales"),
        ),
        (
            "What was the repeat rate and net sales on 2026-08-01?",
            "customer_agent",
            "commerce_agent",
            ("metric.repeat_rate", "metric.net_sales"),
        ),
        (
            "What was the refunded amount and net sales on 2026-08-01?",
            "operations_agent",
            "commerce_agent",
            ("metric.refunded_amount_excl_tax", "metric.net_sales"),
        ),
        (
            "What were attributed net revenue and CAC on 2026-08-01?",
            "performance_agent",
            "attribution_agent",
            ("metric.attributed_net_revenue", "metric.cac"),
        ),
    ],
)
async def test_two_domain_lookup_transfers_once_and_returns_both_metrics(
    runtime, query, initial_lead, final_lead, metrics
):
    result = await run_mission(
        runtime,
        query=query,
        timezone="Asia/Kolkata",
        as_of="2026-09-03",
    )
    assert result.status == "completed", (result.status, result.error)
    assert result.query_class == "lookup"
    assert result.initial_mission_lead == initial_lead
    assert result.mission_lead == final_lead
    assert result.leadership_epoch >= 1
    assert len(result.handoff_history) == 1
    assert result.handoff_history[0].from_agent == initial_lead
    assert result.handoff_history[0].to_agent == final_lead

    values = {row.metric_or_fact: row.value for row in result.evidence}
    for metric_id in metrics:
        assert isinstance(values.get(metric_id), (int, float)), (metric_id, values)
    assert all(claim.support_refs and claim.gate_status == "passed" for claim in result.claims)
    assert "seleric.metrics_query" in [call["capability"] for call in runtime.mcp.invocations]
