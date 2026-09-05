"""MCP hybrid DataProvider tests (v1.11)."""

from __future__ import annotations

import pytest

from seleric_swarm.swarm.providers.mcp_data import DOMAIN_MCP_ROUTES, build_hybrid_bundle


@pytest.mark.asyncio
async def test_hybrid_staging_uses_mcp_for_performance(runtime):
    bundle, stats = build_hybrid_bundle(
        "cac_regression", mcp=runtime.mcp, execution_mode="staging"
    )
    if "performance.daily_cac" not in runtime.mcp.capabilities:
        pytest.skip("performance.daily_cac not in MCP gateway (live catalogue-only config)")
    provider = bundle.data_for("performance")
    assert provider is not None
    result = await provider.fetch(
        metric_ids=["metric.cac"],
        time_range={"start": "2026-09-03", "end": "2026-09-03"},
    )
    assert result.readings
    cac = next(r for r in result.readings if r.metric_id == "metric.cac" and not r.dimensions)
    # Either MCP overlay hit or fallback — both valid; staging with fixture MCP should hit.
    if stats.mcp_hits:
        assert cac.data_origin == "MCP"
        assert "performance.daily_cac" in stats.capabilities_used
        assert any("MCP data path used" in line for line in stats.limitations())
    else:
        assert stats.mcp_fallbacks >= 1


@pytest.mark.asyncio
async def test_hybrid_fixture_mode_skips_mcp(runtime):
    bundle, stats = build_hybrid_bundle(
        "cac_regression", mcp=runtime.mcp, execution_mode="fixture"
    )
    provider = bundle.data_for("commerce")
    assert provider is not None
    # Pure FixtureDataProvider — no Hybrid wrapper in fixture mode
    assert type(provider).__name__ == "FixtureDataProvider"
    await provider.fetch(
        metric_ids=["metric.net_sales"],
        time_range={"start": "2026-09-01", "end": "2026-09-01"},
    )
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


def test_domain_mcp_routes_cover_registry_capabilities():
    assert DOMAIN_MCP_ROUTES["commerce"][1] == "commerce.daily_sales"
    assert DOMAIN_MCP_ROUTES["performance"][1] == "performance.daily_cac"


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
