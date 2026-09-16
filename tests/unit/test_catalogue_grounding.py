"""Grain-first catalogue grounding — fake bootstrap, no live MCP."""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import MagicMock

import pytest

from seleric_swarm.coordinator.catalogue_grounding import (
    apply_catalogue_grain,
    constrain_hints_to_grain,
    dimensions_in_query,
)
from seleric_swarm.services.catalogue_bootstrap import CatalogueBootstrap, CatalogueMetricMeta
from seleric_swarm.services.metrics import lead_agent_for_hints


def _def(metric_id: str, catalogue: str, domain: str = "commerce") -> SimpleNamespace:
    return SimpleNamespace(id=metric_id, catalogue_metric=catalogue, domain=domain, aliases=[])


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


def test_sku_list_drops_pnl_metric_that_cannot_slice():
    hints = ["metric.units_sold", "metric.net_profit"]
    defs = [
        *_DEFS,
        _def("metric.net_profit", "net_profit_all_channels", "finance"),
    ]
    cache = [
        *_CACHE,
        {"id": "units_sold", "supported_dimensions": ["product_title", "sku"]},
        {"id": "net_profit_all_channels", "supported_dimensions": ["brand_id", "report_date"]},
    ]
    out, dims = constrain_hints_to_grain(
        hints,
        query="Give me the list of SKUs sold in the last 30 days",
        metrics=_Registry(defs),
        bootstrap=_bootstrap(cache),
    )
    assert dims == ["sku"]
    assert out == ["metric.units_sold"]
    assert "metric.net_profit" not in out


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


def test_grain_keeps_assigned_metrics_that_cannot_slice():
    hints = ["metric.net_sales", "metric.orders"]
    out, dims = constrain_hints_to_grain(
        hints,
        query="Get me channel wise report",
        metrics=_Registry(_DEFS),
        bootstrap=_bootstrap(_CACHE),
    )
    assert out == hints
    assert dims == ["lt_channel"]
    assert lead_agent_for_hints(out, _Registry(_DEFS)) == "commerce_agent"


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


def test_unsupported_grain_keeps_assigned_identity():
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
    assert out == ["metric.net_sales"]
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
async def test_apply_catalogue_grain_ignores_live_dims_the_hinted_metric_cannot_slice():
    """Live resolve is always consulted — but 'funnel status' matching
    fulfillment_status must not replace sessions with an orders breakdown.
    Grounding is 'does the asked metric support this dim?', not a keyword gate.
    """

    class _Mcp:
        capabilities: ClassVar[set[str]] = {"seleric.catalogue_resolve_dimension"}

        async def call(self, **kwargs):
            return {
                "kind": "ambiguous",
                "candidates": [
                    {"dimension_id": "fulfillment_status"},
                    {"dimension_id": "order_status"},
                    {"dimension_id": "financial_status"},
                ],
            }

    runtime = SimpleNamespace(
        bootstrap=_bootstrap(_CACHE),
        metrics=_Registry(_DEFS),
        mcp=_Mcp(),
    )
    hints = ["metric.sessions"]
    out, dims = await apply_catalogue_grain("funnel status on 2026-08-01", hints, runtime=runtime)
    assert out == hints
    assert dims == []


@pytest.mark.asyncio
async def test_apply_catalogue_grain_still_resolves_real_breakdown_requests():
    class _Mcp:
        capabilities: ClassVar[set[str]] = {"seleric.catalogue_resolve_dimension"}

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
        capabilities: ClassVar[set[str]] = {"seleric.catalogue_resolve_dimension"}

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


@pytest.mark.asyncio
async def test_ambiguous_product_question_uses_live_dim_supported_by_units_sold():
    """'How are products doing' has no breakdown language. Entity lists are
    not grain — do not slice, and do not drop the P&L metric.
    """

    class _Mcp:
        capabilities = {"seleric.catalogue_resolve_dimension"}

        async def call(self, **kwargs):
            return {
                "kind": "ambiguous",
                "candidates": [
                    {"dimension_id": "product_id", "confidence": 0.6},
                    {"dimension_id": "item_count", "confidence": 0.4},
                ],
            }

    defs = [
        *_DEFS,
        _def("metric.net_profit", "net_profit_all_channels", "finance"),
    ]
    cache = [
        *_CACHE,
        {"id": "units_sold", "supported_dimensions": ["product_title", "sku", "product_id"]},
        {"id": "net_profit_all_channels", "supported_dimensions": ["brand_id", "report_date"]},
        {"id": "item_count_measure", "supported_dimensions": ["item_count"]},
    ]
    runtime = SimpleNamespace(
        bootstrap=_bootstrap(cache),
        metrics=_Registry(defs),
        mcp=_Mcp(),
    )
    out, dims = await apply_catalogue_grain(
        "How are products doing?",
        ["metric.units_sold", "metric.net_profit"],
        runtime=runtime,
        entities=["product"],
    )
    assert dims == []
    assert out == ["metric.units_sold", "metric.net_profit"]


