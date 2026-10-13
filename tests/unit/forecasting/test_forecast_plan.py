"""Forecast plan compiler."""

from __future__ import annotations

from datetime import date

import pytest

from seleric_swarm.agent.forecast_plan import compile_forecast_plan, render_forecast_plan
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


@pytest.mark.asyncio
async def test_compile_net_sales_validated():
    plan = await compile_forecast_plan(
        ["net_sales"],
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
    plan = await compile_forecast_plan(["aov"], catalogue=_catalogue(), as_of=date(2026, 10, 9), horizon_days=7)
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
        plan = await compile_forecast_plan([mid], catalogue=cat, as_of=date(2026, 10, 9), horizon_days=7)
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
    plan = await compile_forecast_plan(["refunds"], catalogue=cat, as_of=date(2026, 10, 9))
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
    plan = await compile_forecast_plan(
        ["experimental_score"], catalogue=cat, as_of=date(2026, 10, 9), horizon_days=7
    )
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
    plan = await compile_forecast_plan(
        ["net_sales"], catalogue=cat, as_of=date(2026, 10, 9), horizon_days=60, entity_dimension="lt_platform"
    )
    assert plan.entity_dimension == "lt_platform"
    assert plan.horizon.n_days == 30  # clamped by entity policy
    assert any("entity_horizon_clamped" in n for n in plan.notes)
