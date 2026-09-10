"""Grain-first catalogue grounding — fake bootstrap, no live MCP."""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from seleric_swarm.coordinator.catalogue_grounding import apply_catalogue_grain, constrain_hints_to_grain
from seleric_swarm.services.catalogue_bootstrap import CatalogueBootstrap, CatalogueMetricMeta
from seleric_swarm.services.metrics import lead_agent_for_hints


def _def(metric_id: str, catalogue: str, domain: str = "commerce") -> SimpleNamespace:
    return SimpleNamespace(id=metric_id, catalogue_metric=catalogue, domain=domain)


class _Registry:
    def __init__(self, defs: list[SimpleNamespace]) -> None:
        self._defs = {d.id: d for d in defs}

    def get(self, metric_id: str):
        return self._defs.get(metric_id)

    def all(self):
        return list(self._defs.values())

    def owner_agent_for(self, metric_id: str) -> str | None:
        d = self.get(metric_id)
        if not d or not d.domain:
            return None
        return f"{d.domain}_agent"

    def id_for_catalogue(self, catalogue_id: str | None) -> str | None:
        if not catalogue_id:
            return None
        for definition in self._defs.values():
            if definition.id == catalogue_id or definition.catalogue_metric == catalogue_id:
                return definition.id
        return None

    def bind_catalogue(self, bootstrap) -> None:
        pass


def _bootstrap(rows: list[dict]) -> CatalogueBootstrap:
    bs = CatalogueBootstrap(MagicMock())
    for row in rows:
        bs._cache[row["id"]] = CatalogueMetricMeta(
            id=row["id"],
            view=row.get("view") or "",
            supported_dimensions=list(row.get("supported_dimensions") or []),
            raw=dict(row.get("raw") or row),
        )
    bs._warmed_at = time.monotonic()
    return bs


_DEFS = [
    _def("metric.net_sales", "commerce_net_revenue_daily", "commerce"),
    _def("metric.orders", "orders", "commerce"),
    _def("metric.attributed_net_revenue", "attributed_net_revenue", "attribution"),
    _def("metric.sessions", "web_sessions", "funnel"),
    _def("metric.units_sold", "units_sold", "product"),
]

_CACHE = [
    {"id": "commerce_net_revenue_daily", "supported_dimensions": ["order_date"]},
    {"id": "orders", "supported_dimensions": ["order_date"]},
    {"id": "attributed_net_revenue", "supported_dimensions": ["lt_channel", "brand_id"]},
    {"id": "web_sessions", "supported_dimensions": ["channel"]},
    {"id": "units_sold", "supported_dimensions": ["product_title"]},
]


def test_no_grain_leaves_hints_unchanged():
    hints = ["metric.net_sales", "metric.orders"]
    out, dims = constrain_hints_to_grain(
        hints, query="What were net sales yesterday?", metrics=_Registry(_DEFS), bootstrap=_bootstrap(_CACHE)
    )
    assert out == hints
    assert dims == []


def test_cold_bootstrap_is_noop():
    hints = ["metric.net_sales"]
    out, dims = constrain_hints_to_grain(
        hints, query="Get me channel wise report", metrics=_Registry(_DEFS), bootstrap=None
    )
    assert out == hints
    assert dims == []


def test_grain_replaces_unsupported_commerce_pack():
    hints = ["metric.net_sales", "metric.orders"]
    out, dims = constrain_hints_to_grain(
        hints,
        query="Get me channel wise report",
        metrics=_Registry(_DEFS),
        bootstrap=_bootstrap(_CACHE),
    )
    assert out == ["metric.attributed_net_revenue"]
    assert dims == ["lt_channel"]
    assert lead_agent_for_hints(out, _Registry(_DEFS)) == "attribution_agent"


def test_grain_keeps_supporting_hint():
    hints = ["metric.attributed_net_revenue", "metric.net_sales"]
    out, dims = constrain_hints_to_grain(
        hints,
        query="attributed net revenue by channel",
        metrics=_Registry(_DEFS),
        bootstrap=_bootstrap(_CACHE),
    )
    assert out == ["metric.attributed_net_revenue"]
    assert dims == ["lt_channel"]


def test_product_grain_prefers_product_title_metrics():
    hints = ["metric.net_sales", "metric.units_sold"]
    out, dims = constrain_hints_to_grain(
        hints,
        query="product wise report",
        metrics=_Registry(_DEFS),
        bootstrap=_bootstrap(_CACHE),
    )
    assert out == ["metric.units_sold"]
    assert dims == ["product_title"]


