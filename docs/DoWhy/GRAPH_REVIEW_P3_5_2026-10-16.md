# Dependency graph review for P3.5 (O-4)

Generated 2026-10-16 from the vendored graph `causal/data/dependency_graph.json` (digest `3116740b8d70`, 233 nodes, 464 edges) and the Phase 3 candidate builder. Nothing here changes the graph: edits are made in Agent Core, by you. Evidence references are to `docs/DoWhy/REBUILD_PLAN_2026-10-15.md` §11 (session 5, entries 10-12).

## How the engine uses the graph now

For a why-question about metric M the engine (1) builds M's **accounting tree** (composition, ratio, declared-input edges: the arithmetic that makes M); (2) walks **mechanism** edges (and **feedback** at lag >= 1 day) backwards from M and every part of M, up to 8 edges; (3) offers at most 8 of the metrics reached, most upstream tier first; (4) never offers a twin, co-measure, subset or allocation of M or its parts, nor a metric joined to what it feeds only by a `structural` edge; (5) compares each **co-measure** pair of M and its parts on the event days (recorded, not yet acting). So an **edge you add or remove changes which causes can be named**. Metrics with no incoming mechanism edge can never be named as a cause of anything they feed.

## 1. Numbers

| | |
|---|---|
| nodes | 233 (certified 225, approved 5, broken 3) |
| nodes on a mechanism or feedback edge | 38 |
| nodes that, as the outcome, get **no candidate cause** | 161 (96 have no incoming edge at all, 52 are arithmetic over parts with no mechanism parent, 12 are allocations, 1 other) |
| catalogue metrics of the eval brand with candidates | 65 of 79 |

## 2. Questions for you (each from something the eval or the data showed)

1. **`site_searches` has no direct edge to `orders`.** The eval plants `site_searches -> orders` as an *off-graph* cause. The graph reaches it only through `site_searches -> session_product_views -> add_to_carts -> orders` (moderate, lag 0). A drop in searches shows up as a cause of net sales only through that chain, and the path screen then requires `session_product_views` and `add_to_carts` to have moved too. Is the chain the right model, or should searches also act on `orders` directly?

2. **`sessions` has only paid-media parents.** Its mechanism parents are `landing_page_views` and `link_clicks`, both ad metrics. A fall in organic, direct, email or referral traffic has no node to be blamed on, and a recording loss on `sessions` has no co-measure pair (eval case `adv_break_sessions`: the engine names `link_clicks` and `ad_spend` for a pure tracking loss). Do you want non-paid traffic sources as nodes, and a declared relation (co-measure or subset) between `sessions` and `landing_page_views`/`link_clicks` that the engine can compare?

3. **`net_sales` vs `order_pnl_net_sales` (co-measure) and the P&L feed.** On the eval brand the pair's ratio is 1.13 with a 7% spread, and on injected drops in `net_sales` the P&L partner moved far less (-16% vs -46%..-63%). Does the P&L feed lag the latest days in production? If so the pair should carry a tolerance or a lag note before MEASUREMENT is allowed to act. The same applies to `pnl_*` twins (ratio 0.83-0.97, spread 15-34%).

4. **`customers` has no edge to `orders` except through `new_customers`.** In the eval data `customers` moves in lockstep with `orders` (the old engine excluded it on that evidence). The graph has `orders -> new_customers -> customers` (structural). Is `customers` meant to be a cause of anything? Today it can only be a downstream effect.

