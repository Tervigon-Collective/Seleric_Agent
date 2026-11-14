# Data feasibility for hourly, entity-level monitoring

Checked 2026-11-02 ~18:15 UTC, read-only, against ClickHouse `serve.*` (the schema the Cube user can see). Windows are the last 7 or 14 days unless stated. Nothing was written or changed.

## Verdict in one paragraph

Hourly data exists and is fresh (about 45 minutes behind), but only for **delivery** (spend, impressions, clicks, landing-page views; Meta also has add-to-cart and initiate-checkout) and for **brand-level orders and sessions**. Outcomes (orders, revenue, profit, returns) are **not available hourly per ad, campaign, product or geo**: the volumes are too small for hourly statistics, and the profit and returns tables are daily. So "hourly, at entity level" is only honest for delivery metrics. Entity outcome metrics must run on daily or multi-day windows with pooling. The plan is tiered on this fact (see `00_PLAN.md`, section 4).

## What exists per grain

| Grain | Hourly source | Metrics available hourly | Finding |
|---|---|---|---|
| Brand, orders | `commerce_orders` (`order_created_at`, `order_created_at_ist`) | order count, revenue, new vs repeat, payment bucket | Brand 20: 565 sale orders in 14 days, **about 1.9 orders per hour**. Brand 24: 2 orders in 14 days. Brand 28 (Commbitz) has no orders at all (paid media only in this warehouse). |
| Brand, traffic | `fct_web_session` (`session_start`, `session_hour`) | sessions, product views, add-to-carts, checkout, purchases, channel, campaign, ad, device, geo | Brand 20: **about 108 sessions per hour**, last session within minutes of the check. |
| Meta ad / ad set / campaign | `meta_ads_hourly`, `fct_paid_media_hourly` | spend, impressions, clicks, link clicks, landing-page views, reach, 3s views, thruplays, add-to-cart, view-content, initiate-checkout | History from 2025-09-22. Active in last 7 days (spend > 0): **39 campaigns, 64 ad sets, 72 ads**. About 48.5k spend per day. Median active ad-hour: 85 impressions, **2 clicks, 0 add-to-carts**. No purchases or revenue hourly. |
| Google campaign | `google_ads_hourly`, `fct_paid_media_hourly` | spend, impressions, clicks, interactions | **Campaign level only**: 270,297 of 272,140 rows have no `ad_id`, one ad set. Brand 20: 5 active campaigns, about 26.4k spend per day. Brand 28: 4 active campaigns, about 11.5k per day. No conversions hourly. |
| Product / SKU | none hourly | (`fct_order_line` has order time via `fct_order`) | 181 products with sales in 14 days: **median 2 order lines in 14 days, p90 8, none with 5 or more per day**. Hourly is impossible; daily is thin; multi-day windows and a category hierarchy are required. |
| Geo | `fct_order`, `fct_web_session` | order and session counts | 24 states, 140 cities in 14 days of orders. Cities are far too sparse for orders; states are workable on daily windows. |
| Cohort | `fct_order` (`is_new_customer`, `person_sale_seq`) | new vs repeat counts | 399 new and 232 repeat orders in 14 days (all brands, from `commerce_orders`): usable on daily windows. |
| Profit | `ssot_pnl_daily`, `mart_pnl_event`, `mart_product_ad_profit` | none hourly | `ssot_pnl_daily` has gross and net profit per day (latest day 2026-11-02). `mart_product_ad_profit` and `mart_pnl_event` carry `campaign_id` and `ad_id`, **daily**. COGS and returns settle with a lag, so recent days are provisional. |
| Returns / RTO | `return_lifecycle`, `refund_events`, `fct_order` flags | event timestamps | Event-dated by owner rule (2026-10-21): returns and RTO on event date. Lagged by days to weeks; not an hourly signal. |

## Attribution depth (limits entity outcomes)

For brand 20 over 14 days: of 631 active orders, **45.8% carry an ad id, 72.7% a campaign id, 100% a platform**. 83 ads received any attributed order; the **median ad had 2 orders in 14 days** (p90 9, max 18). An ad-level CPA or ROAS therefore needs a multi-day window and shrinkage toward its ad set and campaign.

## Freshness and finality

* Hourly paid media: last hour 17:30 UTC when queried at 18:17 UTC, `data_as_of` 18:12. About 45 minutes of lag.
* `is_final` is 99.5 percent (Google) and 99.4 percent (Meta); the rest is the newest hours, which get revised. The detector must either score only final rows or widen its band for non-final hours.
* Orders: last order 17:35 UTC at the same check.

## Scale (how many series)

Brand 20 only, active entities: about 44 campaigns + 64 ad sets + 72 ads = about 180 paid-media nodes, times about 6 delivery series each (spend, impressions, clicks, CTR, CPC, CPM) is roughly 1,100 hourly series. Plus 181 products, 24 states, 2 cohorts, 3 payment buckets and the brand-level series on daily windows. Realistic total: **2 to 4 thousand series** now, scaling linearly with brands. "Thousands" holds, but it is not millions, so a single process with vectorised models is enough; no streaming platform is needed.

## Things to verify before building

1. Time zone. `order_created_at_ist` is typed `DateTime64(... 'UTC')`. It looks like IST wall-clock stored under a UTC label. Hour-of-day profiles must use the business time zone (IST); confirm which column is the true instant.
2. Whether `meta_ads_daily` carries platform-reported purchases and revenue (it is the only place a platform ROAS can come from; the hourly table does not).
3. Late revisions: how far back non-final hours are rewritten (affects the hour-by-hour re-scoring window).
4. Which ad-level columns can be mapped to `lt_ad_id` on orders reliably (the join key between delivery and outcomes).

## Queries used

Table and column listings from `system.tables` / `system.columns` for database `serve`; `count`, `uniqExact`, `quantileExact`, `min`/`max` over `fct_paid_media_hourly`, `meta_ads_hourly`, `commerce_orders`, `fct_order`, `fct_order_line`, `fct_web_session`. Access was the read-only `cube_serve` user. The `default` user and the mage-ai `.env` login were rejected, so no other credential was used.