def test_unsupported_grain_clears_hints():
    hints = ["metric.net_sales"]
    cache = [
        {"id": "commerce_net_revenue_daily", "supported_dimensions": ["order_date"]},
        {"id": "web_sessions", "supported_dimensions": ["channel"]},
    ]
    defs = [_def("metric.net_sales", "commerce_net_revenue_daily")]
    out, dims = constrain_hints_to_grain(
        hints,
        query="Get me channel wise report",
        metrics=_Registry(defs),
        bootstrap=_bootstrap(cache),
    )
    assert out == []
    assert dims == ["channel"]


def test_generic_status_word_does_not_collide_with_fulfillment_status():
    """'funnel status today' must not be read as a fulfillment_status
    breakdown just because 'status' is a substring token of the dimension id
    — regression for a real production false-positive that silently dropped
    valid evidence (grain requested but unsupported by the metric's view)."""
    hints = ["metric.sessions"]
    cache = [
        {"id": "web_sessions", "supported_dimensions": ["channel"]},
        {"id": "orders", "supported_dimensions": ["order_date", "fulfillment_status"]},
    ]
    defs = [
        _def("metric.sessions", "web_sessions", "funnel"),
        _def("metric.orders", "orders", "commerce"),
    ]
    out, dims = constrain_hints_to_grain(
        hints,
        query="funnel status today",
        metrics=_Registry(defs),
        bootstrap=_bootstrap(cache),
    )
    assert dims == []
    assert out == hints


@pytest.mark.asyncio
async def test_apply_catalogue_grain_skips_live_resolution_without_breakdown_language():
    """A plain 'X status' aggregate ask must never reach the live dimension
    resolver — regression for a production bug where the live
    catalogue_resolve_dimension capability fuzzy-matched the generic word
    'status' to nearly every *_status dimension in the catalogue (order_status,
    fulfillment_status, financial_status, ...), silently replacing the
    requested metrics with an unrelated fulfillment-status breakdown."""

    class _BoomMcp:
        capabilities = {"seleric.catalogue_resolve_dimension"}

        async def call(self, **kwargs):
            raise AssertionError("live dimension resolution must not be called without breakdown language")

    runtime = SimpleNamespace(
        bootstrap=_bootstrap(_CACHE),
        metrics=_Registry(_DEFS),
        mcp=_BoomMcp(),
    )
    hints = ["metric.sessions"]
    out, dims = await apply_catalogue_grain("funnel status on 2026-08-01", hints, runtime=runtime)
    assert out == hints
    assert dims == []


@pytest.mark.asyncio
async def test_apply_catalogue_grain_still_resolves_real_breakdown_requests():
    class _Mcp:
        capabilities = {"seleric.catalogue_resolve_dimension"}

        async def call(self, **kwargs):
            return {"kind": "resolved", "dimension_id": "lt_channel"}

    runtime = SimpleNamespace(
        bootstrap=_bootstrap(_CACHE),
        metrics=_Registry(_DEFS),
        mcp=_Mcp(),
    )
    out, dims = await apply_catalogue_grain(
        "attributed net revenue by channel", ["metric.attributed_net_revenue"], runtime=runtime
    )
    assert dims == ["lt_channel"]
    assert out == ["metric.attributed_net_revenue"]


@pytest.mark.asyncio
async def test_apply_catalogue_grain_rejects_uncorroborated_live_suggestion():
    """Regression: live catalogue_resolve_dimension returned 'item_count' for
    'top channels by sessions...' — a real breakdown request, but the wrong
    dimension entirely — which silently swapped funnel hints for unrelated
    commerce metrics. The query text itself must corroborate the live
    suggestion; an uncorroborated one should fall through to the local,
    query-grounded heuristic (which correctly finds 'channel')."""

    class _Mcp:
        capabilities = {"seleric.catalogue_resolve_dimension"}

        async def call(self, **kwargs):
            return {"kind": "resolved", "dimension_id": "item_count"}

    cache = _CACHE + [{"id": "item_count_measure", "supported_dimensions": ["item_count"]}]
    runtime = SimpleNamespace(
        bootstrap=_bootstrap(cache),
        metrics=_Registry(_DEFS),
        mcp=_Mcp(),
    )
    out, dims = await apply_catalogue_grain(
        "top channels by sessions on 2026-08-01", ["metric.sessions"], runtime=runtime
    )
    assert "item_count" not in dims
    assert dims == ["channel"]
    assert out == ["metric.sessions"]


def test_evidence_covers_grain():
    from seleric_swarm.coordinator.catalogue_grounding import evidence_covers_grain

    assert evidence_covers_grain([{"dimensions": {}}], []) is True
    assert evidence_covers_grain([{"dimensions": {}}], ["lt_channel"]) is False
    assert evidence_covers_grain(
        [{"dimensions": {"lt_channel": "meta"}}], ["lt_channel"]
    ) is True


