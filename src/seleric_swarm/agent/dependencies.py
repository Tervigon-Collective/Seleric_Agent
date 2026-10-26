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
from seleric_swarm.state.scratchpad import Scratchpad

if TYPE_CHECKING:
    from seleric_swarm.agent.limits import ExecutionBudgetTracker
    from seleric_swarm.services.metrics import MetricRegistry
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

    max_tool_calls: int = 300
    # One diagnosis fetches ~140-160 series; a turn may run two (before/after, or two entities).
    max_cube_queries: int = 600
    max_causal_queries: int = 80
    max_prediction_calls: int = 80
    max_python_calls: int = 10
    max_runtime_seconds: float = 900.0
    # Per-MCP-call timeout to prevent stuck queries from blocking the mission.
    mcp_call_timeout_s: float = 15.0
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
    # One instance per SelericDeps — never shared across missions. Value type
    # is `Any`, not `dict[str, Any]`: toolsets/_data.py caches raw MCP response
    # dicts under it, keyed by capability and arguments.
    query_cache: MissionQueryCache[str, Any] = field(default_factory=MissionQueryCache)
    # Per-run working memory — an auto-maintained ledger of established facts
    # (values a data tool returned) rendered back into the prompt
    # each turn so the model stops re-fetching the same thing. Not evidence
    # (rule 6). One instance per SelericDeps — never shared across missions.
    scratchpad: Scratchpad = field(default_factory=Scratchpad)
    # Whole live catalogue snapshot, warmed once per mission from
    # CatalogueBootstrap. The agent resolves metric ids against this full list
    # instead of a Qdrant top-k guess; Cube still validates the chosen id.
    catalogue: CatalogueSnapshot = field(default_factory=CatalogueSnapshot)
    # Spelling authority for metric ids. The catalogue snapshot above knows
    # which ids exist; only the registry knows that two of them ("metric.atc_rate"
    # and "session_atc_rate") are the same metric. Any code comparing an id it
    # declared against an id an EvidenceArtifact was stamped with must collapse
    # both through `canonical_id` first. None leaves
    # every spelling distinct, so production paths must pass the real registry.
    metrics: MetricRegistry | None = None
    # The date axis the user picked for this chat ("order_date" | "event_date"); None leaves it to Core's default.
    # Applied to MCP calls by the tool layer (mcp_toolset, toolsets/_data), never written into the prompt.
    date_axis: str | None = None
    # Real per-mission execution-limit enforcement (agent/limits.py). Not a
    # dataclass default_factory: ExecutionBudgetTracker needs this same
    # instance's own `limits`, so it's built in __post_init__ instead — every
    # existing call site (16+ tests, api/missions.py, agent/runner.py) that
    # constructs SelericDeps without a `budget=` kwarg still gets a tracker
    # that actually matches its own limits, not a mismatched shared default.
    _budget: ExecutionBudgetTracker | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self._budget is None:
            from seleric_swarm.agent.limits import ExecutionBudgetTracker

            object.__setattr__(self, "_budget", ExecutionBudgetTracker(limits=self.limits))

    @property
    def budget(self) -> ExecutionBudgetTracker:
        assert self._budget is not None  # set unconditionally in __post_init__
        return self._budget

    def canonical_metric_id(self, metric_id: str) -> str:
        """One spelling of *metric_id*, for code that compares ids with ``==``.

        toolsets/_data.py stamps an EvidenceArtifact with the id the caller
        actually fetched, verbatim and deliberately: two spellings of one metric
        must reach Cube unchanged and be independently attributed (bug #2's
        regression guard forbids collapsing them there). The other half of that
        rule is that anything *comparing* ids must collapse them first —
        otherwise one metric fetched under two spellings looks like two metrics
        and the comparison silently finds nothing. swarm_v2 did this through
        MetricRegistry; carry it wherever an id this code declared meets an id
        an artifact was stamped with. Unknown ids pass through unchanged.
        """
        if self.metrics is None:
            return metric_id
        return self.metrics.canonical_id(metric_id)
