"""Streamable-HTTP adapter for the hosted Seleric catalogue/metrics MCP server.

One consolidated business-data gateway, not one MCP per platform (Meta/Shopify/
PostHog): the server exposes cross-platform semantic views itself and scopes
access per call via ``module`` (see modules_list). Domain agents get pinned to
a module by MCPGateway; this adapter just proxies tool calls.
"""

from __future__ import annotations

import asyncio
import itertools
import json
from typing import Any

import httpx
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential


class MCPUnavailableError(RuntimeError):
    """The Seleric MCP server did not respond after retrying transient failures.

    Distinct from a JSON-RPC error (the server responded but said no) so
    callers can tell "MCP is down" from "MCP said no" (docs/44 PRD-001).
    """


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, (httpx.ConnectError, httpx.TimeoutException, httpx.ReadError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return False


TOOLS = (
    "catalogue_search_metrics",
    "catalogue_list_metrics",
    "catalogue_bootstrap",
    "catalogue_resolve_term",
    "catalogue_resolve_dimension",
    "catalogue_get_metric",
    "catalogue_get_ontology",
    "catalogue_related_metrics",
    "catalogue_list_dimensions",
    "modules_list",
    "metrics_query",
    "metrics_drilldown",
    "insights_explain",
)


class SelericMCPTransport:
    """Minimal MCP JSON-RPC client over the streamable-http transport.

    The server requires a session handshake before any tools/call: POST
    initialize, keep the Mcp-Session-Id it returns, then POST
    notifications/initialized. Every response (including initialize) comes
    back as a single SSE `data:` event, not plain JSON.
    """

    def __init__(self, url: str, token: str, timeout_s: float = 30.0) -> None:
        self._url = url
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        self._client = httpx.AsyncClient(timeout=timeout_s)
        self._ids = itertools.count(1)
        self._session_id: str | None = None
        self._init_lock = asyncio.Lock()

    async def _ensure_session(self) -> None:
        if self._session_id is not None:
            return
        async with self._init_lock:
            if self._session_id is not None:
                return
            body = {
                "jsonrpc": "2.0",
                "id": next(self._ids),
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "seleric-swarm", "version": "0.1.0"},
                },
            }
            resp = await self._post_with_retry(body)
            session_id = resp.headers.get("mcp-session-id")
            if not session_id:
                raise RuntimeError("seleric mcp did not return an Mcp-Session-Id on initialize")
            self._session_id = session_id
            self._headers["Mcp-Session-Id"] = session_id
            notif = {"jsonrpc": "2.0", "method": "notifications/initialized"}
            await self._client.post(self._url, json=notif, headers=self._headers)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        await self._ensure_session()
        body = {
            "jsonrpc": "2.0",
            "id": next(self._ids),
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        resp = await self._post_with_retry(body)
        payload = _parse_jsonrpc_response(resp)
        if "error" in payload:
            raise RuntimeError(f"seleric mcp error calling {name}: {payload['error']}")
        result = payload.get("result") or {}
        for block in result.get("content") or []:
            if block.get("type") != "text":
                continue
            parsed = _loads_json(block.get("text"), context=f"tool {name}")
            if parsed is None:
                continue
            return parsed if isinstance(parsed, dict) else {"value": parsed}
        return result if isinstance(result, dict) else {}

    async def _post_with_retry(self, body: dict[str, Any]) -> httpx.Response:
        """3 attempts, short exponential backoff, transient failures only.

        4xx responses are real errors (retrying won't help) — only connection
        errors, timeouts, and 5xx get retried. Raises ``MCPUnavailableError``
        once retries are exhausted instead of leaking the raw httpx exception,
        so callers (``HybridMcpDataProvider.fetch``'s existing broad
        ``except Exception``) keep degrading gracefully either way, and a
        future caller can distinguish "MCP is down" from "MCP said no".
        """
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(3),
                wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
                retry=retry_if_exception(_is_retryable),
                reraise=True,
            ):
                with attempt:
                    resp = await self._client.post(self._url, json=body, headers=self._headers)
                    resp.raise_for_status()
                    return resp
        except (httpx.ConnectError, httpx.TimeoutException, httpx.ReadError, httpx.HTTPStatusError) as exc:
            if _is_retryable(exc):
                raise MCPUnavailableError(f"seleric mcp unavailable after retries: {exc}") from exc
            raise  # a real 4xx error — not a "down" signal, let it surface as-is
        raise AssertionError("unreachable")  # AsyncRetrying always returns or raises

    async def aclose(self) -> None:
        await self._client.aclose()


def _loads_json(raw: Any, *, context: str) -> Any | None:
    """Parse a JSON payload. Empty bodies are skipped; garbage raises RuntimeError."""
    if raw is None:
        return None
    text = raw if isinstance(raw, str) else str(raw)
    if not text.strip():
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"seleric mcp returned invalid JSON ({context}): {exc}") from exc


def _parse_jsonrpc_response(resp: httpx.Response) -> dict[str, Any]:
    if "text/event-stream" in resp.headers.get("content-type", ""):
        for line in resp.text.splitlines():
            if not line.startswith("data:"):
                continue
            parsed = _loads_json(line[len("data:") :].strip(), context="sse data")
            if parsed is None:
                continue
            if not isinstance(parsed, dict):
                raise RuntimeError("seleric mcp sse data was not a JSON object")  # noqa: TRY004
            return parsed
        raise RuntimeError("no data event in streamable-http response")
    parsed = _loads_json(resp.text, context="http body")
    if not isinstance(parsed, dict):
        raise RuntimeError("seleric mcp returned an empty or non-object response")  # noqa: TRY004
    return parsed


class RemoteToolServer:
    """Adapts one MCP tool on a shared transport to the gateway's capability/call contract."""

    def __init__(self, transport: SelericMCPTransport, tool_name: str, capability_prefix: str) -> None:
        self._transport = transport
        self._tool_name = tool_name
        self.capability = f"{capability_prefix}.{tool_name}"

    async def call(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return await self._transport.call_tool(self._tool_name, arguments)


def build_seleric_servers(*, url: str, token: str, capability_prefix: str = "seleric") -> list[RemoteToolServer]:
    transport = SelericMCPTransport(url=url, token=token)
    return [RemoteToolServer(transport, tool, capability_prefix) for tool in TOOLS]
