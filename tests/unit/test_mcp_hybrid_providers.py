"""MCP hybrid DataProvider tests (v1.11)."""

from __future__ import annotations

import asyncio

import pytest

from seleric_swarm.swarm.providers.mcp_data import build_hybrid_bundle


@pytest.mark.asyncio
async def test_hybrid_staging_uses_mcp_for_performance(runtime):
    bundle, stats = build_hybrid_bundle(
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
async def test_fetch_series_below_min_rows_returns_none(runtime):
    # docs/44 ROB-002: a short (below min_rows) window must not hand DoWhy a
    # rank-deficient dataset — better to decline than fabricate confidence.
    bundle, _stats = build_hybrid_bundle(
        mcp=runtime.mcp, execution_mode="staging", metrics=runtime.metrics, agents=runtime.agents
    )
    provider = bundle.data_for("performance")

    async def fake_call(*, agent_id, capability, arguments):
        if capability == "seleric.catalogue_get_metric":
            # Confirm any requested measure as existing (Step 1 always succeeds).
            return {"id": arguments.get("metric_id", ""), "display_name": "test"}
        return {"rows": [{"cac": 100.0}]}

    provider._mcp.call = fake_call
    frame = await provider.fetch_series(
        metric_ids=["metric.cac"],
        time_range={"start": "2026-09-01", "end": "2026-09-03"},  # 3 days < min_rows=8
    )
    assert frame is None


@pytest.mark.asyncio
async def test_fetch_series_returns_dataframe_when_enough_days(runtime):
    bundle, _stats = build_hybrid_bundle(
        mcp=runtime.mcp, execution_mode="staging", metrics=runtime.metrics, agents=runtime.agents
    )
    provider = bundle.data_for("performance")

    async def fake_call(*, agent_id, capability, arguments):
        if capability == "seleric.catalogue_get_metric":
            return {"id": arguments.get("metric_id", ""), "display_name": "test"}
        measure = arguments["measures"][0]
        day = arguments["time_range"]["start"]
        return {"rows": [{measure: float(day[-2:])}]}  # deterministic per-day value

    provider._mcp.call = fake_call
    frame = await provider.fetch_series(
        metric_ids=["metric.cac", "metric.cpm"],
        time_range={"start": "2026-09-01", "end": "2026-09-08"},  # 8 days == min_rows
    )
    assert frame is not None
    assert set(frame.columns) == {"metric.cac", "metric.cpm"}
    assert len(frame) == 8


@pytest.mark.asyncio
async def test_fetch_series_returns_single_metric_frame(runtime):
    bundle, _stats = build_hybrid_bundle(
        mcp=runtime.mcp, execution_mode="staging", metrics=runtime.metrics, agents=runtime.agents
    )
    provider = bundle.data_for("performance")

    async def fake_call(*, agent_id, capability, arguments):
        if capability == "seleric.catalogue_get_metric":
            return {"id": arguments.get("metric_id", ""), "display_name": "test"}
        measure = arguments["measures"][0]
        return {"rows": [{measure: 1.0}]}

    provider._mcp.call = fake_call
    frame = await provider.fetch_series(
        metric_ids=["metric.cac"],
        time_range={"start": "2026-09-01", "end": "2026-09-08"},
    )
    assert frame is not None
    assert list(frame.columns) == ["metric.cac"]
    assert len(frame) == 8


@pytest.mark.asyncio
async def test_fetch_series_runs_day_queries_concurrently(runtime):
    bundle, _stats = build_hybrid_bundle(
        mcp=runtime.mcp, execution_mode="staging", metrics=runtime.metrics, agents=runtime.agents
    )
    provider = bundle.data_for("performance")
    in_flight = 0
    max_in_flight = 0

    async def fake_call(*, agent_id, capability, arguments):
        nonlocal in_flight, max_in_flight
        if capability == "seleric.catalogue_get_metric":
            return {"id": arguments.get("metric_id", ""), "display_name": "test"}
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.02)
        in_flight -= 1
        measure = arguments["measures"][0]
        return {"rows": [{measure: 1.0}]}

    provider._mcp.call = fake_call
    frame = await provider.fetch_series(
        metric_ids=["metric.cac", "metric.cpm"],
        time_range={"start": "2026-09-01", "end": "2026-09-08"},
    )
    assert frame is not None
    assert max_in_flight > 1


