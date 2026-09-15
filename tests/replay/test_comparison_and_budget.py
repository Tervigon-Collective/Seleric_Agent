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
    assert result.status == "completed", (result.status, result.error)
    assert result.query_class == "comparison"
    day_rows = [row for row in result.evidence if row.metric_or_fact == "metric.net_sales"]
    assert len(day_rows) == 2
    delta_rows = [row for row in result.evidence if row.metric_or_fact.endswith(".delta")]
    assert delta_rows
    # day_rows[0]/[1] are period A (2026-08-01) / period B (2026-08-02) in
    # query order. observer.py::_comparison_deltas computes period_a -
    # period_b (positive = period A higher, matching how the question was
    # asked), not chronological "later minus earlier".
    assert delta_rows[0].value == pytest.approx(float(day_rows[0].value) - float(day_rows[1].value))


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
