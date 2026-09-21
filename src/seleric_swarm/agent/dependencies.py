"""``SelericDeps`` — frozen, ``CONTRACTS.md`` §1.

Plain dataclass (PydanticAI convention — deps are not validated Pydantic
models), one instance per mission run, immutable for the run's lifetime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

from seleric_swarm.conversations.contracts import ContextBundle, Principal
from seleric_swarm.services.catalogue_bootstrap import CatalogueSnapshot
from seleric_swarm.state.cache import MissionQueryCache

if TYPE_CHECKING:
    from seleric_swarm.state.artifacts import ArtifactStore


class SelericMcpClient(Protocol):
    """Thin MCP surface used by semantic/actions toolsets.

    Matches ``MCPGateway.call`` so Profile B/C tools can type against deps
    without importing the gateway.
    """

    async def call(
        self, *, agent_id: str, capability: str, arguments: dict[str, Any]
    ) -> Any: ...


class NullMcpClient:
    """Stand-in for paths that must not talk to MCP (analytics, stub missions)."""

    async def call(self, *, agent_id: str, capability: str, arguments: dict[str, Any]) -> Any:
        raise NotImplementedError("this path does not call MCP")


@dataclass(frozen=True)
class ExecutionLimits:
    """Supersedes ``coordinator/governance/budget.py::MissionLimits``.

    Fields carry over by name where the concept survives; the rest are new
    per spec §39 / non-negotiable rule 11. ``max_llm_calls``,
    ``max_agent_calls``, ``max_leadership_transfers``, ``max_iterations``
    from the old ``MissionLimits`` do NOT carry over — those counted
    LangGraph/swarm multi-agent handoffs, which don't exist in a
    single-agent loop.
    """

    max_tool_calls: int = 8
    max_cube_queries: int = 6
    max_causal_queries: int = 3
    max_prediction_calls: int = 3
    # Confirmed = 1 with A1 acceptance (2026-09-18). Causal widening uses
    # estimate_effect(search_breadth=...), not this counter.
    max_validation_revisions: int = 1
    max_runtime_seconds: float = 120.0
    # PydanticAI per-run retries — how many times the model may recover from a
    # tool ModelRetry (e.g. an unknown metric id) or an output-validation
    # error within one mission before the run fails.
    agent_retries: int = 2


@dataclass(frozen=True)
class SelericDeps:
    mission_id: str
    as_of: datetime
    principal: Principal
    thread_id: str
    run_id: str
    trace_id: str
    context: ContextBundle
    mcp_client: SelericMcpClient
    artifact_store: ArtifactStore
    limits: ExecutionLimits
    # Dedupes identical seleric-mcp fetches within this mission run (state/cache.py).
    # One instance per SelericDeps — never shared across missions.
    query_cache: MissionQueryCache[str, dict[str, Any]] = field(default_factory=MissionQueryCache)
    # Whole live catalogue snapshot, warmed once per mission from
    # CatalogueBootstrap. The agent resolves metric ids against this full list
    # instead of a Qdrant top-k guess; Cube still validates the chosen id.
    catalogue: CatalogueSnapshot = field(default_factory=CatalogueSnapshot)
