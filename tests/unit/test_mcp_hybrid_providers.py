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


def _series_rows(measure: str, start: str, end: str) -> list[dict]:
    from datetime import date, timedelta

    s = date.fromisoformat(start[:10])
    e = date.fromisoformat(end[:10])
    rows = []
    day = s
    while day <= e:
        rows.append(
            {
                "view.report_date.day": f"{day.isoformat()}T00:00:00.000",
                measure: float(day.isoformat()[-2:]),
            }
        )
        day += timedelta(days=1)
    return rows


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
        assert arguments.get("granularity") == "day"
        start = arguments["time_range"]["start"]
        end = arguments["time_range"]["end"]
        return {"rows": _series_rows(measure, start, end)}

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
        start = arguments["time_range"]["start"]
        end = arguments["time_range"]["end"]
        return {"rows": _series_rows(measure, start, end)}

    provider._mcp.call = fake_call
    frame = await provider.fetch_series(
        metric_ids=["metric.cac"],
        time_range={"start": "2026-09-01", "end": "2026-09-08"},
    )
    assert frame is not None
    assert list(frame.columns) == ["metric.cac"]
    assert len(frame) == 8


@pytest.mark.asyncio
async def test_fetch_series_one_query_per_metric(runtime):
    bundle, _stats = build_hybrid_bundle(
        mcp=runtime.mcp, execution_mode="staging", metrics=runtime.metrics, agents=runtime.agents
    )
    provider = bundle.data_for("performance")
    in_flight = 0
    max_in_flight = 0
    query_count = 0

    async def fake_call(*, agent_id, capability, arguments):
        nonlocal in_flight, max_in_flight, query_count
        if capability == "seleric.catalogue_get_metric":
            return {"id": arguments.get("metric_id", ""), "display_name": "test"}
        query_count += 1
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.02)
        in_flight -= 1
        measure = arguments["measures"][0]
        start = arguments["time_range"]["start"]
        end = arguments["time_range"]["end"]
        return {"rows": _series_rows(measure, start, end)}

    provider._mcp.call = fake_call
    frame = await provider.fetch_series(
        metric_ids=["metric.cac", "metric.cpm"],
        time_range={"start": "2026-09-01", "end": "2026-09-08"},
    )
    assert frame is not None
    assert query_count == 2
    assert max_in_flight > 1


@pytest.mark.asyncio
async def test_fetch_passes_dimensions_to_mcp(runtime):
    bundle, _stats = build_hybrid_bundle(
        mcp=runtime.mcp, execution_mode="staging", metrics=runtime.metrics, agents=runtime.agents
    )
    provider = bundle.data_for("performance")
    seen: list[dict] = []

    async def fake_call(*, agent_id, capability, arguments):
        if capability == "seleric.catalogue_get_metric":
            return {"id": arguments.get("metric_id", ""), "display_name": "test"}
        seen.append(arguments)
        measure = arguments["measures"][0]
        return {
            "rows": [
                {measure: 10.0, "campaign": "A"},
                {measure: 7.0, "campaign": "B"},
            ]
        }

    provider._mcp.call = fake_call
    result = await provider.fetch(
        metric_ids=["metric.cac"],
        time_range={"start": "2026-09-01", "end": "2026-09-01"},
        dimensions={"campaign": ""},
        limit=2,
    )
    assert seen
    assert seen[0]["dimensions"] == ["campaign"]
    assert seen[0]["limit"] == 2
    assert [r.value for r in result.readings] == [10.0, 7.0]
    assert result.readings[0].dimensions == {"campaign": "A"}


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
# _resolve_measure core-logic tests
#
# Sprint 2 consolidation (docs/refactor/SPRINT_PLAN.md, 2026-09-18):
# _resolve_measure() no longer does exact-match validation, keyword-overlap
# semantic-search fallback, or stale-registry auto-substitution — that
# heuristic layer (services/measure.py::resolve_measure(), bug #8's root
# cause) is exactly what this profile retires. It is now a plain
# `definition.catalogue_metric` field read: zero MCP calls, zero guessing.
# A stale/missing catalogue_metric now surfaces as a live Cube error at the
# actual metrics_query call (caught by fetch()'s existing error handling)
# instead of being silently substituted. The tests below replace the ones
# that exercised the retired keyword-matching/substitution/caching behavior.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_measure_returns_catalogue_metric_field_with_no_mcp_calls(runtime):
    """_resolve_measure is now a static config-field read — no MCP round-trip
    at all, confirmed vs. current AND non-existent catalogue metric ids
    alike (validation happens downstream, at the real query call)."""
    bundle, stats = build_hybrid_bundle(
        mcp=runtime.mcp, execution_mode="staging", metrics=runtime.metrics, agents=runtime.agents
    )
    provider = bundle.data_for("performance")

    call_log: list[str] = []

    async def fake_call(*, agent_id, capability, arguments):
        call_log.append(capability)
        return {"rows": [{"cac": 1500.0}]}

    provider._mcp.call = fake_call

    definition = runtime.metrics.get("metric.cac")
    assert definition is not None, "metric.cac must be in registry"
    resolved = await provider._resolve_measure(definition)

    assert resolved == "cac"
    assert call_log == [], "resolving a measure must not call MCP at all anymore"
    assert not stats.stale_registry_subs, "substitution recording is retired along with the fallback"


@pytest.mark.asyncio
async def test_resolve_measure_returns_stale_id_verbatim_not_none(runtime):
    """A stale/phantom catalogue_metric is returned as-is (no substitution,
    no None) — the live metrics_query call surfaces the real failure instead."""
    from seleric_swarm.services.metrics import MetricDefinition

    bundle, _stats = build_hybrid_bundle(
        mcp=runtime.mcp, execution_mode="staging", metrics=runtime.metrics, agents=runtime.agents
    )
    provider = bundle.data_for("funnel")

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
    assert resolved == "ghost_measure"


@pytest.mark.asyncio
async def test_resolve_measure_returns_none_when_catalogue_metric_unset(runtime):
    """A registry entry with no catalogue_metric at all still returns None —
    there's nothing to query, no fallback to attempt."""
    from seleric_swarm.services.metrics import MetricDefinition

    bundle, _stats = build_hybrid_bundle(
        mcp=runtime.mcp, execution_mode="staging", metrics=runtime.metrics, agents=runtime.agents
    )
    provider = bundle.data_for("funnel")

    unset_def = MetricDefinition(
        {"id": "metric.unset_test", "description": "No catalogue_metric configured.", "domain": "funnel"}
    )
    provider._metrics._metrics[unset_def.id] = unset_def

    resolved = await provider._resolve_measure(unset_def)
    assert resolved is None


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
