from __future__ import annotations

import asyncio
import inspect
import os
from pathlib import Path
from typing import Any

import yaml

from seleric_swarm.paths import repo_root
from seleric_swarm.protocols.mcp.servers.seleric_remote import TOOLS as SELERIC_TOOLS
from seleric_swarm.protocols.mcp.servers.seleric_remote import build_seleric_servers

# Every domain agent with catalogue access gets the same read-only tool set
# (a catalogue-level constant, not a per-domain one); what differs per agent is
# which module (data-access scope) the gateway pins, read from the registry.
SELERIC_ACTION_TOOLS = {
    "actions_list_available",
    "actions_propose",
    "actions_commit",
    "actions_status",
}
SELERIC_CAPABILITIES = {
    f"seleric.{tool}" for tool in SELERIC_TOOLS if tool not in SELERIC_ACTION_TOOLS
}
SELERIC_ACTION_CAPABILITIES = {f"seleric.{tool}" for tool in SELERIC_ACTION_TOOLS}

# Tools whose server signature accepts ``module``. The gateway pins the agent's
# seleric_module onto these only — listing/resolve tools reject the extra argument.
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


def _build_allowlist() -> tuple[dict[str, set[str]], dict[str, str]]:
    """MCP allowlist + module pin for the single V3 agent.

    Sprint 5: the legacy observer/domain/coordinator agents this allowlist
    used to also cover (read from ``config/agent_registry.yaml`` via
    ``AgentRegistry``) were deleted along with swarm_v2 — ``v3_agent`` is the
    only caller left, so this is just its capability set now, not a
    registry-driven per-agent lookup.
    """

    # The single-agent runtime gets the action surface without granting
    # write capabilities to anything else. The remote MCP server still
    # enforces caller scopes, proposal eligibility, explicit confirmation
    # tokens, its write kill switch, and executor-level policy.
    allowlist = {"v3_agent": SELERIC_CAPABILITIES | SELERIC_ACTION_CAPABILITIES}
    return allowlist, {}


class MCPGateway:
    def __init__(self, config_path: str) -> None:
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
        self._allowlist, self._module = _build_allowlist()
        self.invocations: list[dict[str, Any]] = []

    @property
    def capabilities(self) -> set[str]:
        """MCP capabilities with a live server in this process (executable today)."""

        return set(self._servers)

    def module_for(self, agent_id: str) -> str | None:
        """Seleric MCP module pinned for this agent, if any."""
        return self._module.get(agent_id)

    def _authorize(self, agent_id: str, capability: str) -> None:
        # Writes stay v3_agent-only. Reads are open to any caller identity —
        # surviving service paths (catalogue warmup, ontology, business_state)
        # still stamp coordinator/observer/domain agent_ids for provenance,
        # and there is no multi-agent permission model left after Sprint 5.
        if capability in SELERIC_ACTION_CAPABILITIES:
            if agent_id != "v3_agent":
                raise PermissionError(f"agent {agent_id} is not allowed to call {capability}")
            return
        if capability in SELERIC_CAPABILITIES:
            return
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

    async def aclose(self) -> None:
        """Close underlying HTTP transports (shared across tool wrappers)."""
        seen: set[int] = set()
        for server in self._servers.values():
            transport = getattr(server, "_transport", None)
            if transport is None:
                closer = getattr(server, "aclose", None)
                if closer is not None:
                    maybe = closer()
                    if inspect.isawaitable(maybe):
                        await maybe
                continue
            tid = id(transport)
            if tid in seen:
                continue
            seen.add(tid)
            closer = getattr(transport, "aclose", None)
            if closer is not None:
                maybe = closer()
                if inspect.isawaitable(maybe):
                    await maybe

    def close(self) -> None:
        """Best-effort sync teardown for HTTP transports outside an async context."""
        try:
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(self.aclose())
            finally:
                loop.close()
        except Exception:  # noqa: S110 - teardown must never fail the suite (loop already closed, etc.)
            pass
