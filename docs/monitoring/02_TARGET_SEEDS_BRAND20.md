# Target seeds for brand 20 (history-derived, adjustable)

> **Updated 2026-11-03:** the loaded targets, now on the semantic layer's `ssot_*` metrics with ratios re-seeded on pooled 7-day windows, are in `04_V1_PROGRESS.md` and in Postgres (`monitor.targets_current`). The volume values below are unchanged; the ratio rows (MER, CAC, CVR, returns %) in this file are the older daily-ratio proposals and are superseded.

Computed 2026-11-03, read-only, from `serve.ssot_pnl_daily` (Shopify + Amazon summed) and `serve.funnel_daily` (CVR = converted_sessions / sessions). These are **proposals to be edited**, not decisions. The owner's instruction (2026-11-03): targets only, seeded from the historical data, adjustable.

## Which days were used, and why

57 days (2026-09-07 to 2026-11-02) were read. 32 were used. Excluded by rule, and each exclusion is a finding in itself:

| Days | Why excluded | What the data shows |
|---|---|---|
| 2026-09-14 to 2026-09-28 | Regime break: ads were off or recovering | Meta and Google spend are both **0 on 09-15 to 09-22** (Meta 0 until 09-23, Google 0 on several later days). Orders were 11-25 a day on 09-15 to 09-20 (vs 46-88 on 09-07 to 09-13) and sessions fell from about 3,500 to 600-1,400 over the same days. This looks like ads being off, not a missing ingest, because orders and sessions fell with it. **The cause is not known; owner to confirm.** |
| 2026-09-28, 2026-10-13, 2026-10-15, 2026-10-30 | Bulk "closed returns" lump | `returns_closed` is 1,077,233 on 09-28 against 67,302 of gross sales (net sales -1.01M); 110,034 on 10-15; 59,236 on 10-13; 62,000 on 10-30. Rule used: `returns_closed / gross_sales > 25%` of the day. Returns are on event date (owner rule 2026-10-21), so a mass closure lands on one day. |

Remaining clean window: 2026-09-29 to 2026-11-02, minus the three lump days inside it (10-13, 10-15, 10-30) = **32 days**. Percentiles on 32 days are rough.

## Proposed values

"Better" is the good direction. Floor / target / ceiling are read per metric: for a higher-is-better metric the floor is the alarm line and the ceiling is an "unusually high, check the data" flag; for a lower-is-better metric (CAC, returns) the ceiling is the alarm line and the floor is the "suspiciously low" flag; ad spend is bad on both sides.

| Metric | Better | p10 (floor) | p50 (target) | p90 (ceiling) | Median 10-27 to 11-02 (6 clean days; 10-30 excluded) |
|---|---|---|---|---|---|
| Orders per day | higher | 31 | 50 | 74 | 68 |
| Gross sales per day | higher | 57,146 | 93,724 | 139,874 | 133,494 |
| Net sales per day | higher | 50,572 | 87,187 | 127,936 | 122,912 |
| Gross profit (contribution) per day | higher | 30,425 | 51,499 | 75,835 | 73,879 |
| Gross margin % (gross profit / net sales) | higher | 51.5 | 56.9 | 60.7 | 58.8 |
| Ad spend per day | both sides | 42,732 | 53,057 | 79,807 | 79,499 |
| MER (net sales / ad spend) | higher | 1.13 | 1.52 | 2.05 | 1.80 |
| CAC (ad spend / new customers) | lower | 1,569 | 2,250 | 3,013 | 2,043 |
| CVR % (converted / sessions) | higher | 0.66 | 1.12 | 1.92 | 2.01 |
| Closed returns % of gross sales | lower | 0.18 | 2.36 | 8.07 | 4.02 |
| Net profit per day | higher | -15,951 | -7,131 | 10,414 | 2,408 |
| Net margin % (net profit / net sales) | higher | -31.3 | -8.2 | 9.5 | 2.2 |

## Two things the history cannot seed honestly

1. **Net profit and net margin.** The median of the clean days is **negative** (-7,131 a day, -8.2%). Using it as the target would record "lose money" as the goal. Proposed instead: floor = the p10 (a loss limit), **target = break-even (0)**, ceiling = p90, and the owner sets the real number.
2. **Platform ROAS.** It is in the default list but is not seeded: the hourly Meta table has no purchase value, and I have not confirmed that `meta_ads_daily` carries it.

## Caveats on these numbers

* The last 7 days run well above the median (orders 68 vs 50, net sales 123k vs 87k). A sale or festival ramp may be under way; if so, a median target is easy to meet this week and the owner may want a higher target for it.
* Ratio metrics (MER, CAC, CVR, returns %) are percentiles of **daily** ratios, which are noisy (CAC p10-p90 is 1,569-3,013). The plan evaluates ratios on a trailing 7-day pooled value, which is tighter, so the bands above are too wide for that. First step of the build: re-seed ratios from rolling 7-day pooled values.
* Recent days are provisional for profit, net sales and returns (COGS and returns settle late). 
* Net sales and gross profit here are the `ssot_pnl_daily` definitions; the owner's metric-definition work (net sales bases, exchange orders, Shopify/Amazon COGS scope) is still open elsewhere, so these may shift.
* Weekday effects are not in the seeds. They are handled by the expected-curve context shown beside each target position.
