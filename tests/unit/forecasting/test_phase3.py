"""Phase 3: catalogue eligibility, workspace brand, and forecast scoring."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from seleric_swarm.agent.forecast_plan import compile_forecast_plan
from seleric_swarm.agent.plan import MetricSlot
from seleric_swarm.agent.understand import ForecastHorizonSlot, ForecastSlots, Understanding
from seleric_swarm.forecasting.policies import load_forecast_policies
from seleric_swarm.forecasting.scope import brand_filters, principal_brand_id
from seleric_swarm.forecasting.score import is_due, score_payload
from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta, CatalogueSnapshot


def test_catalogue_forecast_block_overrides_yaml():
    policies = load_forecast_policies()
    status, reasons = policies.eligibility(
        "net_sales",
        catalogue_forecast={"status": "refused", "maturity_days": 30},
    )
    assert status == "refused"
    assert "T_CATALOGUE" in reasons


def test_catalogue_entity_not_in_block_refused():
    policies = load_forecast_policies()
    status, reasons = policies.eligibility(
        "net_sales",
        "product_sku",
        catalogue_forecast={"status": "validated", "entities": {"lt_platform": {}}},
    )
    assert status == "refused"
    assert "T_ENTITY_UNSUPPORTED" in reasons


@pytest.mark.asyncio
async def test_compile_uses_catalogue_forecast_block():
    cat = CatalogueSnapshot(
        metrics=(
            CatalogueMetricMeta(
                id="refunds",
                label="Refunds",
                raw={
                    "status": "certified",
                    "forecast": {"status": "validated", "maturity_days": 7, "nonnegative": True},
                },
            ),
        )
    )
    plan = await compile_forecast_plan(
        Understanding.model_validate(
            {
                "kind": "forecast",
                "shape": "lookup",
                "entity_dimension": "",
                "rank_by": None,
                "metrics": [MetricSlot(words="refunds", metric_id="refunds")],
                "breakdown_dimensions": [],
                "names_period": False,
                "forecast": ForecastSlots(
                    targets=[MetricSlot(words="refunds", metric_id="refunds")],
                    horizon=ForecastHorizonSlot(n=7),
                ),
            }
        ),
        catalogue=cat,
        as_of=date(2026, 10, 10),
    )
    target = next(t for t in plan.targets if t.metric_id == "refunds")
    assert target.status == "validated"
    assert "T_CATALOGUE" in target.reason_codes


def test_brand_filters_pin_workspace_brand():
    pinned = brand_filters(
        "42",
        [{"dimension": "brand_id", "operator": "equals", "values": ["20"]},
         {"dimension": "lt_platform", "operator": "equals", "values": ["meta"]}],
    )
    assert pinned is not None
    brands = [f for f in pinned if f["dimension"] == "brand_id"]
    assert brands == [{"dimension": "brand_id", "operator": "equals", "values": ["42"]}]
    assert any(f["dimension"] == "lt_platform" for f in pinned)


def test_principal_brand_from_workspace_config():
    class _Ctx:
        workspace_config = {"brand_id": "42"}

    class _Deps:
        context = _Ctx()

    assert principal_brand_id(_Deps()) == "42"
    assert principal_brand_id(object()) is None


def test_score_payload_pairs_mature_days():
    payload = {
        "targets": [
            {
                "metric_id": "orders",
                "days": [
                    {"date": "2026-10-01", "mean": 10, "p10": 8, "p50": 10, "p90": 12},
                    {"date": "2026-10-02", "mean": 11, "p10": 9, "p50": 11, "p90": 13},
                ],
            }
        ]
    }
    scores = score_payload(payload, {"orders": {"2026-10-01": 10.0, "2026-10-02": 12.0}})
    assert scores["orders"]["count"] == 2
    assert "coverage_80" in scores["orders"]


def test_score_job_is_due(tmp_path):
    stamp = tmp_path / ".last"
    now = datetime(2026, 10, 10, tzinfo=timezone.utc)
    assert is_due(stamp, now=now)
    stamp.write_text(now.isoformat(), encoding="utf-8")
    assert not is_due(stamp, now=now + timedelta(hours=1))
    assert is_due(stamp, now=now + timedelta(hours=25))
