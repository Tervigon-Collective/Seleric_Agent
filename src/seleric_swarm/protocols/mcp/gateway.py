from __future__ import annotations

import inspect
import os
from pathlib import Path
from typing import Any

import yaml

from seleric_swarm.paths import repo_root
from seleric_swarm.protocols.mcp.servers.seleric_remote import TOOLS as SELERIC_TOOLS
from seleric_swarm.protocols.mcp.servers.seleric_remote import build_seleric_servers
from seleric_swarm.registry.agent_registry import AgentRegistry

# Every domain agent with catalogue access gets the same read-only tool set
# (a catalogue-level constant, not a per-domain one); what differs per agent is
# which module (data-access scope) the gateway pins, read from the registry.
SELERIC_CAPABILITIES = {f"seleric.{tool}" for tool in SELERIC_TOOLS}

# Tools whose server signature accepts ``module``. The gateway pins the agent's
# seleric_module onto these only — other catalogue tools (list_dimensions,
# resolve_term, modules_list) would reject the extra argument.
SELERIC_MODULE_ARG_TOOLS = {
    f"seleric.{tool}"
    for tool in (
        "catalogue_search_metrics",
        "catalogue_get_metric",
        "catalogue_get_ontology",
        "catalogue_related_metrics",
        "metrics_query",
        "metrics_drilldown",
    )
}


def _build_allowlist(agents: AgentRegistry) -> tuple[dict[str, set[str]], dict[str, str]]:
    """Per-agent MCP allowlist + module pin, entirely from config/agent_registry.yaml."""

    allowlist: dict[str, set[str]] = {}
    module_map: dict[str, str] = {}
    observer_caps: set[str] = set()
    for agent in agents.domain_agents(enabled_only=True):
        aid = agent["id"]
        caps = set(agent.get("mcp_capabilities") or [])
        module = agent.get("seleric_module")
        if module:
            caps |= SELERIC_CAPABILITIES
            module_map[aid] = module
        allowlist[aid] = caps
        observer_caps |= caps
    allowlist["observer_agent"] = observer_caps
    allowlist["coordinator_agent"] = {
        "seleric.catalogue_search_metrics",
        "seleric.catalogue_get_metric",
        "seleric.catalogue_list_dimensions",
        "seleric.catalogue_resolve_term",
    }
    return allowlist, module_map


class MCPGateway:
    def __init__(self, config_path: str, fixture_path: str | None = None, agents: AgentRegistry | None = None) -> None:
        del fixture_path  # leftover kw from older fixture-backed gateway
        root = repo_root()
        path = Path(config_path)
        if not path.is_absolute():
            path = root / path
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        servers = data.get("servers") or {}
        self._servers: dict[str, Any] = {}
        for name, cfg in servers.items():
            if cfg.get("transport") != "streamable_http":
                continue
            url = os.environ.get(cfg.get("url_env", ""), "")
            token = os.environ.get(cfg.get("auth_token_env", ""), "")
            if not url or not token:
                continue
            if name == "seleric":
                for remote in build_seleric_servers(
                    url=url, token=token, capability_prefix=cfg.get("capability_prefix", "seleric")
                ):
                    self._servers[remote.capability] = remote
        if agents is None:
            agents = AgentRegistry(str(root / "config" / "agent_registry.yaml"))
        self._allowlist, self._module = _build_allowlist(agents)
        self.invocations: list[dict[str, Any]] = []

    @property
    def capabilities(self) -> set[str]:
        """MCP capabilities with a live server in this process (executable today)."""

        return set(self._servers)

    def module_for(self, agent_id: str) -> str | None:
        """Seleric MCP module pinned for this agent, if any."""
        return self._module.get(agent_id)

    def _authorize(self, agent_id: str, capability: str) -> None:
        allowed = self._allowlist.get(agent_id, set())
        if capability not in allowed:
            raise PermissionError(f"agent {agent_id} is not allowed to call {capability}")

    async def call(self, *, agent_id: str, capability: str, arguments: dict[str, Any]) -> Any:
        self._authorize(agent_id, capability)
        if capability in SELERIC_MODULE_ARG_TOOLS:
            # Explicit module in arguments wins (including None = unscoped).
            if "module" in arguments:
                module = arguments.get("module")
            else:
                module = self._module.get(agent_id)
            if module:
                arguments = {**arguments, "module": module}
            else:
                arguments = {k: v for k, v in arguments.items() if k != "module"}
        server = self._servers.get(capability)
        if server is None:
            raise NotImplementedError(f"MCP capability not available: {capability}")
        result = server.call(arguments)
        if inspect.isawaitable(result):
            result = await result
        self.invocations.append({"agent_id": agent_id, "capability": capability, "arguments": arguments})
        return result
