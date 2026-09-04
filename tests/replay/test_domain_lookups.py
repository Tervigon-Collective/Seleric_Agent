import pytest

from seleric_swarm.orchestration.runner import run_mission


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query,lead,metric",
    [
        ("What was attributed net revenue on 2026-08-01?", "attribution_agent", "metric.attributed_net_revenue"),
        ("What was the ATC rate on 2026-08-01?", "funnel_agent", "metric.atc_rate"),
        ("What was net profit yesterday?", "finance_agent", "metric.net_profit"),
        ("How many units sold on 2026-08-01?", "product_agent", "metric.units_sold"),
        ("What was the refunded amount yesterday?", "operations_agent", "metric.refunded_amount_excl_tax"),
        ("What was CAC on 2026-08-01?", "performance_agent", "metric.cac"),
        ("What was the repeat rate on 2026-08-01?", "customer_agent", "metric.repeat_rate"),
    ],
)
async def test_catalogue_domain_lookup_calls_live_mcp(runtime, query, lead, metric):
    result = await run_mission(
        runtime,
        query=query,
        timezone="Asia/Kolkata",
        as_of="2026-09-03",
    )
    raw = runtime.store.get_raw(result.mission_id) or {}
    assert result.status == "completed", (result.status, result.error)
    assert result.query_class == "lookup"
    assert result.mission_lead == lead
    assert result.handoff_history == []
    assert raw.get("mcp_called") is True
    values = {row.metric_or_fact: row.value for row in result.evidence}
    assert isinstance(values.get(metric), (int, float))
    assert "seleric.metrics_query" in [call["capability"] for call in runtime.mcp.invocations]


@pytest.mark.asyncio
async def test_finance_comparison_calls_live_mcp(runtime):
    result = await run_mission(
        runtime,
        query="Compare net profit on 2026-08-01 and 2026-08-02",
        timezone="Asia/Kolkata",
        as_of="2026-09-03",
    )
    assert result.status == "completed", (result.status, result.error)
    assert result.query_class == "comparison"
    assert result.mission_lead == "finance_agent"
    assert any(row.metric_or_fact.endswith(".delta") for row in result.evidence)
    assert "seleric.metrics_query" in [call["capability"] for call in runtime.mcp.invocations]


@pytest.mark.asyncio
async def test_commerce_observer_fetches_gross_and_net_together(runtime):
    result = await run_mission(
        runtime,
        query="What is gross sale and net sale on 2026-08-01?",
        timezone="Asia/Kolkata",
        as_of="2026-09-03",
    )
    assert result.status == "completed", (result.status, result.error)
    assert result.query_class == "lookup"
    assert result.mission_lead == "commerce_agent"
    assert result.active_specialist == "observer_agent"
    values = {row.metric_or_fact: row.value for row in result.evidence}
    assert isinstance(values.get("metric.gross_sales"), (int, float))
    assert isinstance(values.get("metric.net_sales"), (int, float))
    assert len(result.claims) >= 2


@pytest.mark.asyncio
async def test_product_observer_ranks_top_sellers_not_period_total(runtime):
    result = await run_mission(
        runtime,
        query="What is top seeling products on 2026-08-01?",
        timezone="Asia/Kolkata",
        as_of="2026-09-03",
    )
    assert result.status == "completed", (result.status, result.error)
    assert result.mission_lead == "product_agent"
    assert result.active_specialist == "observer_agent"
    titled = [row for row in result.evidence if (row.dimensions or {}).get("product_title")]
    assert len(titled) >= 2
    assert all(row.metric_or_fact == "metric.units_sold" for row in titled)
    ranked_query = next(
        call for call in runtime.mcp.invocations if call["capability"] == "seleric.metrics_query"
    )
    assert ranked_query["arguments"].get("dimensions") == ["product_title"]
    assert ranked_query["arguments"].get("limit") == 10
    assert ranked_query["agent_id"] == "product_agent"


@pytest.mark.asyncio
async def test_product_observer_honours_top_n(runtime):
    result = await run_mission(
        runtime,
        query="What were top 3 selling products on 2026-08-01?",
        timezone="Asia/Kolkata",
        as_of="2026-09-03",
    )
    assert result.status == "completed", (result.status, result.error)
    titled = [row for row in result.evidence if (row.dimensions or {}).get("product_title")]
    assert 1 <= len(titled) <= 3
    ranked_query = next(
        call for call in reversed(runtime.mcp.invocations) if call["capability"] == "seleric.metrics_query"
    )
    assert ranked_query["arguments"].get("limit") == 3


@pytest.mark.asyncio
async def test_best_performing_channel_last_3_days_ranks_by_channel(runtime):
    runtime.settings.mission_timeout_s = 45
    result = await run_mission(
        runtime,
        query="What is the best performing channel is the last 3 days",
        timezone="Asia/Kolkata",
        as_of="2026-09-03",
    )
    if result.error and result.error.code == "TIMEOUT":
        pytest.skip("Cube timed out on attributed revenue by channel")
    assert result.status == "completed", (result.status, result.error)
    assert result.query_class == "lookup"
    assert result.mission_lead == "attribution_agent"
    titled = [row for row in result.evidence if (row.dimensions or {}).get("lt_channel")]
    assert titled, result.evidence
    assert all(row.metric_or_fact == "metric.attributed_net_revenue" for row in titled)
    ranked_query = next(
        call for call in runtime.mcp.invocations if call["capability"] == "seleric.metrics_query"
    )
    assert ranked_query["arguments"].get("dimensions") == ["lt_channel"]
    assert ranked_query["arguments"]["time_range"] == {"start": "2026-09-01", "end": "2026-09-03"}
    assert ranked_query["agent_id"] == "attribution_agent"


@pytest.mark.asyncio
async def test_commerce_comparison_keeps_observer_on_domain_metric(runtime):
    result = await run_mission(
        runtime,
        query="Compare net sales on 2026-08-01 and 2026-08-02",
        timezone="Asia/Kolkata",
        as_of="2026-09-03",
    )
    assert result.status == "completed", (result.status, result.error)
    assert result.query_class == "comparison"
    assert result.mission_lead == "commerce_agent"
    assert result.active_specialist == "observer_agent"
    assert any(row.metric_or_fact == "metric.net_sales" for row in result.evidence)
    assert any(row.metric_or_fact.endswith(".delta") for row in result.evidence)
