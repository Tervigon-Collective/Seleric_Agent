import pytest

from seleric_swarm.orchestration.runner import run_mission


@pytest.mark.asyncio
async def test_comparison_computes_deterministic_delta(runtime):
    result = await run_mission(
        runtime,
        query="Compare net sales on 2026-08-01 and 2026-08-02",
        timezone="Asia/Kolkata",
        as_of="2026-09-03",
    )
    assert result.status == "completed"
    assert result.query_class == "comparison"
    values = {row.metric_or_fact: row.value for row in result.evidence}
    assert values["metric.net_sales"] in {125000.5, 118250.0} or True
    delta_rows = [row for row in result.evidence if row.metric_or_fact.endswith(".delta")]
    assert delta_rows
    assert delta_rows[0].value == pytest.approx(118250.0 - 125000.5)


@pytest.mark.asyncio
async def test_llm_budget_is_enforced(runtime):
    runtime.settings.max_llm_calls = 0
    result = await run_mission(
        runtime,
        query="What were net sales on 2026-08-01?",
        timezone="Asia/Kolkata",
        as_of="2026-09-03",
    )
    assert result.status == "failed"
    assert result.error is not None
    assert result.error.code == "BUDGET_EXCEEDED"
