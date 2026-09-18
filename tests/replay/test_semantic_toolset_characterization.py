"""Sprint 1/2 (Profile B) characterization: does SemanticToolset.query_metrics()
match the legacy fetch path, across every metric already covered by
tests/replay/test_data_access_characterization.py's ``_CASES``?

Per docs/refactor/SPRINT_PLAN.md Sprint 1: "Re-run
tests/replay/test_data_access_characterization.py against the new toolset
... to establish it matches at least one of the three legacy paths." Sprint 2
broadens this to all three ``_CASES`` metrics (not just one) before any call
site gets routed through the new toolset — one matching metric is not enough
evidence to reroute live production traffic.

Requires live Seleric MCP credentials (skips otherwise, same as every other
``runtime``-fixture test in this repo).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from seleric_swarm.agent.dependencies import ExecutionLimits, SelericDeps
from seleric_swarm.conversations.contracts import ContextBundle, Principal
from seleric_swarm.state.artifacts import InMemoryArtifactStore
from seleric_swarm.swarm.providers.mcp_data import McpDataProvider, McpFetchStats
from seleric_swarm.toolsets import semantic

_DAY = "2026-08-01"

# (agent_id, legacy metric_id, catalogue metric_id) — catalogue ids are each
# metric's config/metric_registry.yaml `catalogue_metric`. Mirrors
# tests/replay/test_data_access_characterization.py::_CASES so both suites
# characterize the same live data.
_CASES = [
    ("performance_agent", "metric.cac", "cac"),  # seleric_module: null (unscoped) in the registry
    ("finance_agent", "metric.net_profit", "net_profit_all_channels"),  # unscoped (canonical_pnl)
    ("product_agent", "metric.units_sold", "units_sold"),  # commerce module (module-scoped in the registry)
]


def _domain_for(agent_id: str) -> str:
    return agent_id.removesuffix("_agent")


class _RunContext:
    def __init__(self, deps: SelericDeps) -> None:
        self.deps = deps


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_id,metric_id_legacy,metric_id_catalogue", _CASES)
async def test_semantic_toolset_query_metrics_matches_hybrid_provider_fetch(
    runtime, agent_id, metric_id_legacy, metric_id_catalogue
):
    provider = McpDataProvider(
        _domain_for(agent_id),
        mcp=runtime.mcp,
        stats=McpFetchStats(),
        metrics=runtime.metrics,
        agent_id=agent_id,
    )
    legacy = await provider.fetch(
        metric_ids=[metric_id_legacy],
        time_range={"start": _DAY, "end": _DAY},
    )
    legacy_value = legacy.readings[0].value if legacy.readings else None
    assert legacy_value is not None, (
        f"McpDataProvider.fetch() returned no reading for {metric_id_legacy} on {_DAY} "
        f"(missing={legacy.missing}) -- cannot characterize, MCP data unavailable"
    )

    deps = SelericDeps(
        mission_id="characterization",
        as_of=datetime.now(tz=UTC),
        principal=Principal(principal_id="p1", workspace_id="ws1", user_id="u1"),
        thread_id="t1",
        run_id="r1",
        trace_id="tr1",
        context=ContextBundle(),
        mcp_client=runtime.mcp,
        artifact_store=InMemoryArtifactStore(),
        limits=ExecutionLimits(),
    )
    ctx = _RunContext(deps)
    period = datetime.fromisoformat(_DAY).replace(tzinfo=UTC)
    result = await semantic.query_metrics(
        ctx,
        metric_id=metric_id_catalogue,
        dimensions={},
        grain="none",
        period_start=period,
        period_end=period,
    )
    assert result.success, (
        f"SemanticToolset.query_metrics({metric_id_catalogue}) failed: {result.summary}. "
        "SemanticToolset currently calls MCP unscoped (no `module` argument) under the "
        "`observer_agent` identity -- if this metric requires module scoping to resolve "
        "unambiguously, this failure is that gap surfacing, not a transient error."
    )
    artifact = deps.artifact_store.get(result.artifact_ids[0])
    new_value = artifact.payload["value"]

    assert new_value == pytest.approx(legacy_value, rel=1e-6), (
        f"DIVERGENCE for {metric_id_catalogue} on {_DAY}: "
        f"SemanticToolset.query_metrics()={new_value} vs "
        f"McpDataProvider.fetch()={legacy_value}. The new toolset calls "
        "metrics_query directly with the catalogue id and no MetricRegistry/"
        "resolve_measure heuristic -- a divergence here means the legacy "
        "path's resolve_measure() was substituting a different measure than "
        "the catalogue id declared in metric_registry.yaml, which is itself "
        "worth flagging before Sprint 2 consolidation trusts either path."
    )
