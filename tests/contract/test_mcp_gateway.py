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
async def test_newly_wired_read_capabilities_are_live(runtime):
    """The read tools wired in the A2 amendment resolve as live capabilities
    (routing works), and the catalogue-only ones return a usable shape without
    external ad credentials."""
    gw = runtime.mcp
    for cap in (
        "seleric.catalogue_resolve_brand",
        "seleric.meta_insights_query",
        "seleric.meta_accounts_list",
        "seleric.google_query_gaql",
        "seleric.google_accounts_list_accessible",
    ):
        assert cap in gw.capabilities
    # insights_explain was removed from the adapter (dead — never called).
    assert "seleric.insights_explain" not in gw.capabilities

    # catalogue_resolve_brand is Cube/catalogue-only (no ad creds) — exercise it
    # live and expect a dict with a brand_id for the default brand.
    resolved = await gw.call(
        agent_id="v3_agent",
        capability="seleric.catalogue_resolve_brand",
        arguments={"text": "Tilting Heads"},
    )
    assert isinstance(resolved, dict)
    assert resolved.get("brand_id")

    # meta_insights_query routes (the server owns scope/credential enforcement);
    # a permission/argument error dict is an acceptable "server responded" result
    # here — we only assert it did not blow up the transport.
    insights = await gw.call(
        agent_id="v3_agent",
        capability="seleric.meta_insights_query",
        arguments={
            "account_id": "act_smoke",
            "level": "account",
            "fields": ["spend"],
            "time_range": {"preset": "last_30d"},
        },
    )
    assert isinstance(insights, dict)
