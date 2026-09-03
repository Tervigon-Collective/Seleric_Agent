from seleric_swarm.protocols.a2a.envelope import SwarmEnvelope
from seleric_swarm.protocols.mcp.gateway import MCPGateway
from seleric_swarm.protocols.mcp.servers.fixture_commerce import FixtureCommerceServer


def test_envelope_contract():
    env = SwarmEnvelope(
        mission_id="M-1",
        task_id="T-1",
        message_id="msg-1",
        from_agent="coordinator_agent",
        intent="task_request",
        payload={"query_class": "lookup"},
        idempotency_key="k1",
    )
    assert env.mission_id == "M-1"
    dumped = env.model_dump()
    SwarmEnvelope.model_validate(dumped)


def test_fixture_commerce_contract_found():
    server = FixtureCommerceServer("data/fixtures/commerce/daily_sales.json")
    result = server.call({"date": "2026-08-01", "metrics": ["metric.net_sales"]})
    assert result["found"] is True
    assert result["metrics"]["metric.net_sales"] == 125000.5
    assert result["row_count"] == 1
    assert result["query_hash"]


def test_fixture_commerce_missing_day_is_not_zero():
    server = FixtureCommerceServer("data/fixtures/commerce/daily_sales.json")
    result = server.call({"date": "2026-07-15", "metrics": ["metric.net_sales"]})
    assert result["found"] is False
    assert result["metrics"] == {}
    assert result["row_count"] == 0


import pytest


@pytest.mark.asyncio
async def test_gateway_allowlist():
    gw = MCPGateway("config/mcp_servers.yaml")
    with pytest.raises(PermissionError):
        await gw.call(agent_id="coordinator_agent", capability="commerce.daily_sales", arguments={"date": "2026-08-01"})
    row = await gw.call(
        agent_id="observer_agent",
        capability="commerce.daily_sales",
        arguments={"date": "2026-08-01", "metrics": ["metric.net_sales"]},
    )
    assert row["found"] is True
