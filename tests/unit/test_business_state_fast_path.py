"""Business-state: the snapshot freshness gate and snapshot view, and the endpoint that now runs the agent loop."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from seleric_swarm.api import business_state as mod
from seleric_swarm.services.business_state.formatter import _snapshot_context, is_stale
from seleric_swarm.services.domain_health.models import DomainStateSnapshot, ResolvedMetric


def _snapshot(computed_at: str, *, status: str = "OK") -> DomainStateSnapshot:
    return DomainStateSnapshot(
        domain="business",
        brand_id="20",
        as_of="2026-09-29",
        computed_at=computed_at,
        status=status,
        metrics=[ResolvedMetric(metric_id="metric.net_sales", value=71727.93, period_delta_pct=-18.0)],
    )


def test_fresh_snapshot_is_not_stale():
    assert is_stale(_snapshot(datetime.now(UTC).isoformat())) is False


def test_old_snapshot_is_stale():
    assert is_stale(_snapshot((datetime.now(UTC) - timedelta(hours=3)).isoformat())) is True


def test_unparseable_computed_at_is_stale():
    assert is_stale(_snapshot("not-a-date")) is True


def test_snapshot_view_states_period_baseline_and_unit():
    view = _snapshot_context(_snapshot(datetime.now(UTC).isoformat()), {"metric.net_sales": "INR"})
    assert view["period"] == "2026-09-29" and "one day" in view["values_are"]
    (metric,) = view["metrics"]
    assert metric["metric"] == "net_sales" and metric["unit"] == "INR" and metric["change_pct_vs_prior_day"] == -18.0


class _Request:
    def __init__(self):
        self.state = SimpleNamespace(request_id="req-1")
        self.app = SimpleNamespace(state=SimpleNamespace(runtime_provider=lambda: object()))


@pytest.mark.asyncio
async def test_endpoint_runs_the_agent_loop(monkeypatch):
    seen = {}

    async def fake_mission(runtime, **kw):
        seen.update(kw)
        return {"result": {"final_response": "You did ₹71,728 in net sales."}}

    monkeypatch.setattr(mod, "run_v3_mission", fake_mission)
    monkeypatch.setattr(mod, "request_principal", lambda request: SimpleNamespace(workspace_id="w", user_id="u"))
    out = await mod.business_state(mod.BusinessStateRequest(question="How is the business?"), _Request())
    assert out["source"] == "agent_loop" and out["answer"] == "You did ₹71,728 in net sales."
    assert seen["query"] == "How is the business?" and seen["timezone"] == "Asia/Kolkata"


@pytest.mark.asyncio
async def test_endpoint_rejects_an_empty_question():
    with pytest.raises(HTTPException) as exc:
        await mod.business_state(mod.BusinessStateRequest(question="  "), _Request())
    assert exc.value.status_code == 400
