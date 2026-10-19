# AG-08 Core coordination notes

Agent does **not** modify `Seleric_Agent_Core`, Cube, or Mage in this assignment.

## Missing semantic capability

Several session-funnel **rates** in `catalogue_v2` declare no `ratio_components`:

- `add_to_cart_rate`
- `add_to_cart_to_checkout_rate`
- (and related session conversion rates)

Agent AG08-03 therefore:

1. Prefers additive stage **counts** when planning a funnel.
2. When only a rate is available and Core provides `ratio_components`, recomputes from numerator/denominator.
3. Marks a rate without components as a disclosure (`ratio_without_components`) rather than inventing a share.

**Ask for Core:** add `ratio_components: {numerator, denominator}` on session funnel rates so stage conversions can always be recomputed from certified components.

## Cross-view campaign join

`ad_spend` (view `paid_media`, grain `ad_day`) and `net_profit` (view `order_pnl`, grain `brand_order_day_channel_ad`) are different views. Agent ranks them **separately** and only joins on stable campaign/ad id + matching date basis when Core declares a validated aggregation relationship. Display-name joins are refused.
