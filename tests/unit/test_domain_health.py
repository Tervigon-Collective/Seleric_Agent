"""Sprint 3/4 checklist: running the resolver once produces a valid snapshot
per domain with correct status rollup and headline signals, readable back
via SnapshotStore.get_latest -- see
docs/features/business-state-service/05_SPRINT_PLAN.md Sprint 3/4.
"""

import pytest

from seleric_swarm.contracts.lookup import TimeRangeV1
from seleric_swarm.domain.models import FeatureValue, MetricState
from seleric_swarm.services.domain_health.resolver import DomainHealthProfiles, DomainStateResolver
from seleric_swarm.services.domain_health.scheduler import ALL_DOMAINS, run_once
from seleric_swarm.services.domain_health.snapshot_store import SnapshotStore

TIME_RANGE = TimeRangeV1(kind="absolute", start="2026-09-01", end="2026-09-07")


def _state(metric_id: str, value: float, period_delta_pct: float | None = None, status: str = "OK") -> MetricState:
    features = {}
    if period_delta_pct is not None:
        features["period_delta_pct"] = FeatureValue(value=period_delta_pct)
    return MetricState(
        metric_id=metric_id,
        catalogue_metric_id=metric_id,
        as_of="2026-09-07",
        actual=value,
        features=features,
        freshness="CURRENT",
        status=status,
        provenance={"query_id": f"q-{metric_id}"},
    )


class _FakeBusinessState:
    def __init__(self, states: dict[str, MetricState]):
        self._states = states
        self.requests = []

    async def get_metric_state(self, request):
        self.requests.append(request)
        return self._states[request.metric_id]


COMMERCE_STATES = {
    "metric.net_sales": _state("metric.net_sales", 71727.93, period_delta_pct=-18.0),
    "metric.gross_sales": _state("metric.gross_sales", 90000.0, period_delta_pct=2.0),
    "metric.orders": _state("metric.orders", 120.0, period_delta_pct=-5.0),
    "metric.returns_cancels": _state("metric.returns_cancels", 5.0),
}


@pytest.mark.asyncio
async def test_resolve_commerce_snapshot_flags_net_sales_drop():
    business_state = _FakeBusinessState(COMMERCE_STATES)
    resolver = DomainStateResolver(business_state)

    snapshot = await resolver.resolve("commerce", time_range=TIME_RANGE)

    assert snapshot.domain == "commerce"
    assert snapshot.brand_id == "20"
    assert snapshot.status == "OK"
    assert {m.metric_id for m in snapshot.metrics} == set(COMMERCE_STATES)
    assert snapshot.headline_signals == [
        "net_sales_drop: metric.net_sales period_delta_pct -18.0% below threshold -15.0%"
    ]
    # -18.0 (percent-scale, not a 0-1 fraction) breaches the -15 threshold.
    # order_volume_drop threshold is -20%; -5% doesn't breach it.
    assert not any("order_volume_drop" in s for s in snapshot.headline_signals)
    assert all(r.agent_id == "commerce_agent" for r in business_state.requests)


@pytest.mark.asyncio
async def test_resolve_degrades_status_on_partial_metric():
    states = dict(COMMERCE_STATES)
    states["metric.orders"] = _state("metric.orders", 120.0, period_delta_pct=-0.05, status="PARTIAL")
    resolver = DomainStateResolver(_FakeBusinessState(states))

    snapshot = await resolver.resolve("commerce", time_range=TIME_RANGE)

    assert snapshot.status == "DEGRADED"


@pytest.mark.asyncio
async def test_snapshot_round_trips_through_store(tmp_path):
    resolver = DomainStateResolver(_FakeBusinessState(COMMERCE_STATES))
    snapshot = await resolver.resolve("commerce", time_range=TIME_RANGE)

    store = SnapshotStore(base_dir=tmp_path)
    store.save(snapshot)

    loaded = store.get_latest("commerce")
    assert loaded == snapshot


def test_snapshot_store_returns_none_when_empty(tmp_path):
    store = SnapshotStore(base_dir=tmp_path)
    assert store.get_latest("commerce") is None


