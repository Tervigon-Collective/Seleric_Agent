"""Live OpenMetadata ontology snapshot, fetched from seleric-mcp.

The YAML graph lives in Base_Agent (`catalogue/openmetadata/`). This service
caches per-module and per-metric slices so domain/intelligence agents can
attach data-product context without copying that YAML into the swarm.
Falls back to empty dicts when the live catalogue is not configured.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

from seleric_swarm.protocols.mcp.gateway import MCPGateway
from seleric_swarm.semantic import McpSemanticPort

_log = logging.getLogger("seleric.services.ontology")


def _bare_metric_id(metric_id: str) -> str:
    raw = (metric_id or "").strip()
    return raw.removeprefix("metric.") if raw else raw


@runtime_checkable
class OntologyPort(Protocol):
    async def for_agent(self, agent_id: str) -> dict[str, Any]: ...

    async def for_module(self, module: str, *, agent_id: str = "observer_agent") -> dict[str, Any]: ...

    async def metric_context(self, metric_id: str, *, agent_id: str = "observer_agent") -> dict[str, Any]: ...

    async def related_metrics(self, metric_id: str, *, agent_id: str = "observer_agent") -> dict[str, Any]: ...


class OntologyService:
    """Process-level cache of catalogue://ontology, keyed by module / metric."""

    def __init__(self, mcp: MCPGateway) -> None:
        self._mcp = mcp
        self._port = McpSemanticPort(mcp)
        self._by_agent: dict[str, dict[str, Any]] = {}
        self._by_module: dict[str, dict[str, Any]] = {}
        self._by_metric: dict[str, dict[str, Any]] = {}
        self._related: dict[str, dict[str, Any]] = {}

    async def _read(self, label: str, call: Any) -> dict[str, Any]:
        # Ontology is query context, not numbers. A down/old MCP must not fail the mission.
        try:
            result = await call
        except Exception:
            _log.warning("ontology_call_failed", exc_info=True, extra={"operation": label})
            return {}
        return result if isinstance(result, dict) else {}

    async def for_agent(self, agent_id: str) -> dict[str, Any]:
        cached = self._by_agent.get(agent_id)
        if cached is not None:
            return cached
        if not self._port.supports("ontology"):
            return {}
        result = await self._read("ontology", self._port.ontology(agent_id=agent_id))
        if not result or result.get("error"):
            return {}
        self._by_agent[agent_id] = result
        module = result.get("module") or self._mcp.module_for(agent_id)
        if module:
            self._by_module[module] = result
        return result

    async def for_module(self, module: str, *, agent_id: str = "observer_agent") -> dict[str, Any]:
        if module in self._by_module:
            return self._by_module[module]
        if not self._port.supports("ontology"):
            return {}
        result = await self._read("ontology", self._port.ontology(agent_id=agent_id, module=module))
        if not result or result.get("error"):
            return {}
        self._by_module[module] = result
        return result

    async def metric_context(self, metric_id: str, *, agent_id: str = "observer_agent") -> dict[str, Any]:
        key = _bare_metric_id(metric_id)
        if key in self._by_metric:
            return self._by_metric[key]
        if not self._port.supports("metric"):
            return {}
        result = await self._read("metric", self._port.fetch_metric(key, agent_id=agent_id))
        if not result or result.get("error"):
            return {}
        om = result.get("openmetadata") or {}
        self._by_metric[key] = om
        return om

    async def related_metrics(self, metric_id: str, *, agent_id: str = "observer_agent") -> dict[str, Any]:
        key = _bare_metric_id(metric_id)
        if key in self._related:
            return self._related[key]
        if not self._port.supports("related"):
            return {}
        result = await self._read("related", self._port.related_metrics(key, agent_id=agent_id))
        if not result or result.get("error"):
            return {}
        self._related[key] = result
        return result