@pytest.mark.asyncio
async def test_product_wise_question_filters_to_slicable_metric():
    class _Mcp:
        capabilities = {"seleric.catalogue_resolve_dimension"}

        async def call(self, **kwargs):
            return {
                "kind": "ambiguous",
                "candidates": [
                    {"dimension_id": "product_id", "confidence": 0.6},
                    {"dimension_id": "item_count", "confidence": 0.4},
                ],
            }

    defs = [
        *_DEFS,
        _def("metric.net_profit", "net_profit_all_channels", "finance"),
    ]
    cache = [
        *_CACHE,
        {"id": "units_sold", "supported_dimensions": ["product_title", "sku", "product_id"]},
        {"id": "net_profit_all_channels", "supported_dimensions": ["brand_id", "report_date"]},
        {"id": "item_count_measure", "supported_dimensions": ["item_count"]},
    ]
    runtime = SimpleNamespace(
        bootstrap=_bootstrap(cache),
        metrics=_Registry(defs),
        mcp=_Mcp(),
    )
    out, dims = await apply_catalogue_grain(
        "How are products doing product-wise?",
        ["metric.units_sold", "metric.net_profit"],
        runtime=runtime,
        entities=["product"],
    )
    assert dims == ["product_id"]
    assert out == ["metric.units_sold"]


@pytest.mark.asyncio
async def test_ambiguous_sku_vs_seller_sku_keeps_hinted_metric_dim():
    class _Mcp:
        capabilities = {"seleric.catalogue_resolve_dimension"}

        async def call(self, **kwargs):
            return {
                "kind": "ambiguous",
                "candidates": [
                    {"dimension_id": "sku", "confidence": 1.0},
                    {"dimension_id": "seller_sku", "confidence": 1.0},
                ],
            }

    cache = [
        *_CACHE,
        {"id": "units_sold", "supported_dimensions": ["product_title", "sku"]},
    ]
    runtime = SimpleNamespace(
        bootstrap=_bootstrap(cache),
        metrics=_Registry(_DEFS),
        mcp=_Mcp(),
    )
    out, dims = await apply_catalogue_grain(
        "What moved by SKU?",
        ["metric.units_sold"],
        runtime=runtime,
        entities=["sku"],
    )
    assert dims == ["sku"]
    assert out == ["metric.units_sold"]


def test_evidence_covers_grain():
    from seleric_swarm.coordinator.catalogue_grounding import evidence_covers_grain

    assert evidence_covers_grain([{"dimensions": {}}], []) is True
    assert evidence_covers_grain([{"dimensions": {}}], ["lt_channel"]) is False
    assert evidence_covers_grain(
        [{"dimensions": {"lt_channel": "meta"}}], ["lt_channel"]
    ) is True
    # Claim gate must use Observer-requested dims. Leftover state grain on an
    # aggregate retrieve is not a coverage failure.
    assert evidence_covers_grain([{"dimensions": {}}], []) is True


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
    assert out == ["metric.net_sales"]


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
    assert out == ["metric.net_sales"]


@pytest.mark.asyncio
async def test_untyped_resolve_term_is_ignored():
    from seleric_swarm.coordinator.catalogue_grounding import resolve_catalogue_dimension

    class _Mcp:
        capabilities: ClassVar[set[str]] = {"seleric.catalogue_resolve_term"}

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
        capabilities: ClassVar[set[str]] = {"seleric.catalogue_resolve_dimension"}

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


