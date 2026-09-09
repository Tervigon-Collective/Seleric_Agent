import httpx
import pytest

from seleric_swarm.protocols.mcp.gateway import MCPGateway
from seleric_swarm.protocols.mcp.servers.seleric_remote import (
    MCPUnavailableError,
    SelericMCPTransport,
    _parse_jsonrpc_response,
)


_REQ = httpx.Request("POST", "https://example.invalid/mcp")


def _resp(status: int, **kwargs) -> httpx.Response:
    r = httpx.Response(status, request=_REQ, **kwargs)
    return r


def _init_response() -> httpx.Response:
    return _resp(200, headers={"mcp-session-id": "sess-1"}, json={"jsonrpc": "2.0", "result": {}})


def _tool_response(value: dict) -> httpx.Response:
    return _resp(200, json={"jsonrpc": "2.0", "result": {"value": value}})


@pytest.mark.asyncio
async def test_transport_retries_transient_failure_then_succeeds(monkeypatch):
    # docs/44 PRD-001: a 502 (or connection error) should be retried, not
    # propagate on the first failure.
    transport = SelericMCPTransport(url="https://example.invalid/mcp", token="t")
    calls = {"n": 0}

    async def fake_post(url, *, json, headers):
        calls["n"] += 1
        if calls["n"] == 1:
            return _resp(502, text="bad gateway")
        if json.get("method") == "initialize":
            return _init_response()
        return _tool_response({"ok": True})

    monkeypatch.setattr(transport._client, "post", fake_post)
    result = await transport.call_tool("metrics_query", {})
    assert result == {"value": {"ok": True}}
    assert calls["n"] >= 2


@pytest.mark.asyncio
async def test_transport_raises_mcp_unavailable_after_exhausting_retries(monkeypatch):
    transport = SelericMCPTransport(url="https://example.invalid/mcp", token="t")

    async def always_fails(url, *, json, headers):
        return _resp(503, text="service unavailable")

    monkeypatch.setattr(transport._client, "post", always_fails)
    with pytest.raises(MCPUnavailableError):
        await transport.call_tool("metrics_query", {})


@pytest.mark.asyncio
async def test_transport_does_not_retry_4xx(monkeypatch):
    transport = SelericMCPTransport(url="https://example.invalid/mcp", token="t")
    calls = {"n": 0}

    async def fake_post(url, *, json, headers):
        calls["n"] += 1
        if json.get("method") == "initialize":
            return _init_response()
        return _resp(400, text="bad request")

    monkeypatch.setattr(transport._client, "post", fake_post)
    with pytest.raises(httpx.HTTPStatusError):
        await transport.call_tool("metrics_query", {})
    # initialize (1) + notifications/initialized (1) + exactly one
    # tool-call attempt (not retried, since 400 isn't transient)
    assert calls["n"] == 3


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
