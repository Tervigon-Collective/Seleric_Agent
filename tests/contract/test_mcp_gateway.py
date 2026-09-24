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


@pytest.mark.asyncio
async def test_read_capabilities_are_live_and_ad_platform_tools_are_not(runtime):
    """Catalogue resolution capabilities route live; the third-party ad-platform
    tools are no longer part of the agent's surface."""
    gw = runtime.mcp
    for cap in ("seleric.catalogue_resolve_brand", "seleric.catalogue_resolve_values"):
        assert cap in gw.capabilities
    for cap in (
        "seleric.meta_insights_query",
        "seleric.meta_accounts_list",
        "seleric.google_query_gaql",
        "seleric.google_accounts_list_accessible",
    ):
        assert cap not in gw.capabilities
    # insights_explain was removed from the adapter (dead — never called).
    assert "seleric.insights_explain" not in gw.capabilities

    resolved = await gw.call(
        agent_id="v3_agent",
        capability="seleric.catalogue_resolve_brand",
        arguments={"text": "Tilting Heads"},
    )
    assert isinstance(resolved, dict)
    assert resolved.get("brand_id")

    values = await gw.call(
        agent_id="v3_agent",
        capability="seleric.catalogue_resolve_values",
        arguments={"text": "orders from whatsapp"},
    )
    assert isinstance(values, dict)
    assert values.get("status") in ("ok", "warming")