@pytest.mark.asyncio
async def test_hints_from_catalogue_falls_back_to_resolve_term_for_single_strong_token():
    """Regression: 'roas' vs catalogue id 'net_roas_all_channels' overlaps on
    only one token and isn't the whole id, so the search-based scorer finds
    nothing. The glossary-backed catalogue_resolve_term should still resolve
    it instead of the classifier returning 'no registered metric matches'."""
    from seleric_swarm.coordinator.catalogue_grounding import hints_from_catalogue

    class _Mcp:
        capabilities: ClassVar[set[str]] = {
            "seleric.catalogue_search_metrics",
            "seleric.catalogue_resolve_term",
        }

        async def call(self, *, agent_id, capability, arguments):
            del agent_id
            if capability == "seleric.catalogue_search_metrics":
                return {"matches": []}
            assert capability == "seleric.catalogue_resolve_term"
            if arguments["text"] == "roas":
                return {"kind": "resolved", "metric_id": "net_roas_all_channels", "confidence": 1.0}
            return {"kind": "unknown", "suggestions": []}

    defs = [_def("net_roas_all_channels", "net_roas_all_channels", "performance")]
    runtime = SimpleNamespace(mcp=_Mcp(), metrics=_Registry(defs))
    hints = await hints_from_catalogue("how much roas has increased over the last 3 day?", runtime=runtime)
    assert hints == ["net_roas_all_channels"]


@pytest.mark.asyncio
async def test_hints_from_catalogue_ignores_unresolved_term():
    from seleric_swarm.coordinator.catalogue_grounding import hints_from_catalogue

    class _Mcp:
        capabilities: ClassVar[set[str]] = {
            "seleric.catalogue_search_metrics",
            "seleric.catalogue_resolve_term",
        }

        async def call(self, *, agent_id, capability, arguments):
            del agent_id, arguments
            if capability == "seleric.catalogue_search_metrics":
                return {"matches": []}
            return {"kind": "unknown", "suggestions": []}

    runtime = SimpleNamespace(mcp=_Mcp(), metrics=_Registry(_DEFS))
    hints = await hints_from_catalogue("gibberish query", runtime=runtime)
    assert hints == []


@pytest.mark.asyncio
async def test_hints_from_catalogue_collapses_cadence_siblings():
    from seleric_swarm.coordinator.catalogue_grounding import hints_from_catalogue

    class _Mcp:
        capabilities = {"seleric.catalogue_search_metrics", "seleric.catalogue_resolve_term"}

        async def call(self, *, agent_id, capability, arguments):
            del agent_id, arguments
            if capability == "seleric.catalogue_resolve_term":
                return {"kind": "unknown", "suggestions": []}
            return {
                "matches": [
                    {"id": "meta_ctr", "display_name": "Meta CTR"},
                    {"id": "meta_ctr_hourly", "display_name": "Meta CTR hourly"},
                ]
            }

    defs = [
        _def("meta_ctr", "meta_ctr", "performance"),
        _def("meta_ctr_hourly", "meta_ctr_hourly", "performance"),
    ]
    runtime = SimpleNamespace(
        mcp=_Mcp(),
        metrics=_Registry(defs),
        bootstrap=_bootstrap(
            [
                {
                    "id": "meta_ctr",
                    "view": "meta_ad_performance",
                    "raw": {"grain": "daily"},
                },
                {
                    "id": "meta_ctr_hourly",
                    "view": "meta_ad_performance_hourly",
                    "raw": {"grain": "hourly"},
                },
            ]
        ),
    )
    hints = await hints_from_catalogue("What is the meta CTR last 7 days?", runtime=runtime)
    assert hints == ["meta_ctr"]


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
    assert out == ["metric.net_sales"]
    assert lead_agent_for_hints(out, registry) == "commerce_agent"


def test_collapse_assigned_metrics_drops_hourly_sibling():
    from seleric_swarm.coordinator.catalogue_grounding import collapse_assigned_metrics

    class _Metrics:
        def canonical_id(self, metric_id: str) -> str:
            return {"metric.ctr": "meta_ctr"}.get(metric_id, metric_id)

    bs = _bootstrap(
        [
            {
                "id": "meta_ctr",
                "view": "meta_ad_performance",
                "raw": {"grain": "daily ad grain"},
            },
            {
                "id": "meta_ctr_hourly",
                "view": "meta_ad_performance_hourly",
                "raw": {"grain": "hourly ad grain"},
            },
        ]
    )
    collapsed = collapse_assigned_metrics(
        ["metric.ctr", "meta_ctr", "meta_ctr_hourly"],
        _Metrics(),
        "What is the meta CTR last 7 days?",
        bootstrap=bs,
    )
    assert collapsed == ["meta_ctr"]


