"""Live OpenMetadata ontology snapshot, fetched from seleric-mcp.

The YAML graph lives in Base_Agent (`catalogue/openmetadata/`). This service
caches per-module and per-metric slices so domain/intelligence agents can
attach data-product context without copying that YAML into the swarm.
Falls back to empty dicts when the live catalogue is not configured.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from seleric_swarm.protocols.mcp.gateway import MCPGateway

_GET_ONTOLOGY = "seleric.catalogue_get_ontology"
_GET_METRIC = "seleric.catalogue_get_metric"
_RELATED = "seleric.catalogue_related_metrics"


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
        self._by_agent: dict[str, dict[str, Any]] = {}
        self._by_module: dict[str, dict[str, Any]] = {}
        self._by_metric: dict[str, dict[str, Any]] = {}
        self._related: dict[str, dict[str, Any]] = {}

    def _live(self, capability: str) -> bool:
        return capability in self._mcp.capabilities

    async def _call(self, *, agent_id: str, capability: str, arguments: dict[str, Any]) -> dict[str, Any]:
        # Ontology is query context, not numbers. A down/old MCP must not fail the mission.
        try:
            result = await self._mcp.call(agent_id=agent_id, capability=capability, arguments=arguments)
        except Exception:
            return {}
        return result if isinstance(result, dict) else {}

    async def for_agent(self, agent_id: str) -> dict[str, Any]:
        cached = self._by_agent.get(agent_id)
        if cached is not None:
            return cached
        if not self._live(_GET_ONTOLOGY):
            return {}
        result = await self._call(agent_id=agent_id, capability=_GET_ONTOLOGY, arguments={})
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
        if not self._live(_GET_ONTOLOGY):
            return {}
        result = await self._call(
            agent_id=agent_id, capability=_GET_ONTOLOGY, arguments={"module": module}
        )
        if not result or result.get("error"):
            return {}
        self._by_module[module] = result
        return result

    async def metric_context(self, metric_id: str, *, agent_id: str = "observer_agent") -> dict[str, Any]:
        key = _bare_metric_id(metric_id)
        if key in self._by_metric:
            return self._by_metric[key]
        if not self._live(_GET_METRIC):
            return {}
        result = await self._call(
            agent_id=agent_id, capability=_GET_METRIC, arguments={"metric_id": key}
        )
        if not result or result.get("error"):
            return {}
        om = result.get("openmetadata") or {}
        self._by_metric[key] = om
        return om

    async def related_metrics(self, metric_id: str, *, agent_id: str = "observer_agent") -> dict[str, Any]:
        key = _bare_metric_id(metric_id)
        if key in self._related:
            return self._related[key]
        if not self._live(_RELATED):
            return {}
        result = await self._call(
            agent_id=agent_id, capability=_RELATED, arguments={"metric_id": key}
        )
        if not result or result.get("error"):
            return {}
        self._related[key] = result
        return result


class NullOntology:
    """Offline stand-in used when seleric MCP is not configured."""

    async def for_agent(self, agent_id: str) -> dict[str, Any]:
        return {}

    async def for_module(self, module: str, *, agent_id: str = "observer_agent") -> dict[str, Any]:
        return {}

    async def metric_context(self, metric_id: str, *, agent_id: str = "observer_agent") -> dict[str, Any]:
        return {}

    async def related_metrics(self, metric_id: str, *, agent_id: str = "observer_agent") -> dict[str, Any]:
        return {}
