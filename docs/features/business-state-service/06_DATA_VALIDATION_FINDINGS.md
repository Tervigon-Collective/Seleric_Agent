# 06 — Data validation findings (live `seleric-mcp` check, 2026-09-14)

**Status:** point-in-time validation record, not a living design doc. Referenced
from [04](04_DOMAIN_HEALTH_SNAPSHOTS.md) and [03](03_DEFINITIONS_TO_MAKE_FUNCTIONAL.md).

Purpose: before committing to the domain metric lists in 04, checked each
proposed metric against the real catalogue (`modules_list`,
`catalogue_search_metrics`, `catalogue_get_metric`) and ran three live
`metrics_query` calls to see actual field shapes. Four findings changed the
design; the rest confirmed it as written.

## Method

- `modules_list` — confirmed the 8 buildable modules (commerce, finance,
  paidmedia, attribution, customer, operations, product, webanalytics) and
  their metric counts (27, 33, 65, 19, 16, 5, 11, 28 = 204 catalogue metrics
  total).
- `catalogue_search_metrics` per domain — checked every metric named in
  04's tables actually exists, and captured the real catalogue id where the
  doc had used a placeholder description.
- `catalogue_get_metric("commerce_net_revenue_daily")` — checked the full
  field shape (freshness, grain, dimensions, access policy).
- Three live `metrics_query` calls — `commerce_net_revenue_daily` (daily
  series), `repeat_rate` (to test whether it supports daily grain), and
  `["refund_count", "orders"]` together (to test cross-view composition).

## Findings that changed the design

1. **Freshness is already solved, not an open design problem.**
   `provenance.freshness = {source, expected_cadence, cube_last_refresh}` is
   returned on every `metrics_query` call today, live. `MetricState.freshness`
   is a threshold classification of that field, not new plumbing. This
   *removes* risk from 03 §3/§9, it doesn't add scope.

2. **`brand_id` is a real dimension backed by real data, and 04's original
   "single brand today" framing was wrong — but the fix is a scope decision,
   not a mandate.** Nearly every metric's `supported_dimensions` includes
   `brand_id`; an unscoped query silently defaults to `brand_id=20` with a
   warning; and `catalogue_list_brands` confirms **5 active brands**
   (20 Tilting Heads, 26 Sniff Theory, 25 Urthend, 27 Mannmore, 24 The Billy
   Company). **Decision (2026-09-14):** pin `brand_id="20"` for all V0
   sprints, pass it explicitly on every call (never rely on the silent
   default — that's an implicit fallback, not a documented choice), keep
   `brand_id` in the `DomainStateSnapshot` schema so multi-brand rollout
   later is a config loop, not a migration.

3. **"Refund rate" and "return/cancellation rate" aren't catalogue metrics —
   and computing them crosses date axes.** Only counts/amounts exist
   (`refund_count`, `refunded_amount_excl_tax`, `cancelled_orders`,
   `returns_cancels`). A live composed query
   (`metrics_query(["refund_count", "orders"])`) came back with the two
   metrics on **different date axes** (`refund_date` vs `order_date`) and an
   explicit tool-level warning not to join or sum across the parts. This is
   a real, unresolved design gap (03 §3, new `CROSS_AXIS_RATIO_UNSUPPORTED`
   flag), not a rounding detail — any domain "rate" health signal built by
   dividing two separately-fetched metrics needs this check before it's
   safe to compute.

4. **`repeat_rate` has no daily grain — it's a windowed point value, not a
   time series.** `supported_dimensions: [brand_id]` only; a live query
   still accepted a `time_range` but returned **one row** for the whole
   window (the window became an implicit filter on `customer_ltv.last_order_at`,
   not a `GROUP BY` axis). `period_delta_pct` / `rolling_mean_7d` as
   currently scoped (03 §4, daily-series features) don't apply to metrics
   shaped like this — needs a second feature class ("windowed_point":
   compare this cron run's value to the previous cron run's value) before
   the customer domain snapshot can trend it.

## Findings that confirmed the design as-is

- Commerce core metrics (`commerce_net_revenue_daily`, `gross_sales`,
  `orders`) are daily, single-axis, exactly as assumed — live query
  returned a clean 7-day series.
- Finance ratio metrics (`gross_margin_pct`, `mer`, `net_roas_all_channels`,
  `net_profit_all_channels`) exist as pre-built ratios on `canonical_pnl` —
  no cross-axis risk, they're computed server-side already.
- Funnel conversion/checkout-stage rates (`funnel_conversion_rate`,
  `session_conversion_rate`, `session_checkout_rate`,
  `session_checkout_to_purchase_rate`) all exist as pre-built daily ratios —
  funnel domain needs no client-side division.
- Attribution (`attributed_net_revenue`, `channel_orders`) and Product
  (`product_net_revenue`, `product_gross_margin_pct`, dimensionable by
  `sku`) both exist as designed.
- **Performance domain is genuinely cross-module**: `total_ad_spend`,
  `net_roas`, `cac` are NOT in the `paidmedia` module (confirmed by a
  `paidmedia`-scoped search returning zero matches for them) — they live in
  `finance`'s `canonical_pnl`, matching `metric_registry.yaml`'s
  `seleric_module: null` annotation for `metric.spend`/`metric.cac`. Only
  CPM/CTR/CPC-style delivery metrics are actually `paidmedia`-scoped. A
  `DomainStateResolver` for performance cannot be pinned to one MCP module
  allowlist.

## Not re-validated (lower risk, deferred to implementation time)

- Inventory/procurement/technical — already known unbuildable (no module),
  not re-checked live since `modules_list` confirms only 8 modules exist.
- Exact `min_points` / lookback windows per metric — needs golden fixture
  work at Sprint 0/1, not a catalogue lookup.
- Hourly ad-spend gold tables mentioned in 04's open questions — not
  re-queried here; see `[[hourly-grain-timezone-and-ad-spend]]` memory for
  prior finding (exists in gold, not yet exposed in the cube).