def test_collapse_assigned_metrics_picks_intraday_when_asked():
    from seleric_swarm.coordinator.catalogue_grounding import collapse_assigned_metrics

    class _Metrics:
        def canonical_id(self, metric_id: str) -> str:
            return metric_id

    bs = _bootstrap(
        [
            {
                "id": "meta_ctr",
                "view": "meta_ad_performance",
                "raw": {"grain": "daily"},
            },
            {
                "id": "meta_ctr_hourly",
                "view": "meta_ad_performance_hourly",
                "raw": {"grain": "hourly"},
            },
        ]
    )
    assert collapse_assigned_metrics(
        ["meta_ctr", "meta_ctr_hourly"],
        _Metrics(),
        "hourly meta CTR",
        bootstrap=bs,
    ) == ["meta_ctr_hourly"]


def test_collapse_assigned_metrics_keeps_conjunction_across_concepts():
    from seleric_swarm.coordinator.catalogue_grounding import collapse_assigned_metrics

    class _Metrics:
        def canonical_id(self, metric_id: str) -> str:
            return metric_id

        def get(self, metric_id: str):
            return SimpleNamespace(catalogue_metric=metric_id, id=metric_id)

    bs = _bootstrap(
        [
            {"id": "gross_sales", "view": "commerce_daily", "raw": {"grain": "daily"}},
            {
                "id": "gross_sales_hourly",
                "view": "commerce_hourly",
                "raw": {"grain": "hourly"},
            },
            {
                "id": "commerce_net_revenue_daily",
                "view": "commerce_daily",
                "raw": {"grain": "daily"},
            },
            {
                "id": "commerce_net_revenue_hourly",
                "view": "commerce_hourly",
                "raw": {"grain": "hourly"},
            },
        ]
    )
    assert collapse_assigned_metrics(
        [
            "gross_sales",
            "gross_sales_hourly",
            "commerce_net_revenue_daily",
            "commerce_net_revenue_hourly",
        ],
        _Metrics(),
        "What is gross sale and net sale for today",
        bootstrap=bs,
    ) == ["gross_sales", "commerce_net_revenue_daily"]


@pytest.mark.asyncio
async def test_apply_catalogue_grain_skips_campaign_objective_on_aggregate_ctr():
    class _Mcp:
        capabilities = {"seleric.catalogue_resolve_dimension"}

        async def call(self, **kwargs):
            return {"kind": "resolved", "dimension_id": "campaign_objective"}

    defs = [
        *_DEFS,
        _def("meta_ctr", "meta_ctr", "performance"),
        _def("meta_ctr_hourly", "meta_ctr_hourly", "performance"),
    ]
    cache = [
        *_CACHE,
        {"id": "meta_ctr", "supported_dimensions": ["date"]},
        {
            "id": "meta_ctr_hourly",
            "supported_dimensions": ["date", "campaign_objective"],
        },
    ]
    runtime = SimpleNamespace(
        bootstrap=_bootstrap(cache),
        metrics=_Registry(defs),
        mcp=_Mcp(),
    )
    out, dims = await apply_catalogue_grain(
        "What is the meta CTR last 7 days?",
        ["metric.ctr", "meta_ctr", "meta_ctr_hourly"],
        runtime=runtime,
        entities=["campaign_objective"],
    )
    assert dims == []
    assert out == ["metric.ctr", "meta_ctr", "meta_ctr_hourly"]


def test_per_day_does_not_match_session_day_of_week():
    """Bug: "get per day data" single-token-matched "day" against
    session_day_of_week (tokens: session/day/week), a funnel-only dimension
    net_sales can't slice by -- same hallucination class as status ->
    fulfillment_status. Calendar words are generic now, like "status"."""
    hits = dimensions_in_query(
        "why did sales drop from 5 days, get per day data",
        {"session_day_of_week", "brand_id", "report_date"},
    )
    assert "session_day_of_week" not in hits


def test_real_multi_word_dimension_still_matches():
    """The fix must not blanket-suppress every multi-word dimension --
    only single generic-calendar-word overlaps."""
    hits = dimensions_in_query("break down by product title", {"product_title", "brand_id"})
    assert "product_title" in hits


def test_hourly_live_metric_inherits_yaml_ctr_unit():
    from seleric_swarm.services.metrics import MetricRegistry

    bs = _bootstrap(
        [
            {
                "id": "meta_ctr_hourly",
                "supported_dimensions": ["campaign_objective"],
                "category": "paid_media",
            }
        ]
    )
    registry = MetricRegistry("config/metric_registry.yaml")
    registry.bind_catalogue(bs)
    hourly = registry.get("meta_ctr_hourly")
    assert hourly is not None
    assert hourly.unit == "ratio"
