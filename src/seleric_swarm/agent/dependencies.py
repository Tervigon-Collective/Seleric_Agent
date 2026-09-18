"""``SelericDeps`` — frozen, ``CONTRACTS.md`` §1.

Plain dataclass (PydanticAI convention — deps are not validated Pydantic
models), one instance per mission run, immutable for the run's lifetime.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from seleric_swarm.conversations.contracts import ContextBundle, Principal

if TYPE_CHECKING:
    from seleric_swarm.state.artifacts import ArtifactStore


class SelericMcpClient(Protocol):
    """Placeholder for Profile B's thin wrapper over the ``mcp__seleric-mcp__*``
    tools (``catalogue_*`` / ``metrics_query`` / ``metrics_drilldown`` /
    ``actions_*``). Not implemented yet — ``toolsets/semantic.py`` and
    ``toolsets/actions.py`` (Profile B, Sprint 1/3) own the real shape;
    ``SelericDeps.mcp_client`` is typed against this Protocol so Profile A's
    skeleton can be built and tested (with a fake) before Profile B lands.
    """


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
    max_validation_revisions: int = 1
    max_runtime_seconds: float = 120.0


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