@pytest.mark.asyncio
async def test_hybrid_direction_bad_comes_from_registry_not_hardcoded(runtime):
    # docs/44 ROB-001: net_sales is "more is better" (direction_bad: down in
    # metric_registry.yaml) — an upward move must never be reported adverse.
    bundle, _stats = build_hybrid_bundle(
        mcp=runtime.mcp,
        execution_mode="staging",
        metrics=runtime.metrics,
        agents=runtime.agents,
    )
    provider = bundle.data_for("commerce")
    result = await provider.fetch(
        metric_ids=["metric.net_sales"],
        time_range={"start": "2026-08-31", "end": "2026-09-03"},
    )
    reading = next((r for r in result.readings if r.metric_id == "metric.net_sales"), None)
    assert reading is not None
    assert reading.direction_bad == "down"


@pytest.mark.asyncio
async def test_hybrid_production_returns_nothing_for_domain_with_no_module(runtime):
    # "technical" has no seleric_module (no live Technical MCP exists yet) —
    # no fixture fallback: missing live coverage means missing data, not a
    # fabricated synthetic number.
    bundle, stats = build_hybrid_bundle(
        mcp=runtime.mcp,
        execution_mode="production",
        metrics=runtime.metrics,
        agents=runtime.agents,
    )
    technical = bundle.data_for("technical")
    assert type(technical).__name__ == "EmptyDataProvider"
    result = await technical.fetch(
        metric_ids=["metric.unregistered"],
        time_range={"start": "2026-08-31", "end": "2026-09-03"},
    )
    assert result.readings == []
    assert result.missing == ["metric.unregistered"]
    assert result.synthetic is False
    assert stats.mcp_attempts == 0


@pytest.mark.asyncio
async def test_swarm_v2_staging_surfaces_mcp_limitations(runtime):
    from seleric_swarm.coordinator.graph import run_swarm_v2_mission

    result = await run_swarm_v2_mission(
        runtime,
        query="Why has CAC increased over the last three days?",
        full_diagnostic=True,
        full_skeptic=True,
        as_of="2026-09-03",
        execution_mode="staging",
    )
    joined = " ".join(result.limitations)
    assert "execution_mode=staging" in joined or "MCP" in joined


# ---------------------------------------------------------------------------
# _resolve_measure core-logic tests (the registry-to-catalogue contract)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_measure_exact_match_uses_fast_path(runtime):
    """When registry catalogue_metric ID exists in the live catalogue,
    only ONE call is made (catalogue_get_metric direct lookup succeeds).
    No semantic fallback, no substitution recorded.
    """
    bundle, stats = build_hybrid_bundle(
        mcp=runtime.mcp, execution_mode="staging", metrics=runtime.metrics, agents=runtime.agents
    )
    provider = bundle.data_for("performance")

    call_log: list[tuple[str, str]] = []

    async def fake_call(*, agent_id, capability, arguments):
        if capability == "seleric.catalogue_get_metric":
            metric_id = arguments.get("metric_id", "")
            call_log.append((capability, metric_id))
            # Simulate: "cac" is confirmed in the catalogue.
            if metric_id == "cac":
                return {"id": "cac", "display_name": "CAC"}
            return {"error": f"Unknown metric '{metric_id}'"}
        call_log.append((capability, str(arguments.get("query", ""))))
        return {"rows": [{"cac": 1500.0}]}

    provider._mcp.call = fake_call

    definition = runtime.metrics.get("metric.cac")
    assert definition is not None, "metric.cac must be in registry"
    resolved = await provider._resolve_measure(definition)

    assert resolved == "cac"
    # Only one call: catalogue_get_metric — no semantic fallback.
    get_metric_calls = [l for l in call_log if l[0] == "seleric.catalogue_get_metric"]
    assert len(get_metric_calls) == 1
    search_calls = [l for l in call_log if l[0] == "seleric.catalogue_search_metrics"]
    assert len(search_calls) == 0, "semantic search must not be called when ID is confirmed"
    assert not stats.stale_registry_subs, "no substitution for a current registry entry"


