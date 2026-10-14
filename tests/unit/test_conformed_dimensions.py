"""Conformed dimensions, grain-twin routing and structured filters (2026-10-08).

Live 2026-10-07 (thread_590ab450…, "Which Meta campaigns performed best in the last 7 days? Rank them
using spend, revenue, ROAS, CAC, CTR, CPC, CPM, clicks, LPVs, and purchases"): the Meta scope went out as
ad_platform on orders / net_sales / page_views and as finance_channel on ad_spend / ctr / clicks, seven
ModelRetry bounces came back in one step, and the answer shipped with net ROAS only. The catalogue now
declares dimension families and grain twins; the tools answer through them instead of bouncing.

Fixtures use sample ids; the logic under test reads only the catalogue snapshot.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta, CatalogueSnapshot

SEP = {"period_start": datetime(2026, 9, 1, tzinfo=UTC), "period_end": datetime(2026, 9, 30, tzinfo=UTC)}

PNL_DIMS = ["brand_id", "order_date", "finance_channel", "ad_platform", "campaign_name"]
SNAP = CatalogueSnapshot(
    metrics=(
        CatalogueMetricMeta(id="net_profit", supported_dimensions=PNL_DIMS, raw={"unit": "INR"}),
        CatalogueMetricMeta(id="orders", supported_dimensions=["brand_id", "order_date", "platform", "finance_channel",
                                                               "ad_platform", "campaign_name"],
                            raw={"unit": "count", "grain_twins": ["product_orders"]}),
        CatalogueMetricMeta(id="product_orders", supported_dimensions=["brand_id", "order_date", "product_title"]),
        CatalogueMetricMeta(id="cac", supported_dimensions=["brand_id", "report_date"],
                            raw={"unit": "INR", "grain_twins": ["channel_cac"]}),
        CatalogueMetricMeta(id="channel_cac", supported_dimensions=PNL_DIMS, raw={"unit": "INR"}),
        CatalogueMetricMeta(id="repeat_rate", supported_dimensions=["brand_id", "last_order_at", "acquisition_platform"]),
        CatalogueMetricMeta(id="ad_spend", supported_dimensions=["brand_id", "report_date", "ad_platform",
                                                                 "campaign_name"], raw={"unit": "INR"}),
        CatalogueMetricMeta(id="sessions", supported_dimensions=["brand_id", "session_date", "device_type"]),
    ),
    time_dimensions=frozenset({"order_date", "report_date", "session_date", "last_order_at"}),
    dimension_families=(("platform", "platform", 0), ("finance_channel", "platform", 1), ("ad_platform", "platform", 2),
                        ("acquisition_platform", "platform", 3), ("campaign_name", "campaign", 0)),
    dimension_values=(("finance_channel", ("meta", "google", "whatsapp", "organic", "unattributed")),
                      ("ad_platform", ("meta", "google"))),
)


class RecordingMcp:
    """Answers metrics_query with one row per call: the measure plus every requested dimension = 'meta'."""

    def __init__(self, currency: str = "INR") -> None:
        self.calls: list[dict[str, Any]] = []
        self.currency = currency

    async def call(self, *, agent_id: str, capability: str, arguments: dict[str, Any]) -> Any:
        self.calls.append(arguments)
        row = {arguments["measures"][0]: 1234.5678, **{d: "meta" for d in arguments.get("dimensions") or []}}
        return {"query_id": "q1", "rows": [row], "provenance": {"query_id": "q1", "currency": self.currency}}


def _filters(call: dict[str, Any]) -> list[dict[str, Any]]:
    return [f for f in call.get("filters") or [] if f["dimension"] != "brand_id"]


















def test_carries_follows_siblings_and_twins():
    assert SNAP.carries("net_profit", "platform")        # sibling finance_channel
    assert SNAP.carries("cac", "campaign_name")           # twin channel_cac
    assert SNAP.carries("orders", "product_title")        # twin product_orders
    assert SNAP.carries("net_profit", "report_date")      # time is the grain everywhere
    assert not SNAP.carries("sessions", "product_title")  # nothing carries it








def _spend_sub_channel_snap() -> CatalogueSnapshot:
    sub = ["brand_id", "order_date", "sub_channel", "finance_channel"]
    return replace(
        SNAP,
        metrics=(
            CatalogueMetricMeta(
                id="ad_spend",
                supported_dimensions=["brand_id", "report_date", "ad_platform", "campaign_name"],
                view="paid_media",
                raw={"unit": "INR"},
            ),
            CatalogueMetricMeta(id="channel_cac", supported_dimensions=sub, raw={"unit": "INR"}),
            CatalogueMetricMeta(id="cost_per_order", supported_dimensions=sub, raw={"unit": "INR"}),
            CatalogueMetricMeta(id="mer", supported_dimensions=sub, raw={"unit": "ratio"}),
            CatalogueMetricMeta(id="event_count", supported_dimensions=["brand_id", "event_date", "sub_channel"]),
            CatalogueMetricMeta(
                id="event_add_to_carts", supported_dimensions=["brand_id", "event_date", "sub_channel"]
            ),
            *(m for m in SNAP.metrics if m.id not in {"ad_spend", "channel_cac"}),
        ),
    )


def test_ad_spend_sub_channel_alternatives_prefer_pnl_companions():
    """Live 2026-10-08: ModelRetry for ad_spend×sub_channel listed unrelated event_*
    metrics first. Prefer channel_cac / cost_per_order / mer over event noise."""
    snap = _spend_sub_channel_snap()
    alts = snap.metrics_supporting_dimension("sub_channel", like="ad_spend")
    assert "channel_cac" in alts
    assert "cost_per_order" in alts or "mer" in alts
    assert alts.index("channel_cac") < alts.index("event_count")


