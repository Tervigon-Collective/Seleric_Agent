"""Live-MCP verification for Sprint 3 (no fixtures, no fake BusinessStateService).

Complements test_domain_health.py the same way test_business_state_live.py
complements test_business_state.py: the fake-BusinessStateService tests
prove the resolver's orchestration/rollup/headline-signal logic
deterministically; this file hits the real seleric-mcp (via conftest.py's
``runtime`` fixture, which skips if SELERIC_MCP_URL/TOKEN aren't configured)
to prove the commerce config actually resolves against live data.
"""

from __future__ import annotations

import pytest

from seleric_swarm.contracts.lookup import TimeRangeV1
from seleric_swarm.services.domain_health.resolver import DomainStateResolver
from seleric_swarm.services.domain_health.snapshot_store import SnapshotStore


@pytest.mark.asyncio
async def test_resolve_commerce_snapshot_live(runtime, tmp_path):
    resolver = DomainStateResolver(runtime.business_state)

    snapshot = await resolver.resolve(
        "commerce",
        time_range=TimeRangeV1(kind="relative", relative_token="last_7d"),
    )

    assert snapshot.domain == "commerce"
    assert snapshot.brand_id == "20"
    assert snapshot.status in {"OK", "DEGRADED", "UNAVAILABLE"}
    assert {m.metric_id for m in snapshot.metrics} == {
        "metric.net_sales",
        "metric.gross_sales",
        "metric.orders",
        "metric.returns_cancels",
    }
    # net_sales and orders both request period_delta_pct -- real 7d history
    # should resolve it, not go sparse.
    by_metric = {m.metric_id: m for m in snapshot.metrics}
    assert isinstance(by_metric["metric.net_sales"].value, float)
    assert isinstance(by_metric["metric.net_sales"].period_delta_pct, float)
    assert isinstance(by_metric["metric.orders"].period_delta_pct, float)
    assert snapshot.provenance["mcp_query_ids"], "expected real query ids from live MCP calls"
    # headline_signals is deterministic from real numbers -- just assert it's
    # a list of strings, not fabricated by an LLM.
    assert all(isinstance(s, str) for s in snapshot.headline_signals)

    store = SnapshotStore(base_dir=tmp_path)
    store.save(snapshot)
    assert store.get_latest("commerce") == snapshot
