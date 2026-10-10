"""Forecast plan compiler."""

from __future__ import annotations

from datetime import date

import pytest

from seleric_swarm.agent.forecast_plan import compile_forecast_plan, render_forecast_plan
from seleric_swarm.agent.plan import MetricSlot
from seleric_swarm.agent.understand import ForecastHorizonSlot, ForecastSlots, Understanding
from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta, CatalogueSnapshot


def _catalogue() -> CatalogueSnapshot:
    return CatalogueSnapshot(
        metrics=(
            CatalogueMetricMeta(
                id="net_sales",
                label="Net sales",
                raw={"status": "certified", "date_basis": "order_date", "unit": "INR"},
            ),
            CatalogueMetricMeta(
                id="orders",
                label="Orders",
                raw={"status": "certified", "date_basis": "order_date", "unit": "count"},
            ),
        )
    )


def _u(**kw) -> Understanding:
    base = {
        "kind": "forecast",
        "shape": "lookup",
        "entity_dimension": "",
        "rank_by": None,
        "metrics": [MetricSlot(words="net sales", metric_id="net_sales")],
        "breakdown_dimensions": [],
        "names_period": False,
        "forecast": ForecastSlots(
            targets=[MetricSlot(words="net sales", metric_id="net_sales")],
            horizon=ForecastHorizonSlot(n=14, unit="day"),
        ),
    }
    base.update(kw)
    return Understanding.model_validate(base)


@pytest.mark.asyncio
async def test_compile_net_sales_validated():
    plan = await compile_forecast_plan(
        _u(),
        catalogue=_catalogue(),
        as_of=date(2026, 10, 9),
    )
    assert plan.horizon.n_days == 14
    assert plan.cutoff < plan.as_of
    active = [t for t in plan.targets if t.metric_id == "net_sales"]
    assert len(active) == 1
    assert active[0].status == "validated"
    assert active[0].maturity_days == 1
    text = render_forecast_plan(plan)
    assert "net_sales" in text and "validated" in text


@pytest.mark.asyncio
async def test_compile_derived_aov():
    u = _u(
        metrics=[MetricSlot(words="aov", metric_id="aov")],
        forecast=ForecastSlots(
            targets=[MetricSlot(words="aov", metric_id="aov")],
            horizon=ForecastHorizonSlot(n=7),
        ),
    )
    plan = await compile_forecast_plan(u, catalogue=_catalogue(), as_of=date(2026, 10, 9))
    ids = {t.metric_id for t in plan.targets}
    assert "net_sales" in ids and "orders" in ids
    derived = [t for t in plan.targets if t.is_derived]
    assert len(derived) == 1 and derived[0].metric_id == "aov"


@pytest.mark.asyncio
async def test_compile_derived_mer_and_conversion_rate():
    cat = CatalogueSnapshot(
        metrics=(
            CatalogueMetricMeta(
                id="net_sales",
                label="Net sales",
                raw={"status": "certified", "date_basis": "order_date"},
            ),
            CatalogueMetricMeta(
                id="orders",
                label="Orders",
                raw={"status": "certified", "date_basis": "order_date"},
            ),
            CatalogueMetricMeta(
                id="ad_spend",
                label="Ad spend",
                raw={"status": "certified"},
            ),
            CatalogueMetricMeta(
                id="sessions",
                label="Sessions",
                raw={"status": "certified"},
            ),
        )
    )
    for mid in ("mer", "conversion_rate"):
        u = _u(
            metrics=[MetricSlot(words=mid, metric_id=mid)],
            forecast=ForecastSlots(
                targets=[MetricSlot(words=mid, metric_id=mid)],
                horizon=ForecastHorizonSlot(n=7),
            ),
        )
        plan = await compile_forecast_plan(u, catalogue=cat, as_of=date(2026, 10, 9))
        derived = [t for t in plan.targets if t.is_derived and t.metric_id == mid]
        assert len(derived) == 1, mid


@pytest.mark.asyncio
async def test_compile_provisional_unlisted_certified():
    cat = CatalogueSnapshot(
        metrics=(
            CatalogueMetricMeta(
                id="refunds",
                label="Refunds",
                raw={"status": "certified", "date_basis": "order_date", "unit": "INR"},
            ),
        )
    )
    u = _u(
        metrics=[MetricSlot(words="refunds", metric_id="refunds")],
        forecast=ForecastSlots(
            targets=[MetricSlot(words="refunds", metric_id="refunds")],
            horizon=ForecastHorizonSlot(n=14),
        ),
    )
    plan = await compile_forecast_plan(u, catalogue=cat, as_of=date(2026, 10, 9))
    active = [t for t in plan.targets if t.metric_id == "refunds"]
    assert len(active) == 1
    assert active[0].status == "provisional"
    assert "T_PROVISIONAL" in active[0].reason_codes
    assert active[0].engine == "chronos-2-small"


@pytest.mark.asyncio
async def test_compile_refuses_uncertified_unlisted():
    cat = CatalogueSnapshot(
        metrics=(
            CatalogueMetricMeta(
                id="experimental_score",
                label="Experimental",
                raw={"status": "draft"},
            ),
        )
    )
    u = _u(
        metrics=[MetricSlot(words="experimental", metric_id="experimental_score")],
        forecast=ForecastSlots(
            targets=[MetricSlot(words="experimental", metric_id="experimental_score")],
            horizon=ForecastHorizonSlot(n=7),
        ),
    )
    plan = await compile_forecast_plan(u, catalogue=cat, as_of=date(2026, 10, 9))
    active = [t for t in plan.targets if t.metric_id == "experimental_score"]
    assert len(active) == 1
    assert active[0].status == "refused"
    assert "T_CERTIFIED" in active[0].reason_codes


@pytest.mark.asyncio
async def test_compile_entity_horizon_clamp():
    cat = CatalogueSnapshot(
        metrics=(
            CatalogueMetricMeta(
                id="net_sales",
                label="Net sales",
                raw={"status": "certified", "date_basis": "order_date"},
                supported_dimensions=["lt_platform"],
            ),
        )
    )
    u = _u(
        entity_dimension="lt_platform",
        forecast=ForecastSlots(
            targets=[MetricSlot(words="net sales", metric_id="net_sales")],
            horizon=ForecastHorizonSlot(n=60, unit="day"),
        ),
    )
    plan = await compile_forecast_plan(u, catalogue=cat, as_of=date(2026, 10, 9))
    assert plan.entity_dimension == "lt_platform"
    assert plan.horizon.n_days == 30  # clamped by entity policy
    assert any("entity_horizon_clamped" in n for n in plan.notes)
