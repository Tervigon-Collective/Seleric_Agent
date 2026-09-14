"""Sprint 2 checklist: robust z-score/MAD detector + evaluate_anomaly() wiring.

Exercises the real detectors.py/facade.py path end to end (window widening
to cover the anomaly profile's 28d default, median+MAD scoring) against the
golden fixture, plus the SPARSE_HISTORY gating rule (03 SS5): too little
history must return anomaly=None, never a fabricated score.
"""

import json
from pathlib import Path

import pytest

from seleric_swarm.contracts.lookup import TimeRangeV1
from seleric_swarm.domain.models import StateRequest
from seleric_swarm.services.business_state.detectors import robust_zscore
from seleric_swarm.services.business_state.facade import BusinessStateService
from seleric_swarm.services.metrics import MetricRegistry

FIXTURE = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "business_state" / "net_sales_anomaly_series.json").read_text()
)


class _FakeGateway:
    def __init__(self, response: dict):
        self._response = response
        self.calls: list[dict] = []
        self.capabilities = {"seleric.metrics_query", "seleric.catalogue_get_metric"}

    async def call(self, *, agent_id, capability, arguments):
        self.calls.append({"agent_id": agent_id, "capability": capability, "arguments": arguments})
        if capability == "seleric.catalogue_get_metric":
            return {"id": arguments.get("metric_id")}
        return self._response


def _cube_response(fixture: dict) -> dict:
    measure = fixture["catalogue_metric_id"]
    view = "commerce_performance"
    rows = [
        {f"{view}.report_date.day": f"{point['ts']}T00:00:00.000", measure: str(point["value"])}
        for point in fixture["series"]
    ]
    return {
        "rows": rows,
        "provenance": {
            "cube_view": view,
            "generated_at": f"{fixture['as_of']}T12:00:00+00:00",
            "freshness": {"cube_last_refresh": f"{fixture['as_of']}T11:00:00+00:00"},
        },
    }


def _runtime(fixture: dict):
    from types import SimpleNamespace

    metrics = MetricRegistry("config/metric_registry.yaml")
    gateway = _FakeGateway(_cube_response(fixture))
    runtime = SimpleNamespace(metrics=metrics, mcp=gateway)
    runtime.business_state = BusinessStateService(runtime)
    return runtime, gateway


def test_robust_zscore_matches_hand_worked_fixture():
    values = [p["value"] for p in FIXTURE["series"]]
    history, observed = values[:-1], values[-1]
    result = robust_zscore(history, observed, z_threshold=3.0)
    expected = FIXTURE["expected_anomaly"]
    assert result.expected == expected["expected"]
    assert result.deviation_pct == pytest.approx(expected["deviation_pct"])
    assert result.score == pytest.approx(expected["score"])
    assert result.direction == expected["direction"]
    assert result.is_anomaly == expected["is_anomaly"]


@pytest.mark.asyncio
async def test_get_metric_state_anomaly_matches_golden_fixture():
    runtime, gateway = _runtime(FIXTURE)
    request = StateRequest(
        metric_id=FIXTURE["metric_id"],
        time_range=TimeRangeV1(kind="absolute", start=FIXTURE["series"][-1]["ts"], end=FIXTURE["series"][-1]["ts"]),
        agent_id="commerce_agent",
        need=["anomaly"],
    )
    state = await runtime.business_state.get_metric_state(request)

    # Fetch window must have been widened past the 1-day request to cover the
    # anomaly profile's 28d default -- otherwise this would starve on history.
    query_call = next(c for c in gateway.calls if c["capability"] == "seleric.metrics_query")
    assert query_call["arguments"]["time_range"]["start"] < FIXTURE["series"][-1]["ts"]

    expected = FIXTURE["expected_anomaly"]
    assert state.status == "OK"
    assert state.anomaly is not None
    assert state.anomaly["observed"] == expected["observed"]
    assert state.anomaly["expected"] == expected["expected"]
    assert state.anomaly["score"] == pytest.approx(expected["score"])
    assert state.anomaly["direction"] == expected["direction"]
    assert state.anomaly["is_anomaly"] == expected["is_anomaly"]
    assert state.anomaly["detector"]["strategy"] == "robust_zscore"
    assert state.quality_flags == []


@pytest.mark.asyncio
async def test_anomaly_sparse_history_never_fabricates_a_score():
    """Fewer points than the profile's min_points -> anomaly=None + SPARSE_HISTORY,
    never a computed-but-untrustworthy score (03 SS5)."""
    short_fixture = dict(FIXTURE, series=FIXTURE["series"][-5:])
    runtime, _ = _runtime(short_fixture)
    request = StateRequest(
        metric_id=short_fixture["metric_id"],
        time_range=TimeRangeV1(kind="absolute", start=short_fixture["series"][-1]["ts"], end=short_fixture["series"][-1]["ts"]),
        agent_id="commerce_agent",
        need=["anomaly"],
    )
    state = await runtime.business_state.get_metric_state(request)

    assert state.anomaly is None
    assert "SPARSE_HISTORY" in state.quality_flags
    assert state.status == "PARTIAL"


@pytest.mark.asyncio
async def test_evaluate_anomaly_returns_same_subset():
    runtime, _ = _runtime(FIXTURE)
    request = StateRequest(
        metric_id=FIXTURE["metric_id"],
        time_range=TimeRangeV1(kind="absolute", start=FIXTURE["series"][-1]["ts"], end=FIXTURE["series"][-1]["ts"]),
        agent_id="commerce_agent",
        need=[],
    )
    anomaly = await runtime.business_state.evaluate_anomaly(request)
    assert anomaly is not None
    assert anomaly["direction"] == FIXTURE["expected_anomaly"]["direction"]
