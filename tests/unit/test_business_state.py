"""Sprint 1 checklist item: fixture series -> expected mean/delta.

Exercises the real series.py/features.py/facade.py path end to end against a
fake MCP gateway shaped exactly like the live commerce_net_revenue_daily
response (see docs/features/business-state-service/06_DATA_VALIDATION_FINDINGS.md),
using the golden fixture from tests/fixtures/business_state/.
"""

import json
from pathlib import Path

import pytest

from seleric_swarm.agents.base import AgentContext
from seleric_swarm.agents.intelligence.observer import Agent as ObserverAgent
from seleric_swarm.contracts.lookup import TimeRangeV1
from seleric_swarm.domain.models import StateRequest
from seleric_swarm.services.business_state.facade import BusinessStateService
from seleric_swarm.services.metrics import MetricRegistry

FIXTURES_DIR = Path(__file__).parents[1] / "fixtures" / "business_state"
FIXTURE = json.loads((FIXTURES_DIR / "net_sales_series.json").read_text())
NET_PROFIT_FIXTURE = json.loads((FIXTURES_DIR / "net_profit_series.json").read_text())


class _FakeGateway:
    def __init__(self, response: dict):
        self._response = response
        self.calls: list[dict] = []
        self.capabilities = {"seleric.metrics_query", "seleric.catalogue_get_metric"}

    async def call(self, *, agent_id, capability, arguments):
        self.calls.append({"agent_id": agent_id, "capability": capability, "arguments": arguments})
        if capability == "seleric.catalogue_get_metric":
            # services.measure.resolve_measure's exact-match confirmation --
            # any dict without an "error" key confirms the preferred id.
            return {"id": arguments.get("metric_id")}
        return self._response


def _cube_response(fixture: dict) -> dict:
    """Shape rows exactly like the live seleric-mcp response (`.day` suffix
    date key, string-typed measure values) so series.py's row parsing is
    exercised for real, not against a hand-simplified stand-in.
    """
    measure = fixture["catalogue_metric_id"]
    view = "commerce_performance"
    rows = [
        {
            f"{view}.report_date.day": f"{point['ts']}T00:00:00.000",
            measure: str(point["value"]),
        }
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fixture, agent_id",
    [
        pytest.param(FIXTURE, "commerce_agent", id="net_sales"),
        pytest.param(NET_PROFIT_FIXTURE, "finance_agent", id="net_profit"),
    ],
)
async def test_get_metric_state_matches_golden_fixture(fixture, agent_id):
    runtime, _ = _runtime(fixture)
    request = StateRequest(
        metric_id=fixture["metric_id"],
        time_range=TimeRangeV1(kind="absolute", start=fixture["series"][0]["ts"], end=fixture["series"][-1]["ts"]),
        agent_id=agent_id,
        need=["actual", "features"],
    )
    state = await runtime.business_state.get_metric_state(request)

    expected = fixture["expected_features"]
    assert state.status == "OK"
    assert state.actual == expected["current_value"]
    assert state.features["current_value"].value == expected["current_value"]
    assert state.features["period_delta_pct"].value == pytest.approx(expected["period_delta_pct"])
    assert state.features["rolling_mean_7d"].value == pytest.approx(expected["rolling_mean_7d"])
    assert state.features["rolling_std_7d"].value == pytest.approx(expected["rolling_std_7d"])
    assert state.quality_flags == []


@pytest.mark.asyncio
async def test_observer_emits_evidence_from_business_state():
    runtime, gateway = _runtime(FIXTURE)
    observer = ObserverAgent(runtime)
    ctx = AgentContext(
        mission_id="M-test", task_id="T-1", question="how are net sales trending",
        mission_lead="commerce_agent", payload={},
    )
    time_range = TimeRangeV1(kind="absolute", start=FIXTURE["series"][0]["ts"], end=FIXTURE["series"][-1]["ts"])

    rows = await observer.business_state_evidence(ctx, metric_id=FIXTURE["metric_id"], time_range=time_range)

    assert any(c["capability"] == "seleric.metrics_query" for c in gateway.calls)
    by_fact = {row["metric_or_fact"]: row for row in rows}
    assert by_fact[FIXTURE["metric_id"]]["value"] == FIXTURE["expected_features"]["current_value"]
    assert by_fact[f"{FIXTURE['metric_id']}.feature.rolling_mean_7d"]["value"] == pytest.approx(
        FIXTURE["expected_features"]["rolling_mean_7d"]
    )
    assert all(row["source"] == "deterministic.business_state" for row in rows)
