import pytest
import httpx

from seleric_swarm.protocols.mcp.gateway import MCPGateway
from seleric_swarm.protocols.mcp.servers.seleric_remote import _parse_jsonrpc_response


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
        arguments={"measures": ["net_profit"], "time_range": {"preset": "last_30d"}},
    )
    assert captured["module"] == "finance"

    captured.clear()
    await gw.call(
        agent_id="finance_agent",
        capability="seleric.metrics_query",
        arguments={
            "measures": ["cac"],
            "time_range": {"preset": "last_30d"},
            "module": None,  # metric-level unscoped override
        },
    )
    assert "module" not in captured


def test_empty_sse_data_is_skipped():
    resp = httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        text='data:\ndata: {"jsonrpc": "2.0", "result": {"ok": true}}\n',
    )
    assert _parse_jsonrpc_response(resp)["result"] == {"ok": True}


def test_empty_sse_only_raises_runtime_error_not_json_decode():
    resp = httpx.Response(200, headers={"content-type": "text/event-stream"}, text="data:\n\n")
    with pytest.raises(RuntimeError, match="no data event"):
        _parse_jsonrpc_response(resp)


def test_invalid_sse_json_raises_runtime_error():
    resp = httpx.Response(
        200, headers={"content-type": "text/event-stream"}, text="data: not-json\n"
    )
    with pytest.raises(RuntimeError, match="invalid JSON"):
        _parse_jsonrpc_response(resp)
