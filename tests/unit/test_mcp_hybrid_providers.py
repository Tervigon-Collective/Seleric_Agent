"""MCP hybrid DataProvider tests (v1.11)."""

from __future__ import annotations

import pytest

from seleric_swarm.swarm.providers.mcp_data import build_hybrid_bundle


@pytest.mark.asyncio
async def test_hybrid_staging_uses_mcp_for_performance(runtime):
    bundle, stats = build_hybrid_bundle(
        "cac_regression",
        mcp=runtime.mcp,
        execution_mode="staging",
        metrics=runtime.metrics,
        agents=runtime.agents,
    )
    assert "seleric.metrics_query" in runtime.mcp.capabilities
    provider = bundle.data_for("performance")
    assert type(provider).__name__ == "HybridMcpDataProvider"
    result = await provider.fetch(
        metric_ids=["metric.cac"],
        time_range={"start": "2026-08-31", "end": "2026-09-03"},
    )
    assert result.readings
    cac = next(r for r in result.readings if r.metric_id == "metric.cac" and not r.dimensions)
    assert cac.data_origin == "MCP"
    assert cac.synthetic is False
    assert stats.mcp_hits >= 1
    assert "seleric.metrics_query" in stats.capabilities_used


@pytest.mark.asyncio
async def test_hybrid_production_returns_nothing_for_domain_with_no_module(runtime):
    # "technical" has no seleric_module (no live Technical MCP exists yet) —
    # no fixture fallback: missing live coverage means missing data, not a
    # fabricated synthetic number.
    bundle, stats = build_hybrid_bundle(
        "cac_regression",
        mcp=runtime.mcp,
        execution_mode="production",
        metrics=runtime.metrics,
        agents=runtime.agents,
    )
    technical = bundle.data_for("technical")
    assert type(technical).__name__ == "EmptyDataProvider"
    result = await technical.fetch(
        metric_ids=["metric.js_error_rate"],
        time_range={"start": "2026-08-31", "end": "2026-09-03"},
    )
    assert result.readings == []
    assert result.missing == ["metric.js_error_rate"]
    assert result.synthetic is False
    assert stats.mcp_attempts == 0


@pytest.mark.asyncio
async def test_swarm_v2_staging_surfaces_mcp_limitations(runtime):
    from seleric_swarm.coordinator.graph import run_swarm_v2_mission

    result = await run_swarm_v2_mission(
        runtime,
        scenario_id="cac_regression",
        query="Why has CAC increased over the last three days?",
        full_diagnostic=True,
        full_skeptic=True,
        as_of="2026-09-03",
        execution_mode="staging",
    )
    joined = " ".join(result.limitations)
    assert "execution_mode=staging" in joined or "MCP" in joined


def test_api_rejects_bad_execution_mode(runtime, monkeypatch):
    from fastapi.testclient import TestClient

    import seleric_swarm.main as main_mod
    from seleric_swarm.main import app

    monkeypatch.setattr(main_mod, "_runtime", runtime)
    client = TestClient(app, raise_server_exceptions=True)
    bad = client.post(
        "/v1/missions",
        json={"query": "why CAC?", "mode": "read_only", "execution_mode": "live"},
    )
    assert bad.status_code == 400
    assert "execution_mode" in bad.json()["detail"]
