import pytest

from seleric_swarm.protocols.mcp.gateway import MCPGateway


@pytest.mark.asyncio
async def test_seleric_capability_unavailable_without_env(monkeypatch):
    monkeypatch.delenv("SELERIC_MCP_URL", raising=False)
    monkeypatch.delenv("SELERIC_MCP_TOKEN", raising=False)
    gw = MCPGateway("config/mcp_servers.yaml")
    with pytest.raises(NotImplementedError):
        await gw.call(
            agent_id="finance_agent",
            capability="seleric.metrics_query",
            arguments={"measures": ["net_profit"], "time_range": {"preset": "last_30d"}},
        )


@pytest.mark.asyncio
async def test_seleric_capability_denied_for_wrong_agent(monkeypatch):
    monkeypatch.setenv("SELERIC_MCP_URL", "https://example.invalid/mcp")
    monkeypatch.setenv("SELERIC_MCP_TOKEN", "test-token")
    gw = MCPGateway("config/mcp_servers.yaml")
    with pytest.raises(PermissionError):
        await gw.call(
            agent_id="inventory_agent",
            capability="seleric.metrics_query",
            arguments={"measures": ["net_profit"], "time_range": {"preset": "last_30d"}},
        )


@pytest.mark.asyncio
async def test_module_is_pinned_server_side(monkeypatch):
    monkeypatch.setenv("SELERIC_MCP_URL", "https://example.invalid/mcp")
    monkeypatch.setenv("SELERIC_MCP_TOKEN", "test-token")
    gw = MCPGateway("config/mcp_servers.yaml")

    captured = {}

    async def fake_call(arguments):
        captured.update(arguments)
        return {"rows": []}

    gw._servers["seleric.metrics_query"].call = fake_call

    await gw.call(
        agent_id="finance_agent",
        capability="seleric.metrics_query",
        arguments={"measures": ["net_profit"], "time_range": {"preset": "last_30d"}, "module": "commerce"},
    )

    # finance_agent is pinned to "finance" regardless of what the caller passed.
    assert captured["module"] == "finance"
