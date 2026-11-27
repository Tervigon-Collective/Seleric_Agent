"""Seleric MCP client for the live monitor: ad-platform reads and the gated writes behind one-click actions.

The agent's own MCP gateway deliberately does not expose ad-platform tools, so the monitor holds its own transport to
the same server (same URL and token). Reads go through ``call``; lists through ``paginate``.
"""

from __future__ import annotations

import os
from typing import Any

from seleric_swarm.protocols.mcp.servers.seleric_remote import SelericMCPTransport


class McpToolError(RuntimeError):
    def __init__(self, tool: str, envelope: dict[str, Any]) -> None:
        err = envelope.get("error") or {}
        self.tool = tool
        self.code = str(err.get("code") or envelope.get("status") or "ERROR")
        self.envelope = envelope
        super().__init__(f"{tool}: {self.code}: {err.get('message') or envelope.get('message') or ''}".strip())


class McpClient:
    def __init__(self, url: str | None = None, token: str | None = None, timeout_s: float = 60.0) -> None:
        url = url or os.getenv("SELERIC_MCP_URL", "")
        token = token or os.getenv("SELERIC_MCP_TOKEN", "")
        if not url or not token:
            raise RuntimeError("SELERIC_MCP_URL and SELERIC_MCP_TOKEN are required for the live monitor")
        self._t = SelericMCPTransport(url=url, token=token, timeout_s=timeout_s)

    async def raw(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """The envelope as returned, including APPROVAL_REQUIRED and errors."""
        return await self._t.call_tool(tool, arguments)

    async def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        out = await self.raw(tool, arguments)
        if out.get("success") is False or out.get("error"):
            raise McpToolError(tool, out)
        return out

    async def paginate(self, tool: str, arguments: dict[str, Any], *, page_size: int = 200, max_pages: int = 20) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        after: str | None = None
        for _ in range(max_pages):
            args = {**arguments, "limit": page_size, **({"after": after} if after else {})}
            out = await self.call(tool, args)
            rows.extend(out.get("data") or [])
            paging = out.get("paging") or {}
            after = paging.get("after")
            if not paging.get("has_next") or not after:
                break
        return rows

    async def aclose(self) -> None:
        await self._t.aclose()
