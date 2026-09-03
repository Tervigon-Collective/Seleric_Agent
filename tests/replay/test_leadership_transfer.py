import pytest

from seleric_swarm.orchestration.runner import run_mission


@pytest.mark.asyncio
async def test_cac_and_net_sales_transfers_leadership_with_evidence(runtime):
    result = await run_mission(
        runtime,
        query="What were CAC and net sales on 2026-08-01?",
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
    assert values["metric.cac"] == 420.5
    assert values["metric.net_sales"] == 125000.5
    assert all(claim.support_refs and claim.gate_status == "passed" for claim in result.claims)

    capabilities = [call["capability"] for call in runtime.mcp.invocations]
    assert "performance.daily_cac" in capabilities
    assert "commerce.daily_sales" in capabilities
    assert raw.get("mcp_called") is True


@pytest.mark.asyncio
async def test_performance_only_cac_stays_unsupported_without_mcp(runtime):
    result = await run_mission(
        runtime,
        query="What is CAC for last week?",
        timezone="Asia/Kolkata",
        as_of="2026-09-03",
    )
    raw = runtime.store.get_raw(result.mission_id) or {}
    assert result.status == "failed"
    assert result.query_class == "unsupported"
    assert result.error is not None
    assert result.error.code == "ROUTING_UNSUPPORTED"
    assert result.handoff_history == []
    assert raw.get("mcp_called") is False
    assert runtime.mcp.invocations == []
