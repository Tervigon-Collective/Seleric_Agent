"""The catalogue is the only metric registry: direction_bad and the owning domain come from the MCP catalogue entry, and a
domain health profile entry can name a fixed slice (catalogue metric + filters) without any second metric list."""

import pytest

from seleric_swarm.contracts.lookup import TimeRangeV1
from seleric_swarm.domain.models import StateRequest
from seleric_swarm.services.business_state.facade import BusinessStateService
from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta
from seleric_swarm.services.metrics import MetricRegistry


class _Catalogue:
    def __init__(self, *entries: CatalogueMetricMeta) -> None:
        self._entries = entries

    def is_warm(self) -> bool:
        return True

    def entries(self):
        return list(self._entries)


def _registry() -> MetricRegistry:
    r = MetricRegistry()
    r.bind_catalogue(
        _Catalogue(
            CatalogueMetricMeta(id="ad_spend", label="Ad spend", raw={"category": "paid_media", "unit": "currency"}),
            CatalogueMetricMeta(
                id="net_roas", label="Net ROAS", raw={"category": "finance", "agent_domain": "performance", "direction_bad": "down"}
            ),
        )
    )
    return r


def test_domain_and_direction_come_from_the_catalogue_entry():
    r = _registry()
    assert r.get("ad_spend").domain == "performance" and r.get("ad_spend").direction_bad == "up"
    assert r.get("net_roas").domain == "performance"  # agent_domain beats the finance category
    assert r.get("net_roas").direction_bad == "down"
    assert r.owner_agent_for("net_roas") == "performance_agent"
    assert r.get("metric.net_roas") is None  # no second spelling


def test_an_unbound_registry_is_empty():
    assert MetricRegistry().all() == []


class _Gateway:
    capabilities = frozenset({"seleric.metrics_query", "seleric.catalogue_get_metric"})

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def call(self, *, agent_id, capability, arguments):
        self.calls.append(arguments)
        return {"rows": [], "provenance": {}}


@pytest.mark.asyncio
async def test_a_profile_slice_sends_its_filters_and_axis_to_the_catalogue_metric():
    from types import SimpleNamespace

    gateway = _Gateway()
    runtime = SimpleNamespace(metrics=_registry(), mcp=gateway)
    service = BusinessStateService(runtime)
    request = StateRequest(
        metric_id="meta_spend",
        catalogue_metric_id="ad_spend",
        filters={"ad_platform": "meta"},
        date_axis="event_date",
        time_range=TimeRangeV1(kind="absolute", start="2026-09-01", end="2026-09-07"),
        agent_id="performance_agent",
    )
    state = await service.get_metric_state(request)
    sent = next(c for c in gateway.calls if "measures" in c)
    assert sent["measures"] == ["ad_spend"] and sent["date_axis"] == "event_date"
    assert {"dimension": "ad_platform", "operator": "equals", "values": ["meta"]} in sent["filters"]
    assert state.metric_id == "meta_spend" and state.catalogue_metric_id == "ad_spend"


@pytest.mark.asyncio
async def test_a_state_request_warms_the_catalogue_first():
    """Cron and API entry points have no mission to warm the catalogue for them: the registry is empty until it is."""
    from types import SimpleNamespace

    registry = MetricRegistry()
    warmed = []

    class _Bootstrap:
        async def refresh_if_stale(self) -> None:
            warmed.append(True)
            registry.bind_catalogue(_Catalogue(CatalogueMetricMeta(id="net_sales", label="Net sales", raw={"category": "commerce"})))

    runtime = SimpleNamespace(metrics=registry, mcp=_Gateway(), bootstrap=_Bootstrap())
    state = await BusinessStateService(runtime).get_metric_state(
        StateRequest(
            metric_id="net_sales",
            time_range=TimeRangeV1(kind="absolute", start="2026-09-01", end="2026-09-07"),
            agent_id="commerce_agent",
        )
    )
    assert warmed and "CATALOGUE_MISS" not in state.quality_flags