@pytest.mark.asyncio
async def test_resolve_measure_stale_registry_falls_back_to_semantic(runtime):
    """When the registry's catalogue_metric ID is absent (catalogue_get_metric
    returns an error), _resolve_measure searches by description and uses the
    first keyword-matching candidate.  The substitution is recorded so the
    operator knows to update metric_registry.yaml.
    """
    bundle, stats = build_hybrid_bundle(
        mcp=runtime.mcp, execution_mode="staging", metrics=runtime.metrics, agents=runtime.agents
    )
    provider = bundle.data_for("funnel")

    call_log: list[tuple[str, str]] = []

    async def fake_call(*, agent_id, capability, arguments):
        if capability == "seleric.catalogue_get_metric":
            metric_id = arguments.get("metric_id", "")
            call_log.append((capability, metric_id))
            # Stale ID: not in catalogue → error returned
            return {"error": f"Unknown metric '{metric_id}'", "suggestions": []}
        if capability == "seleric.catalogue_search_metrics":
            call_log.append((capability, str(arguments.get("query", ""))))
            # Semantic search by description finds the current equivalent.
            # "session_conversion_rate" shares "session"/"conversion"/"rate"
            # tokens with id "metric.purchase_cvr_stale_test" → overlap guard
            # accepts it.
            return {"matches": [{"id": "session_conversion_rate"}]}
        return {"rows": [{"session_conversion_rate": 0.035}]}

    provider._mcp.call = fake_call

    from seleric_swarm.services.metrics import MetricDefinition
    # Realistic stale scenario: the catalogue renamed "session_purchase_rate" to
    # "session_conversion_rate".  The registry metric's ID tokens include "session"
    # which also appears in "session_conversion_rate" → keyword guard accepts it.
    stale_def = MetricDefinition(
        {
            "id": "metric.session_purchase_rate",   # tokens: {"session","purchase","rate"}
            "catalogue_metric": "session_purchase_rate",   # stale — not in catalogue
            "description": "Session purchase rate — sessions that completed a purchase.",
            "domain": "funnel",
        }
    )
    provider._metrics._metrics[stale_def.id] = stale_def

    resolved = await provider._resolve_measure(stale_def)

    # Must resolve to the keyword-safe semantic match, NOT the stale ID.
    # "session_conversion_rate" shares "session" (and "rate") with the id tokens.
    assert resolved == "session_conversion_rate"
    # Substitution must be recorded.
    assert len(stats.stale_registry_subs) == 1
    metric_id, stale, replaced = stats.stale_registry_subs[0]
    assert metric_id == "metric.session_purchase_rate"
    assert stale == "session_purchase_rate"
    assert replaced == "session_conversion_rate"
    # Two calls total: catalogue_get_metric (exact check) + catalogue_search_metrics (semantic).
    get_metric_calls = [l for l in call_log if l[0] == "seleric.catalogue_get_metric"]
    search_calls = [l for l in call_log if l[0] == "seleric.catalogue_search_metrics"]
    assert len(get_metric_calls) == 1
    assert len(search_calls) == 1
    # Limitation text should name the stale ID and the resolved replacement.
    limitation_text = " ".join(stats.limitations())
    assert "session_purchase_rate" in limitation_text
    assert "session_conversion_rate" in limitation_text


@pytest.mark.asyncio
async def test_resolve_measure_unresolvable_returns_none_not_stale_id(runtime):
    """When neither the exact lookup NOR any keyword-safe semantic match finds
    the metric, _resolve_measure must return None — never the stale ID —
    so the caller emits a clear 'measure not found' limitation instead of a
    misleading 'no data for period' error.
    """
    bundle, stats = build_hybrid_bundle(
        mcp=runtime.mcp, execution_mode="staging", metrics=runtime.metrics, agents=runtime.agents
    )
    provider = bundle.data_for("funnel")

    async def fake_call(*, agent_id, capability, arguments):
        if capability == "seleric.catalogue_get_metric":
            return {"error": "Unknown metric", "suggestions": []}
        # Semantic search returns candidates but none share tokens with "ghost_metric"
        return {"matches": [{"id": "total_orders"}, {"id": "web_sessions"}]}

    provider._mcp.call = fake_call

    from seleric_swarm.services.metrics import MetricDefinition
    ghost_def = MetricDefinition(
        {
            "id": "metric.ghost_metric",
            "catalogue_metric": "ghost_measure",
            "description": "A retired metric that has no current catalogue equivalent.",
            "domain": "funnel",
        }
    )
    provider._metrics._metrics[ghost_def.id] = ghost_def

    resolved = await provider._resolve_measure(ghost_def)

    # Must be None — never the stale preferred ID.
    assert resolved is None, "unresolvable metric must return None, not the phantom ID"
    # No substitution recorded (nothing safe to substitute with).
    assert not stats.stale_registry_subs


@pytest.mark.asyncio
async def test_resolve_measure_cache_prevents_duplicate_catalogue_calls(runtime):
    """Second call for the same metric ID must return the cached result with
    no additional MCP calls — ensures the mission pays the resolution cost
    once per metric, not once per observer wave.
    """
    bundle, _stats = build_hybrid_bundle(
        mcp=runtime.mcp, execution_mode="staging", metrics=runtime.metrics, agents=runtime.agents
    )
    provider = bundle.data_for("performance")

    call_count = 0

    async def counting_call(*, agent_id, capability, arguments):
        nonlocal call_count
        if capability == "seleric.catalogue_get_metric":
            call_count += 1
            return {"id": "cac", "display_name": "CAC"}  # always confirmed
        return {"rows": [{"cac": 1200.0}]}

    provider._mcp.call = counting_call

    definition = runtime.metrics.get("metric.cac")
    await provider._resolve_measure(definition)
    calls_after_first = call_count
    await provider._resolve_measure(definition)   # second call — must use cache
    assert call_count == calls_after_first, "no extra catalogue calls on cache hit"


