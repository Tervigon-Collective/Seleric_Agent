"""Sprint 1 (Profile B) characterization: does SemanticToolset.query_metrics()
match one of the three legacy fetch paths, before Sprint 2 consolidation?

Per docs/refactor/SPRINT_PLAN.md Sprint 1: "Re-run
tests/replay/test_data_access_characterization.py against the new toolset
... to establish it matches at least one of the three legacy paths." This
compares the new toolset against ``HybridMcpDataProvider.fetch()`` (the path
``tests/replay/test_data_access_characterization.py`` already exercises) for
the same metric/day already used there (``metric.units_sold`` ->
catalogue id ``units_sold``, from ``config/metric_registry.yaml``).

Requires live Seleric MCP credentials (skips otherwise, same as every other
``runtime``-fixture test in this repo).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from seleric_swarm.agent.contracts import ExecutionLimits, SelericDeps
from seleric_swarm.conversations.contracts import ContextBundle, Principal
from seleric_swarm.state.artifacts import ArtifactStore
from seleric_swarm.swarm.providers.mcp_data import HybridMcpDataProvider, McpFetchStats
from seleric_swarm.toolsets import semantic

_DAY = "2026-08-01"
_METRIC_ID_LEGACY = "metric.units_sold"
_METRIC_ID_CATALOGUE = "units_sold"  # config/metric_registry.yaml catalogue_metric for the above
_AGENT_ID = "product_agent"


class _RunContext:
    def __init__(self, deps: SelericDeps) -> None:
        self.deps = deps


@pytest.mark.asyncio
async def test_semantic_toolset_query_metrics_matches_hybrid_provider_fetch(runtime):
    provider = HybridMcpDataProvider(
        "product",
        mcp=runtime.mcp,
        stats=McpFetchStats(),
        metrics=runtime.metrics,
        agent_id=_AGENT_ID,
    )
    legacy = await provider.fetch(
        metric_ids=[_METRIC_ID_LEGACY],
        time_range={"start": _DAY, "end": _DAY},
    )
    legacy_value = legacy.readings[0].value if legacy.readings else None
    assert legacy_value is not None, (
        f"HybridMcpDataProvider.fetch() returned no reading for {_METRIC_ID_LEGACY} on {_DAY} "
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
        artifact_store=ArtifactStore(),
        limits=ExecutionLimits(),
    )
    ctx = _RunContext(deps)
    period = datetime.fromisoformat(_DAY).replace(tzinfo=UTC)
    result = await semantic.query_metrics(
        ctx,
        metric_id=_METRIC_ID_CATALOGUE,
        dimensions={},
        grain="none",
        period_start=period,
        period_end=period,
    )
    assert result.success, f"SemanticToolset.query_metrics() failed: {result.summary}"
    artifact = deps.artifact_store.get(result.artifact_ids[0])
    new_value = artifact.payload["value"]

    assert new_value == pytest.approx(legacy_value, rel=1e-6), (
        f"DIVERGENCE for {_METRIC_ID_CATALOGUE} on {_DAY}: "
        f"SemanticToolset.query_metrics()={new_value} vs "
        f"HybridMcpDataProvider.fetch()={legacy_value}. The new toolset calls "
        "metrics_query directly with the catalogue id and no MetricRegistry/"
        "resolve_measure heuristic -- a divergence here means the legacy "
        "path's resolve_measure() was substituting a different measure than "
        "the catalogue id declared in metric_registry.yaml, which is itself "
        "worth flagging before Sprint 2 consolidation trusts either path."
    )
