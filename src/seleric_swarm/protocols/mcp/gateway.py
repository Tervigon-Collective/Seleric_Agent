from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from seleric_swarm.paths import repo_root
from seleric_swarm.protocols.mcp.servers.fixture_commerce import FixtureCommerceServer
from seleric_swarm.protocols.mcp.servers.fixture_performance import FixturePerformanceServer

AGENT_ALLOWLIST: dict[str, set[str]] = {
    "commerce_agent": {"commerce.daily_sales"},
    "performance_agent": {"performance.daily_cac"},
    "observer_agent": {"commerce.daily_sales", "performance.daily_cac"},
    "coordinator_agent": set(),
}


class MCPGateway:
    def __init__(self, config_path: str, fixture_path: str | None = None) -> None:
        root = repo_root()
        path = Path(config_path)
        if not path.is_absolute():
            path = root / path
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        servers = data.get("servers") or {}
        commerce_path = fixture_path
        if commerce_path is None:
            commerce = servers.get("commerce_fixture") or {}
            commerce_path = commerce.get("fixture_path", "data/fixtures/commerce/daily_sales.json")
        performance = servers.get("performance_fixture") or {}
        performance_path = performance.get("fixture_path", "data/fixtures/performance/daily_cac.json")
        self._commerce = FixtureCommerceServer(commerce_path)
        self._performance = FixturePerformanceServer(performance_path)
        self._servers = {
            self._commerce.capability: self._commerce,
            self._performance.capability: self._performance,
        }
        self.invocations: list[dict[str, Any]] = []

    def _authorize(self, agent_id: str, capability: str) -> None:
        allowed = AGENT_ALLOWLIST.get(agent_id, set())
        if capability not in allowed:
            raise PermissionError(f"agent {agent_id} is not allowed to call {capability}")

    async def call(self, *, agent_id: str, capability: str, arguments: dict[str, Any]) -> Any:
        self._authorize(agent_id, capability)
        server = self._servers.get(capability)
        if server is None:
            raise NotImplementedError(f"MCP capability not available in V1: {capability}")
        result = server.call(arguments)
        self.invocations.append({"agent_id": agent_id, "capability": capability, "arguments": arguments})
        return result