@pytest.mark.asyncio
async def test_resolve_measure_keyword_guard_rejects_wrong_domain_fallback(runtime):
    """The keyword-overlap guard must reject semantic-search candidates that
    share no ID tokens with the registry metric — the canonical wrong-fallback
    case is return_rate → total_orders, which is prevented by the guard.
    """
    bundle, stats = build_hybrid_bundle(
        mcp=runtime.mcp, execution_mode="staging", metrics=runtime.metrics, agents=runtime.agents
    )
    provider = bundle.data_for("commerce")

    async def fake_call(*, agent_id, capability, arguments):
        if capability == "seleric.catalogue_get_metric":
            return {"error": "Unknown metric 'return_rate'", "suggestions": []}
        # Catalogue returns "total_orders" as semantic match for the description
        # "Returns as a share of orders" — this is wrong and must be rejected.
        return {"matches": [{"id": "total_orders"}, {"id": "orders"}]}

    provider._mcp.call = fake_call

    from seleric_swarm.services.metrics import MetricDefinition
    rr_def = MetricDefinition(
        {
            "id": "metric.return_rate",
            "catalogue_metric": "return_rate",
            "description": "Returns as a share of orders.",
            "domain": "commerce",
        }
    )
    provider._metrics._metrics[rr_def.id] = rr_def

    resolved = await provider._resolve_measure(rr_def)

    # Neither total_orders nor orders shares a token with "return_rate" → None.
    assert resolved is None, "keyword guard must reject total_orders as a return_rate substitute"
    assert not stats.stale_registry_subs, "no substitution when guard rejects all candidates"


@pytest.mark.asyncio
async def test_resolve_measure_step0_hits_bootstrap_cache_without_mcp_call(runtime):
    """When CatalogueBootstrap has already cached the preferred catalogue_metric
    ID, _resolve_measure must return it via Step 0 with ZERO MCP calls.

    This is the hot-path guarantee: once the bootstrap cache is warm,
    every subsequent _resolve_measure call for a known metric costs nothing.
    """
    from unittest.mock import AsyncMock, MagicMock

    from seleric_swarm.services.catalogue_bootstrap import CatalogueBootstrap
    from seleric_swarm.swarm.providers.mcp_data import build_hybrid_bundle

    # Build a pre-warmed bootstrap whose cache already contains "cac".
    mock_mcp_for_bootstrap = MagicMock()
    mock_mcp_for_bootstrap.call = AsyncMock(
        return_value={"matches": [{"id": "cac", "label": "CAC", "view": "ltv_cac"}]}
    )
    bootstrap = CatalogueBootstrap(mock_mcp_for_bootstrap, ttl_seconds=3600)
    await bootstrap.warm()   # pre-warm — cache now has "cac"
    assert bootstrap.has("cac"), "bootstrap cache must be warm before the test"

    # Build the provider bundle using the pre-warmed bootstrap.
    bundle, _stats = build_hybrid_bundle(
        mcp=runtime.mcp,
        execution_mode="staging",
        metrics=runtime.metrics,
        agents=runtime.agents,
        bootstrap=bootstrap,
    )
    provider = bundle.data_for("performance")

    # Intercept MCP calls made by the provider itself to verify Step 0 fires.
    provider_call_log: list[str] = []

    async def recording_call(*, agent_id, capability, arguments):
        provider_call_log.append(capability)
        # Fallback response in case Step 0 is unexpectedly bypassed.
        return {"id": "cac", "display_name": "CAC"}

    provider._mcp.call = recording_call

    definition = runtime.metrics.get("metric.cac")
    assert definition is not None
    resolved = await provider._resolve_measure(definition)

    assert resolved == "cac", "Step 0 should return the cached catalogue_metric"
    # Key assertion: the provider must NOT have called catalogue_get_metric or
    # catalogue_search_metrics — those are Steps 1 and 2, which are bypassed
    # when the bootstrap cache already confirms the ID.
    assert provider_call_log == [], (
        f"Expected zero provider MCP calls via Step 0, got: {provider_call_log}"
    )


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
