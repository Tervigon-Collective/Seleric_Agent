"""MCP gateway abstraction.

Bind this interface to the official MCP client SDK and enforce capability allowlists here.
"""
from typing import Any


class MCPGateway:
    async def call(self, *, agent_id: str, capability: str, arguments: dict[str, Any]) -> Any:
        # TODO: authorize agent + mission, resolve MCP server/tool, invoke, normalize and audit.
        raise NotImplementedError