ALL_METRIC_STATES = {
    **COMMERCE_STATES,
    "metric.net_profit": _state("metric.net_profit", 12000.0, period_delta_pct=-2.0),
    "metric.gross_margin_pct": _state("metric.gross_margin_pct", 42.0),
    "metric.mer": _state("metric.mer", 3.1, period_delta_pct=1.0),
    "metric.rto_cost": _state("metric.rto_cost", 900.0),
    "metric.spend": _state("metric.spend", 20000.0, period_delta_pct=5.0),
    "metric.net_roas": _state("metric.net_roas", 2.4, period_delta_pct=-3.0),
    "metric.cac": _state("metric.cac", 300.0),
    "metric.cpm": _state("metric.cpm", 150.0),
    "metric.attributed_net_revenue": _state("metric.attributed_net_revenue", 50000.0, period_delta_pct=-1.0),
    "metric.sessions": _state("metric.sessions", 4000.0, period_delta_pct=-10.0),
    "metric.purchase_cvr": _state("metric.purchase_cvr", 1.8),
    "metric.checkout_rate": _state("metric.checkout_rate", 60.0),
    "metric.product_net_revenue": _state("metric.product_net_revenue", 30000.0, period_delta_pct=1.5),
    "metric.product_gross_margin_pct": _state("metric.product_gross_margin_pct", 38.0),
    "metric.repeat_rate": _state("metric.repeat_rate", 22.0),
    "metric.refunded_amount_excl_tax": _state("metric.refunded_amount_excl_tax", 4000.0, period_delta_pct=40.0),
}


@pytest.mark.parametrize("domain", ALL_DOMAINS)
@pytest.mark.asyncio
async def test_resolve_every_domain_produces_a_snapshot(domain):
    """Sprint 4: resolver.py is unchanged per-domain -- it's generic over
    config/domain_health_profiles.yaml for all 8 buildable domains."""
    resolver = DomainStateResolver(_FakeBusinessState(ALL_METRIC_STATES))

    snapshot = await resolver.resolve(domain, time_range=TIME_RANGE)

    profile = DomainHealthProfiles().get(domain)
    assert snapshot.domain == domain
    assert {m.metric_id for m in snapshot.metrics} == {e["metric_id"] for e in profile["metrics"]}
    assert snapshot.status in {"OK", "DEGRADED", "UNAVAILABLE"}


@pytest.mark.asyncio
async def test_refund_spike_uses_above_threshold_rule():
    resolver = DomainStateResolver(_FakeBusinessState(ALL_METRIC_STATES))

    snapshot = await resolver.resolve("operations", time_range=TIME_RANGE)

    assert snapshot.headline_signals == [
        "refund_spike: metric.refunded_amount_excl_tax period_delta_pct 40.0% above threshold 30.0%"
    ]


@pytest.mark.asyncio
async def test_windowed_point_metric_computes_snapshot_over_snapshot_delta(tmp_path):
    """repeat_rate (customer) has no report_date axis -- period_delta_pct
    must come from comparing this run's value to the previous snapshot's,
    not from BusinessStateService features (06_DATA_VALIDATION_FINDINGS.md#4).
    """
    store = SnapshotStore(base_dir=tmp_path)
    resolver = DomainStateResolver(_FakeBusinessState(ALL_METRIC_STATES))

    first = await resolver.resolve("customer", time_range=TIME_RANGE, store=store)
    store.save(first)
    assert first.metrics[0].period_delta_pct is None  # no prior snapshot yet

    states_v2 = dict(ALL_METRIC_STATES)
    states_v2["metric.repeat_rate"] = _state("metric.repeat_rate", 24.2)
    resolver_v2 = DomainStateResolver(_FakeBusinessState(states_v2))
    second = await resolver_v2.resolve("customer", time_range=TIME_RANGE, store=store)

    assert second.metrics[0].value == 24.2
    assert second.metrics[0].period_delta_pct == pytest.approx((24.2 - 22.0) / 22.0 * 100)
    assert second.metrics[0].rolling_mean_7d is None
    # windowed_point metrics never request BusinessStateService "features".
    assert all("features" not in r.need for r in resolver_v2._business_state.requests if r.metric_id == "metric.repeat_rate")


@pytest.mark.asyncio
async def test_scheduler_run_once_resolves_and_saves_every_domain(tmp_path):
    store = SnapshotStore(base_dir=tmp_path)
    resolver = DomainStateResolver(_FakeBusinessState(ALL_METRIC_STATES))

    snapshots = await run_once(resolver, store, time_range=TIME_RANGE)

    assert {s.domain for s in snapshots} == set(ALL_DOMAINS)
    for domain in ALL_DOMAINS:
        assert store.get_latest(domain) is not None