5. **The three levers have no reviewed effect on anything but spend and impressions.** `budget_changes`, `status_changes` -> `ad_spend`; `bid_strategy_changes` -> `impressions`. Edits to creatives, audiences, prices, stock and incidents have no node (Phase 4's evidence ports would add them). Which of these should be graph nodes before Phase 4?

6. **`structural` edges are treated as near-identities.** `orders -> units_ordered -> gross_sales`, `orders -> shipping_cost`, `orders -> new_customers` and others (49 such exclusions across the 233 outcomes) are never offered as a cause of what they feed. If any `structural` edge is a real business prior rather than a definition (for example `orders -> shipping_cost`), say so and the engine will offer it.

7. **Only 3 feedback edges.** `net_sales -> ad_spend` (1-14 d), `orders -> ad_spend` (1-7 d), `orders -> cpm` (1-7 d, negative). They make `net_sales` and `orders` candidate causes of `sessions` and `ctr` through ad spend. Legitimate by the graph's own rule, but weak. Keep, or tighten the lag?

8. **`discounts` is a co-measure of `order_pnl_discounts` and a mechanism parent of `orders` and `net_aov`.** As the cause of a `net_roas` change it is set aside as a co-measure of the outcome's part. Is that intended, or should the discount lever be separable from the booked discount amount?

9. **Status of key nodes.** `link_clicks`, `thruplays`, `hook_rate`, `hold_rate_15s`, `cost_per_link_click` are *approved*, not certified; `checkout_steps`, `avg_seconds_to_checkout`, `avg_seconds_to_purchase` are *broken*. `link_clicks` sits on the main paid-traffic path, so every ad-driven explanation passes through an uncertified node.

10. **Coverage: 161 nodes give no candidate cause.** Section 4 lists them by view. For any of these you expect users to ask about, an incoming mechanism edge is what is missing (product-level and P&L metrics are the bulk: their cause is the total, or a feed, not a business mechanism). Which would you add first?

## 3. Mechanism and feedback edges (every one, for review)

| source | tier | -> target | tier | kind | confidence | sign | lag (days) |
|---|---|---|---|---|---|---|---|
| bid_strategy_changes | 0 intervention | impressions | 2 delivery | mechanism | moderate | +/- | 0..3 |
| budget_changes | 0 intervention | ad_spend | 1 spend | mechanism | strong | +/- | 0..1 |
| status_changes | 0 intervention | ad_spend | 1 spend | mechanism | strong | +/- | 0..1 |
| ad_spend | 1 spend | impressions | 2 delivery | mechanism | strong | + | 0..0 |
| discounts | 1 spend | net_aov | 7 order_value | mechanism | strong | - | 0..0 |
| discounts | 1 spend | orders | 6 purchase | mechanism | moderate | + | 0..0 |
| impressions | 2 delivery | clicks | 3 ad_engagement | mechanism | strong | + | 0..0 |
| impressions | 2 delivery | link_clicks | 3 ad_engagement | mechanism | strong | + | 0..0 |
| impressions | 2 delivery | thruplays | 3 ad_engagement | mechanism | strong | + | 0..0 |
| landing_page_views | 3 ad_engagement | sessions | 4 site_traffic | mechanism | strong | + | 0..0 |
| link_clicks | 3 ad_engagement | landing_page_views | 3 ad_engagement | mechanism | strong | + | 0..0 |
| link_clicks | 3 ad_engagement | sessions | 4 site_traffic | mechanism | strong | + | 0..0 |
| sessions | 4 site_traffic | orders | 6 purchase | mechanism | strong | + | 0..1 |
| sessions | 4 site_traffic | session_product_views | 5 on_site_intent | mechanism | strong | + | 0..0 |
| add_to_carts | 5 on_site_intent | funnel_purchases | 6 purchase | mechanism | strong | + | 0..1 |
| add_to_carts | 5 on_site_intent | orders | 6 purchase | mechanism | strong | + | 0..1 |
| session_collection_views | 5 on_site_intent | session_product_views | 5 on_site_intent | mechanism | moderate | + | 0..0 |
| session_product_views | 5 on_site_intent | add_to_carts | 5 on_site_intent | mechanism | strong | + | 0..0 |
| site_searches | 5 on_site_intent | session_product_views | 5 on_site_intent | mechanism | moderate | + | 0..0 |
| cod_orders | 6 purchase | cancelled_orders | 8 post_purchase | mechanism | strong | + | 0..3 |
| cod_orders | 6 purchase | rto_cost | 9 cost | mechanism | strong | + | 3..15 |
| new_customers | 6 purchase | customers | 6 purchase | mechanism | structural | + | 0..0 |
| new_customers | 6 purchase | repeat_customers | 6 purchase | mechanism | strong | + | 14..180 |
| orders | 6 purchase | ad_spend | 1 spend | feedback | moderate | + | 1..7 |
| orders | 6 purchase | cancelled_orders | 8 post_purchase | mechanism | structural | + | 0..3 |
| orders | 6 purchase | cod_orders | 6 purchase | mechanism | structural | + | 0..0 |
| orders | 6 purchase | cpm | 2 delivery | feedback | moderate | - | 1..7 |
| orders | 6 purchase | new_customers | 6 purchase | mechanism | structural | + | 0..0 |
| orders | 6 purchase | packaging_cost | 9 cost | mechanism | structural | + | 0..0 |
| orders | 6 purchase | prepaid_orders | 6 purchase | mechanism | structural | + | 0..0 |
| orders | 6 purchase | returned_orders | 8 post_purchase | mechanism | structural | + | 3..30 |
| orders | 6 purchase | shipping_cost | 9 cost | mechanism | structural | + | 0..0 |
| orders | 6 purchase | units_ordered | 6 purchase | mechanism | structural | + | 0..0 |
| prepaid_orders | 6 purchase | payment_gateway_fees | 9 cost | mechanism | structural | + | 0..0 |
| units_ordered | 6 purchase | gross_sales | 7 order_value | mechanism | structural | + | 0..0 |
| units_ordered | 6 purchase | returned_units | 8 post_purchase | mechanism | structural | + | 3..30 |
| cancelled_orders | 8 post_purchase | cancel_revenue | 8 post_purchase | mechanism | structural | + | 0..0 |
| net_sales | 8 post_purchase | ad_spend | 1 spend | feedback | strong | + | 1..14 |
| returned_orders | 8 post_purchase | return_revenue | 8 post_purchase | mechanism | structural | + | 0..0 |
| units_sold | 8 post_purchase | product_cost | 9 cost | mechanism | structural | + | 0..0 |

## 4. Metrics with no candidate cause, by view

Reason: *no incoming edge* = nothing in the graph can move it; *arithmetic* = it is built from parts, none of which has a mechanism parent; *allocated* = a modelled share of another metric.

**attribution** (2): `orders_with_touchpoints` (no incoming edge), `touches` (no incoming edge)

**attribution_paths** (1): `avg_touch_count` (arithmetic, parts have no mechanism parent)

**commerce** (10): `discounts` (no incoming edge), `discounts_incl_tax` (no incoming edge), `net_sales_deductions` (no incoming edge), `order_value_incl_tax` (no incoming edge), `paid_sales` (no incoming edge), `payment_orders` (no incoming edge), `refund_amount` (no incoming edge), `return_cancel_revenue` (no incoming edge), `returned_or_cancelled_orders` (no incoming edge), `total_sales` (arithmetic, parts have no mechanism parent)

**funnel_orders** (2): `funnel_order_purchases` (no incoming edge), `funnel_order_revenue` (no incoming edge)

**order_pnl** (14): `channel_new_customers` (no incoming edge), `channel_orders` (no incoming edge), `channel_returned_orders` (no incoming edge), `gross_cogs` (no incoming edge), `gross_profit` (arithmetic, parts have no mechanism parent), `order_pnl_ad_spend` (no incoming edge), `order_pnl_cancel_revenue` (no incoming edge), `order_pnl_discounts` (no incoming edge), `order_pnl_gross_sales` (no incoming edge), `order_pnl_net_sales` (arithmetic, parts have no mechanism parent), `order_pnl_return_revenue` (no incoming edge), `product_cost` (same-quantity routes only), `return_rate` (arithmetic, parts have no mechanism parent), `taxes_on_net_sales` (no incoming edge)

**paid_media_changes** (3): `bid_strategy_changes` (no incoming edge), `budget_changes` (no incoming edge), `status_changes` (no incoming edge)

**payments** (1): `payment_amount` (no incoming edge)

**pnl** (23): `pnl_ad_spend` (no incoming edge), `pnl_be_roas` (arithmetic, parts have no mechanism parent), `pnl_cancel_revenue` (no incoming edge), `pnl_cancelled_orders` (no incoming edge), `pnl_contribution_margin` (arithmetic, parts have no mechanism parent), `pnl_contribution_margin_pct` (arithmetic, parts have no mechanism parent), `pnl_discounts` (no incoming edge), `pnl_gross_sales` (no incoming edge), `pnl_net_cogs` (arithmetic, parts have no mechanism parent), `pnl_net_margin_pct` (arithmetic, parts have no mechanism parent), `pnl_net_profit` (arithmetic, parts have no mechanism parent), `pnl_net_sales` (arithmetic, parts have no mechanism parent), `pnl_operating_cost` (arithmetic, parts have no mechanism parent), `pnl_packaging_cost` (no incoming edge), `pnl_payment_gateway_fees` (no incoming edge), `pnl_product_cost` (no incoming edge), `pnl_return_cancel_revenue` (arithmetic, parts have no mechanism parent), `pnl_return_revenue` (no incoming edge), `pnl_returned_or_cancelled_orders` (no incoming edge), `pnl_returned_orders` (no incoming edge), `pnl_rto_cost` (no incoming edge), `pnl_shipping_cost` (no incoming edge), `pnl_taxes_on_net_sales` (no incoming edge)

**product** (28): `product_ad_spend` (allocated), `product_cancel_revenue` (no incoming edge), `product_clicks` (allocated), `product_cost_per_landing_page_view` (arithmetic, parts have no mechanism parent), `product_cost_per_link_click` (arithmetic, parts have no mechanism parent), `product_cpc` (arithmetic, parts have no mechanism parent), `product_cpm` (arithmetic, parts have no mechanism parent), `product_ctr` (arithmetic, parts have no mechanism parent), `product_discounts` (no incoming edge), `product_gross_cogs` (no incoming edge), `product_gross_margin_pct` (arithmetic, parts have no mechanism parent), `product_gross_profit` (arithmetic, parts have no mechanism parent), `product_gross_roas` (arithmetic, parts have no mechanism parent), `product_gross_sale` (no incoming edge), `product_hold_rate_15s` (arithmetic, parts have no mechanism parent), `product_hook_rate` (arithmetic, parts have no mechanism parent), `product_impressions` (allocated), `product_landing_page_views` (allocated), `product_line_cost` (no incoming edge), `product_link_clicks` (allocated), `product_mer` (arithmetic, parts have no mechanism parent), `product_net_cogs` (no incoming edge), `product_net_revenue` (arithmetic, parts have no mechanism parent), `product_net_roas` (arithmetic, parts have no mechanism parent), `product_orders` (no incoming edge), `product_return_revenue` (no incoming edge), `product_thruplays` (allocated), `product_video_completion_rate` (arithmetic, parts have no mechanism parent)

**product_pnl** (51): `product_be_roas` (arithmetic, parts have no mechanism parent), `product_cac` (arithmetic, parts have no mechanism parent), `product_contribution_margin` (arithmetic, parts have no mechanism parent), `product_contribution_margin_pct` (arithmetic, parts have no mechanism parent), `product_cost_per_order` (arithmetic, parts have no mechanism parent), `product_ltv_cac_ratio` (arithmetic, parts have no mechanism parent), `product_net_margin_pct` (arithmetic, parts have no mechanism parent), `product_net_profit` (arithmetic, parts have no mechanism parent), `product_new_customers` (no incoming edge), `product_operating_cost` (arithmetic, parts have no mechanism parent), `product_order_ad_spend` (allocated), `product_order_cancel_revenue` (no incoming edge), `product_order_discounts` (no incoming edge), `product_order_gross_sales` (no incoming edge), `product_order_net_cogs` (arithmetic, parts have no mechanism parent), `product_order_net_sales` (arithmetic, parts have no mechanism parent), `product_order_orders` (no incoming edge), `product_order_product_cost` (no incoming edge), `product_order_return_revenue` (no incoming edge), `product_order_returned_orders` (no incoming edge), `product_packaging_cost` (allocated), `product_payment_gateway_fees` (allocated), `product_pnl_ad_spend` (allocated), `product_pnl_be_roas` (arithmetic, parts have no mechanism parent), `product_pnl_cancel_revenue` (no incoming edge), `product_pnl_cancelled_orders` (no incoming edge), `product_pnl_contribution_margin` (arithmetic, parts have no mechanism parent), `product_pnl_contribution_margin_pct` (arithmetic, parts have no mechanism parent), `product_pnl_discounts` (no incoming edge), `product_pnl_gross_sales` (no incoming edge), `product_pnl_mer` (arithmetic, parts have no mechanism parent), `product_pnl_net_cogs` (arithmetic, parts have no mechanism parent), `product_pnl_net_margin_pct` (arithmetic, parts have no mechanism parent), `product_pnl_net_profit` (arithmetic, parts have no mechanism parent), `product_pnl_net_roas` (arithmetic, parts have no mechanism parent), `product_pnl_net_sales` (arithmetic, parts have no mechanism parent), `product_pnl_operating_cost` (arithmetic, parts have no mechanism parent), `product_pnl_packaging_cost` (no incoming edge), `product_pnl_payment_gateway_fees` (no incoming edge), `product_pnl_product_cost` (no incoming edge), `product_pnl_return_cancel_revenue` (arithmetic, parts have no mechanism parent), `product_pnl_return_revenue` (no incoming edge), `product_pnl_returned_or_cancelled_orders` (no incoming edge), `product_pnl_returned_orders` (no incoming edge), `product_pnl_rto_cost` (no incoming edge), `product_pnl_shipping_cost` (no incoming edge), `product_pnl_taxes_on_net_sales` (no incoming edge), `product_return_rate` (arithmetic, parts have no mechanism parent), `product_rto_cost` (allocated), `product_shipping_cost` (allocated), `product_taxes_on_net_sales` (no incoming edge)

**returns** (7): `product_refunded_amount_excl_tax` (no incoming edge), `product_returns_excl_tax` (no incoming edge), `refund_count` (no incoming edge), `refund_lines` (no incoming edge), `refunded_amount_excl_tax` (no incoming edge), `return_recovered_cogs` (no incoming edge), `returns_excl_tax` (no incoming edge)

**web_event_detail** (7): `event_add_to_carts` (no incoming edge), `event_collection_views` (no incoming edge), `event_count` (no incoming edge), `event_page_views` (no incoming edge), `event_product_views` (no incoming edge), `event_site_searches` (no incoming edge), `events_per_session` (arithmetic, parts have no mechanism parent)

**web_events** (6): `add_to_cart_events` (no incoming edge), `collection_view_events` (no incoming edge), `page_views` (no incoming edge), `product_view_events` (no incoming edge), `site_search_events` (no incoming edge), `web_events` (no incoming edge)

**web_funnel** (1): `funnel_revenue` (no incoming edge)

**web_sessions** (5): `checkout_steps` (no incoming edge), `remove_from_carts` (no incoming edge), `session_collection_views` (no incoming edge), `session_page_views` (no incoming edge), `site_searches` (no incoming edge)

## 5. Same-quantity pairs the engine compares (co-measure)

| metric | counted again as | expected |
|---|---|---|
| orders | funnel_purchases | funnel ≤ orders (ad blockers, app orders, server-side gaps) |
| orders | funnel_order_purchases |  |
| net_sales | funnel_revenue | Snowplow revenue is ex-tax at purchase; net sales also nets later returns / cancels |
| net_sales | funnel_order_revenue |  |
| ad_spend | pnl_ad_spend | equal |
| ad_spend | order_pnl_ad_spend | equal |
| net_sales | order_pnl_net_sales | near-equal, both order date |
| gross_sales | order_pnl_gross_sales | near-equal, both order date |
| discounts | order_pnl_discounts | near-equal, both order date |
| return_revenue | order_pnl_return_revenue | near-equal, both order date |
| cancel_revenue | order_pnl_cancel_revenue | near-equal, both order date |
| product_cost | product_line_cost | equal in total (catalogue: product-grain twin) |
| gross_cogs | product_gross_cogs | equal in total |
| gross_sales | product_gross_sale | equal in total (catalogue: product-grain twin) |
| product_gross_sale | product_order_gross_sales | near-equal, both order date |
| product_net_revenue | product_order_net_sales | near-equal, both order date |
| product_orders | product_order_orders |  |
| product_line_cost | product_order_product_cost |  |
| product_discounts | product_order_discounts |  |
| product_return_revenue | product_order_return_revenue |  |
| product_cancel_revenue | product_order_cancel_revenue |  |
| product_net_cogs | product_order_net_cogs |  |
| product_ad_spend | product_order_ad_spend | equal |
| returns_excl_tax | pnl_return_revenue | both on the return / refund date; near-equal |
| refund_count | pnl_returned_orders |  |
| payment_amount | paid_sales | different dates (transaction vs order) and refunds negative |
| page_views | session_page_views | session_page_views ≈1.3% below page_views |

## 6. How to answer

Reply with the number of each question and the decision (change the graph in Core / leave / need data first). Graph edits go through Agent Core and a separate push; after a Core change I re-vendor the file, re-run the 18-case suite and the Q4 check, and record the effect in §11.