def test_alias_index_matches_marketplace_to_channel():
    bs = _bootstrap(_CACHE)
    bs._dimension_aliases = {"channel": ["marketplace", "shopify vs amazon"]}
    out, dims = constrain_hints_to_grain(
        ["metric.net_sales"],
        query="report by marketplace",
        metrics=_Registry(_DEFS),
        bootstrap=bs,
    )
    assert dims == ["channel"]
    assert out == ["metric.sessions"]


def test_grain_defaults_metric_in_registry_wins():
    defs = _DEFS + [_def("metric.channel_orders", "channel_orders", "attribution")]
    cache = _CACHE + [{"id": "channel_orders", "supported_dimensions": ["channel"]}]
    bs = _bootstrap(cache)
    bs._grain_defaults = {"channel": {"dimension": "channel", "metric": "channel_orders"}}
    out, dims = constrain_hints_to_grain(
        ["metric.net_sales"],
        query="Get me channel wise report",
        metrics=_Registry(defs),
        bootstrap=bs,
    )
    assert dims == ["channel"]
    assert out == ["metric.channel_orders"]


@pytest.mark.asyncio
async def test_untyped_resolve_term_is_ignored():
    from seleric_swarm.coordinator.catalogue_grounding import resolve_catalogue_dimension

    class _Mcp:
        capabilities = {"seleric.catalogue_resolve_term"}

        async def call(self, *, agent_id, capability, arguments):
            assert arguments.get("kind") == "dimension"
            del agent_id, capability
            return {
                "kind": "ambiguous",
                "candidates": [{"metric_id": "channel_orders", "id": "channel_orders"}],
            }

    dims = await resolve_catalogue_dimension(
        "channel", runtime=SimpleNamespace(mcp=_Mcp())
    )
    assert dims == []


@pytest.mark.asyncio
async def test_resolve_dimension_ambiguous_returns_catalogue_ids():
    from seleric_swarm.coordinator.catalogue_grounding import resolve_catalogue_dimension

    class _Mcp:
        capabilities = {"seleric.catalogue_resolve_dimension"}

        async def call(self, *, agent_id, capability, arguments):
            del agent_id, capability, arguments
            return {
                "kind": "ambiguous",
                "candidates": [
                    {"dimension_id": "channel"},
                    {"dimension_id": "lt_channel"},
                    {"dimension_id": "acquisition_channel"},
                ],
            }

    dims = await resolve_catalogue_dimension(
        "channel", runtime=SimpleNamespace(mcp=_Mcp())
    )
    assert dims == ["channel", "lt_channel", "acquisition_channel"]


def test_bind_catalogue_does_not_hide_yaml_metric_ids():
    """Regression: binding the live catalogue (triggered by any grain/
    breakdown query) must not make ids_for_domain()/get() forget YAML
    metric.* ids for every OTHER metric — a prior full-replace in all()
    caused domain_mission_update's allowlist check to reject a metric it had
    already resolved earlier in the same mission, once any query anywhere
    triggered a live bind."""
    from seleric_swarm.services.metrics import MetricRegistry

    bs = _bootstrap(
        [{"id": "channel_orders", "supported_dimensions": ["channel"], "category": "attribution"}]
    )
    registry = MetricRegistry("config/metric_registry.yaml")
    assert "metric.sessions" in registry.ids_for_domain("funnel")

    registry.bind_catalogue(bs)

    assert "metric.sessions" in registry.ids_for_domain("funnel")
    assert registry.get("metric.sessions") is not None
    assert "channel_orders" in registry.ids_for_domain("attribution")


def test_live_catalogue_is_the_metric_repository():
    from seleric_swarm.services.metrics import MetricRegistry, lead_agent_for_hints

    bs = _bootstrap(
        [
            {
                "id": "commerce_net_revenue_daily",
                "supported_dimensions": ["report_date"],
                "category": "commerce",
            },
            {
                "id": "channel_orders",
                "supported_dimensions": ["channel"],
                "category": "attribution",
            },
            {
                "id": "web_sessions",
                "supported_dimensions": ["channel"],
                "category": "web_analytics",
            },
        ]
    )
    bs._grain_defaults = {
        "by_channel": {"dimension_id": "channel", "default_measure": "channel_orders"}
    }
    registry = MetricRegistry("config/metric_registry.yaml")
    registry.bind_catalogue(bs)

    assert registry.get("channel_orders") is not None
    assert registry.get("channel_orders").domain == "attribution"
    assert registry.id_for_catalogue("channel_orders") == "channel_orders"
    assert "channel_orders" in registry.ids_for_domain("attribution")

    out, dims = constrain_hints_to_grain(
        ["metric.net_sales"],
        query="Get me channel wise report",
        metrics=registry,
        bootstrap=bs,
        resolved_grain=["channel"],
    )
    assert dims == ["channel"]
    assert out == ["channel_orders"]
    assert lead_agent_for_hints(out, registry) == "attribution_agent"
