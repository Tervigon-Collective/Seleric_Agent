"""``SelericDeps`` — frozen, ``CONTRACTS.md`` §1.

Plain dataclass (PydanticAI convention — deps are not validated Pydantic
models), one instance per mission run, immutable for the run's lifetime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

from seleric_swarm.agent.scope import RequiredScope
from seleric_swarm.conversations.contracts import ContextBundle, Principal
from seleric_swarm.services.catalogue_bootstrap import CatalogueSnapshot
from seleric_swarm.state.cache import MissionQueryCache
from seleric_swarm.state.scratchpad import Scratchpad

if TYPE_CHECKING:
    from seleric_swarm.agent.limits import ExecutionBudgetTracker
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

    max_tool_calls: int = 160
    max_cube_queries: int = 100
    max_causal_queries: int = 40
    max_prediction_calls: int = 40
    # Confirmed = 1 with A1 acceptance (2026-09-18). Causal widening uses
    # estimate_effect(search_breadth=...), not this counter.
    max_validation_revisions: int = 1
    max_runtime_seconds: float = 600.0
    # PydanticAI per-run retries — how many times the model may recover from a
    # tool ModelRetry (e.g. an unknown metric id) or an output-validation
    # error within one mission before the run fails.
    agent_retries: int = 2


@dataclass(frozen=True)
class JevConfig:
    """Jev (openjev) endpoint for in-tool typed decisions (action risk #4,
    knowledge relevance #5). Empty base_url/api_key ⇒ every Jev call fails open
    to a no-op, so the tools behave exactly as before when Jev is unconfigured."""

    base_url: str = ""
    api_key: str = ""
    timeout: float = 1.0


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
    # is `Any`, not `dict[str, Any]`: toolsets/semantic.py caches both raw MCP
    # response dicts (query_metric_series/drilldown) and whole `ToolResult`
    # objects (query_metrics' repeat-call dedup) under the same cache, keyed
    # by distinct prefixes -- narrowing this to one shape would just be wrong
    # for the other caller, not more correct.
    query_cache: MissionQueryCache[str, Any] = field(default_factory=MissionQueryCache)
    # Per-mission mutable tool-call tallies (e.g. how many times search_semantics
    # ran) so a tool can break a paraphrase-search loop the exact-arg query_cache
    # can't see. One dict per SelericDeps — never shared across missions.
    call_counts: dict[str, int] = field(default_factory=dict)
    # Per-run working memory — an auto-maintained ledger of established facts
    # (values a successful query_metrics returned) rendered back into the prompt
    # each turn so the model stops re-fetching the same thing. Not evidence
    # (rule 6). One instance per SelericDeps — never shared across missions.
    scratchpad: Scratchpad = field(default_factory=Scratchpad)
    # Whole live catalogue snapshot, warmed once per mission from
    # CatalogueBootstrap. The agent resolves metric ids against this full list
    # instead of a Qdrant top-k guess; Cube still validates the chosen id.
    catalogue: CatalogueSnapshot = field(default_factory=CatalogueSnapshot)
    jev: JevConfig = field(default_factory=JevConfig)
    # Hard constraints the query demanded (requested breakdowns resolved to
    # catalogue dimension ids), captured once in the runner. Read by
    # validation/signals.py::check_scope_coverage to prove the executed evidence
    # covers what was asked. Default-empty ⇒ the check is NOT_APPLICABLE.
    required_scope: RequiredScope = field(default_factory=RequiredScope)
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
