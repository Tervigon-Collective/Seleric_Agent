"""Live-MCP verification for Sprint 0-2.5 (no fixtures, no fake gateway).

The other business_state tests (test_business_state.py,
test_business_state_anomaly.py) use a fake MCP gateway fed by golden
fixtures -- that's deliberate: it proves the feature/anomaly *math* against
known inputs, deterministically, without depending on live data that
changes daily. This file is the complement: it hits the real seleric-mcp
(via conftest.py's ``runtime`` fixture, which skips if SELERIC_MCP_URL/TOKEN
aren't configured) for all 3 pilot metrics, and asserts on structure/ranges
rather than exact values, since the real numbers move every day.
"""

from __future__ import annotations

import pytest

from seleric_swarm.contracts.lookup import TimeRangeV1
from seleric_swarm.domain.models import StateRequest

PILOT_METRICS = [
    ("metric.net_sales", "commerce_agent"),
    ("metric.spend", "performance_agent"),
    ("metric.net_profit", "finance_agent"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("metric_id, agent_id", PILOT_METRICS)
async def test_get_metric_state_live_actual_and_features(runtime, metric_id, agent_id):
    request = StateRequest(
        metric_id=metric_id,
        time_range=TimeRangeV1(kind="relative", relative_token="last_7d"),
        dimensions={"brand_id": "20"},
        agent_id=agent_id,
        need=["actual", "features"],
    )
    state = await runtime.business_state.get_metric_state(request)

    assert state.status in {"OK", "PARTIAL"}, state.quality_flags
    assert isinstance(state.actual, float)
    assert state.freshness in {"CURRENT", "LATE", "STALE"}
    for feature_id in ("current_value", "rolling_mean_7d", "rolling_std_7d"):
        assert feature_id in state.features
        assert isinstance(state.features[feature_id].value, float)
    # rolling_std_7d is a population stdev -- never negative.
    assert state.features["rolling_std_7d"].value >= 0
    assert state.provenance.get("query_id")
    assert state.provenance.get("generated_at")


@pytest.mark.asyncio
@pytest.mark.parametrize("metric_id, agent_id", PILOT_METRICS)
async def test_evaluate_anomaly_live_never_fabricates(runtime, metric_id, agent_id):
    request = StateRequest(
        metric_id=metric_id,
        time_range=TimeRangeV1(kind="relative", relative_token="last_7d"),
        dimensions={"brand_id": "20"},
        agent_id=agent_id,
        need=[],
    )
    anomaly = await runtime.business_state.evaluate_anomaly(request)

    # Real history for all 3 pilot metrics comfortably exceeds min_points=14
    # over the profile's 28d window, so this should resolve, not go sparse --
    # if it doesn't, that's real data drift worth seeing fail loudly.
    assert anomaly is not None
    assert anomaly["direction"] in {"up", "down", "flat"}
    assert anomaly["score"] >= 0
    assert isinstance(anomaly["is_anomaly"], bool)
    assert anomaly["detector"]["strategy"] == "robust_zscore"
