import pytest


@pytest.mark.asyncio
async def test_gateway_allowlist_and_live_seleric(runtime):
    gw = runtime.mcp
    assert "seleric.metrics_query" in gw.capabilities
    # Sprint 5: reads are open to any caller identity; writes stay v3_agent-only.
    with pytest.raises(PermissionError):
        await gw.call(
            agent_id="commerce_agent",
            capability="seleric.actions_propose",
            arguments={},
        )
    row = await gw.call(
        agent_id="v3_agent",
        capability="seleric.metrics_query",
        arguments={
            "measures": ["commerce_net_revenue_daily"],
            "time_range": {"start": "2026-08-01", "end": "2026-08-01"},
        },
    )
    assert row.get("error") is None
    assert row.get("rows")
