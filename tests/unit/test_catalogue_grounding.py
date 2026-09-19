"""catalogue_grounding.py — surviving (non-heuristic) surface only.

2026-09-18: the free-text query-vs-dimension-name matching heuristic
(``dimensions_in_query``/``_GENERIC_DIM_TOKENS``), the catalogue-search/
alias metric guesser (``hints_from_catalogue``), and their grain-grounding
wrapper (``apply_catalogue_grain``/``ground_live_grain``/
``constrain_hints_to_grain``/``preferred_grain``/``resolve_grain_texts``)
were deleted from ``catalogue_grounding.py`` — the bug #8-class heuristic
this migration retires (Sprint 3 Profile B override,
docs/refactor/TASK_SHEET.md). Every test that exercised those functions is
removed with them, not adapted to a function that no longer exists. Bug
#8's regression proof now lives against the new path in
``tests/unit/test_semantic_toolset_bug_regressions.py``.

What remains here: ``evidence_covers_grain``/``resolve_catalogue_dimension``
(structural checks / live-resolver calls, not local keyword matching) and
``collapse_assigned_metrics`` (catalogue-metadata-based cadence collapsing,
still used by the live swarm_v2 classifier, `coordinator/intake/
llm_classifier.py`).
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import MagicMock

import pytest

from seleric_swarm.services.catalogue_bootstrap import CatalogueBootstrap, CatalogueMetricMeta


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


def test_live_catalogue_is_the_metric_repository():
    from seleric_swarm.services.metrics import MetricRegistry

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
    registry = MetricRegistry("config/metric_registry.yaml")
    registry.bind_catalogue(bs)

    assert registry.get("channel_orders") is not None
    assert registry.get("channel_orders").domain == "attribution"
    assert registry.id_for_catalogue("channel_orders") == "channel_orders"
    assert "channel_orders" in registry.ids_for_domain("attribution")


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
