"""A small world with a known answer for the engine-operator tests: spend feeds visits feed orders, sales are orders
times average order value, and on the last day spend is cut while one region carries the whole fall. Metric ids are
generic (``m_*``) so no test can lean on a name."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np

from seleric_swarm.diagnosis.domain.inputs import DiagnosisInput
from seleric_swarm.diagnosis.domain.lineage import MetricMeta

N = 57
DAYS = [date(2026, 8, 8) + timedelta(days=i) for i in range(N)]
EVENT = [DAYS[-1]]


def _s(values: np.ndarray) -> dict[date, float]:
    return {d: float(v) for d, v in zip(DAYS, values, strict=True)}


def engine_input(*, seed: int = 5, shocks: int = 3, drivers: tuple[str, ...] = ("m_spend", "m_noise")) -> DiagnosisInput:
    rng = np.random.default_rng(seed)
    spend = 1000 + 100 * rng.normal(size=N)
    for k in range(shocks):  # on/off switches in the history: intervention-like evidence for direction
        a = 10 + 15 * k
        spend[a : a + 4] = 0.0
    spend[-1] = 400.0
    visits = 2000 + 1.5 * spend + rng.normal(0, 60, N)
    orders = np.maximum(5, 0.02 * visits + rng.normal(0, 3, N))
    aov = 2000 + rng.normal(0, 50, N)
    sales = orders * aov
    north_share = np.clip(0.6 + rng.normal(0, 0.01, N), 0.5, 0.7)
    north, south = sales * north_share, sales * (1 - north_share)
    south[-1] = south[-8:-1].mean()  # the south holds its normal level, so the north carries the whole fall
    north[-1] = sales[-1] - south[-1]
    lineage = {
        "m_sales": MetricMeta("m_sales", "additive", "orders_view", (), unit="USD", grain="brand_order_day"),
        "m_orders": MetricMeta("m_orders", "additive", "orders_view", (), grain="brand_order_day"),
        "m_aov": MetricMeta("m_aov", "ratio", "orders_view", ("m_sales", "m_orders"), grain="brand_order_day"),
        "m_spend": MetricMeta("m_spend", "additive", "ads_view", (), grain="brand_ad_day"),
        "m_visits": MetricMeta("m_visits", "additive", "web_view", (), grain="brand_session_day"),
        "m_noise": MetricMeta("m_noise", "additive", "other_view", (), grain="brand_other_day"),
    }
    series = {
        "m_sales": _s(sales), "m_orders": _s(orders), "m_aov": _s(aov), "m_spend": _s(spend), "m_visits": _s(visits),
        "m_noise": _s(rng.normal(0, 1, N)),
    }
    return DiagnosisInput(
        outcome="m_sales", event_days=list(EVENT), series=series, lineage=lineage,
        segments={"m_sales": {"d_region": {"north": _s(north), "south": _s(south)}}},
        candidate_drivers=list(drivers), claimed_direction="down", scope_tokens=frozenset({"brand"}),
    )
