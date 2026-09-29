# Seleric Agent — Complex Descriptive / Diagnostic Question Test (Round 2)

**Date:** 2026-09-29 (UTC), stack running at `http://127.0.0.1:8091`
**Scope:** 14 complex **descriptive / diagnostic / comparison** questions.
Prescriptive and predictive questions were deliberately **excluded** — the stack exposes no
scenario, forecast or recommendation tool (confirmed in Round 1:
`docs/audits/2026-09-29_15_question_smoke_test.md`), so those questions can only produce
invented answers. This round tests what the product claims to do: describe and explain
what happened, verified claim-by-claim against the live ClickHouse database.

**Harness:** `/tmp/opencode/seleric_test/harness2.py` (14 questions, `CONCURRENCY=2`,
client deadline 700 s) → `results2/c*.json`, `results2/all_merged.json`
**Ground truth:** `/tmp/opencode/seleric_test/gt.py` → `results2/gt.json`
(19 MCP probes + 21 hand-written `serve.*` SQL probes, all executed against ClickHouse
26.6.1.1193 at `clickhouse.seleric.com:8123`, DB `serve`, `brand_id = 20`)
**Helpers:** `ch.py` (ClickHouse HTTP + Cube `/cubejs-api/v1/sql` compile), `mcpcall.py`,
`show.py` (answer ⇄ ground-truth diff viewer)

---

## 1. Outcome summary

| # | id | question (abridged) | status | class | wall s | server s | steps | evid | verdict |
|---|----|---------------------|--------|-------|--------|----------|-------|------|---------|
| 1 | c01 | 7-day funnel by channel | completed | diagnostic | 86.8 | 64.2 | 18 | 43 | **PARTIAL — grain mix** |
| 2 | c02 | ATC-rate drop Sep 14→21 by channel | partial | trend | 56.7 | 40.6 | 16 | 0 | **FAIL — wrong year (2024)** |
| 3 | c03 | mobile vs desktop funnel | partial | trend | 87.8 | 36.1 | 18 | 5 | **PASS (honest refusal)** |
| 4 | c04 | returns by product type | partial | diagnostic | 92.6 | 34.0 | 10 | 180 | **FAIL — join produced nothing** |
| 5 | c05 | first vs last touch attribution | completed | comparison | 63.0 | 39.1 | 10 | 14 | **PARTIAL — missed available source** |
| 6 | c06 | region net sales + top-5 city AOV | completed | trend | 398.8 | 186.1 | 18 | 668 | **PARTIAL — no regions, AOV null** |
| 7 | c07 | COD vs prepaid | completed | comparison | 140.2 | 64.9 | 60 | 5 | **FAIL — COD invented by subtraction** |
| 8 | c08 | gross profit/COGS/margin by channel | completed | trend | 239.2 | 27.2 | 16 | 8 | **PARTIAL — values ✓, margin % ✗** |
| 9 | c09 | top-10 campaigns by spend + ROAS | completed | trend | 82.1 | 31.1 | 14 | 20 | **PARTIAL — spend ✓, 5/10 ROAS missing** |
| 10 | c10 | high-traffic worst-converting pages | completed | diagnostic | **700.2** | 32.9 | 8 | **1496** | **PARTIAL — join collapse** |
| 11 | c11 | best hour / best weekday | completed | trend | 81.7 | 20.4 | 12 | 168 | **PARTIAL — hour ✓, rate ✗, DOW ✗** |
| 12 | c12 | unattributed share, WoW | completed | trend | 33.6 | 19.0 | 10 | 5 | **FAIL — no share computed** |
| 13 | c13 | Aug→Sep margin bridge | completed | trend | 147.2 | 121.4 | 26 | 7 | **PARTIAL — margins ✓, returns missed** |
| 14 | c14 | new vs repeat mix over 90 days | completed | trend | **700.2** | 45.9 | 20 | 3 | **FAIL — no change over time** |

**Totals:** 11 `completed`, 3 `partial`, 0 `failed`, 0 rate-limit errors (Round 1 had 22
`RateLimitError` at concurrency 4 — this round ran at `CONCURRENCY=2`).
Verdicts: 1 PASS, 7 PARTIAL, 5 FAIL, 0 fully-correct.

**Aggregate quality:** 0/14 answers were fully correct on every number asked for.
**13/14** answers either contained a number that does not reconcile with ClickHouse, or
omitted a number the question explicitly asked for (only c03, the honest device refusal, was
clean). 5 answers were graded FAIL because the headline of the answer is wrong or absent.

---

## 2. Ground-truth method (how each claim was verified)

1. **MCP path** — `mcp_metrics(measures, range, dimensions)` → `provenance.cube_query` →
   `POST http://127.0.0.1:4001/cubejs-api/v1/sql` → bind `?` params → execute on ClickHouse.
   This reproduces exactly what the agent's `query_metrics` tool sees.
2. **Direct path** — hand-written SQL over `serve.session_funnel`, `serve.web_events_daily`,
   `serve.funnel_daily`, `serve.commerce_orders`, `serve.product_performance`,
   `serve.order_attribution`, `serve.attribution_paths`, `serve.channel_pnl`,
   `serve.ad_channel_pnl_daily`, `serve.canonical_pnl`, `serve.refund_events`,
   `serve.sales_all_channels`.
3. Measure SQL was mirrored from the Cube models
   (`mage-ai/infra/cube/model/cubes/serve_*.yml`), e.g.
   `channel_pnl.gross_profit = gross_sales − gross_cogs − ad_spend`,
   `canonical_pnl.net_sales = net_sales_excl_tax`.

**Window caveat:** agent windows and probe windows are not always identical
(agent typically `2026-08-31..2026-09-29` for "last 30 days"; probes `2026-08-30..<2026-09-30`).
MCP `range` is end-**inclusive**, SQL `BETWEEN`-style probes are end-**exclusive** — this alone
explains 7-day vs 8-day totals (e.g. `c02` organic sessions 5,867 vs 6,578). Differences of
this size are called out as *window*, not as *error*; numbers below are quoted on the
window that matches the agent's stated period.

---

## 3. Per-question verification

### c01 — 7-day funnel by channel → **PARTIAL (grain/source mixing)**
Question asked for sessions, product views, ATC, checkout, purchases + step conversion, by channel.

*Verified correct:* every **session** number matches `serve.session_funnel` exactly
(organic 5,587 · ig_feed 3,974 · fb_feed 2,539 · google_pmax 825 · google_search 709 ·
google_shopping 442 · other 114 · ai_chatgpt 63 · email_judgeme 8 · audience_network 8,
window 2026-09-23..09-29).

*Verified wrong:* the other three columns come from **two different tables**:
- `product_views` fb_feed **3,807** = `serve.web_events_daily.events WHERE event_type='product_view'`
  (event grain), not session grain — true session-scoped product views = **2,107**.
- `add_to_cart_events` fb_feed **351** = `web_events_daily` add-to-cart events; true
  session-grain ATC = **142**.
- `funnel_purchases` ig_feed **96** = `serve.funnel_daily.purchases`; session-grain
  purchased sessions = **84**.

Consequence: the answer reports impossible rates — **PV/session 156.5 % (ig_feed)**,
**350 % (audience_network)**, and ATC/PV 10.34 %. Session-grain ground truth for the same
window: sessions 14,269 · product views 9,589 · ATC 510 · checkout 92 · purchases 174 ·
conversion **1.22 %**. The requested **checkout step was omitted** (excused as
"checkout step not available" — it is: `stage_reached_checkout`, and `funnel_daily.checkout_sessions`).

Evidence confirms three sources in one table: `web_sessions` + `product_views` +
`add_to_cart_events` + `funnel_purchases`.

### c02 — ATC rate fell Sep 14→Sep 21, which channel? → **FAIL (resolved to 2024)**
Agent answered: *"no rows for 2024-09-14..2024-09-20 and 2024-09-21..2024-09-27"* and stopped
(status `partial`, 0 evidence). It silently resolved the years to **2024**, a window with no
data (all `serve.*` session tables start 2026-06-01), instead of the most recent matching
occurrence (2026).

Ground truth (`serve.session_funnel`, 7-day windows, brand 20):

| channel | w14 sessions | w14 ATC | w14 rate | w21 sessions | w21 ATC | w21 rate | Δ pp |
|---|---|---|---|---|---|---|---|
| google_shopping | 768 | 29 | 3.776 % | 557 | 15 | 2.693 % | −1.08 |
| google_search | 472 | 24 | 5.085 % | 654 | 24 | 3.670 % | −1.42 |
| google_pmax | 432 | 19 | 4.398 % | 551 | 12 | 2.178 % | **−2.22** |
| organic | 5,867 | 26 | 0.443 % | 6,273 | 69 | 1.100 % | +0.66 |
| ig_feed | 477 | 20 | 4.193 % | 4,162 | 243 | 5.839 % | +1.65 |
| fb_feed | 596 | 7 | 1.174 % | 2,522 | 122 | 4.837 % | +3.66 |
| **total** | **8,862** | **147** | **1.659 %** | **15,007** | **508** | **3.385 %** | **+1.73** |

So (a) the premise is false in aggregate — the rate **rose** 1.73 pp; (b) the honest answer
is "the three Google channels' ATC rates fell (PMax −2.22 pp is the largest), while IG/FB/organic
rose". The agent produced neither, and never attempted 2026.

### c03 — mobile vs desktop → **PASS (correct refusal)**
`serve.session_funnel.device_type`: **89,356 / 89,356 NULL** for 2026-08-30..09-30, brand 20.
Agent refused to compare, listed the weekly device-less series it did get, and offered
three concrete next steps. This matches the database exactly. Status `partial` is appropriate.

### c04 — returns by product type → **FAIL (no numbers delivered)**
Agent fetched 3 drilldowns (60 rows each, 180 evidence entries) then reported:
*"the drilldown outputs returned in this session were not in the simple row format my
combinator expects … I could not compute the per-product_type return rates"*.

Ground truth (`serve.product_performance`, `order_date 2026-08-31..<09-30`, `is_eligible_line=1`):

| product_type | return rev | cancel rev | removed | units | refunded | unit return rate |
|---|---|---|---|---|---|---|
| Pet Footwear | 157,134.43 | 31,427.36 | **188,561.79** | — | 67 | — |
| Pet Grooming Tools | 22,394.03 | 3,853.93 | 26,247.96 | — | 13 | — |
| Stuffed Toys & Plushies | 19,669.52 | 3,302.55 | 22,972.07 | — | 13 | — |
| Harnesses | 19,104.42 | 2,117.80 | 21,222.22 | — | 6 | — |
| T-Shirts | 2,539.83 | 846.61 | 3,386.44 | 12 | 4 | 33.3 % |
| Pet Beds | 2,117.80 | 3,600.08 | 5,717.88 | 9 | 3 | 33.3 % |

Top revenue-remover = **Pet Footwear, ₹188,561.79 removed**; highest unit return rates on
material volume = T-Shirts / Pet Beds (33 %). None of this reached the user.

### c05 — first vs last touch attribution → **PARTIAL (available source not explored)**
Agent reported: last_touch_v1 = **1,056** orders (✓ exactly `serve.order_attribution`),
avg touch count **1.99748** (GT 1.99042 on the probe window — window shift only), channel mix
for last touch only, and *"other models unavailable / cannot compute disagreement"*.

Ground truth shows two things the agent did not look at:
- `serve.attribution_paths` contains **all four models**: 795 orders each
  (`first_touch_v1`, `linear_v1`, `last_touch_v1`, `last_non_direct_v1`), avg touch 1.99042,
  single-touch 492/795 — so "how many orders does each model claim" **is** answerable.
  (Source disagreement: `attribution_paths` 795 vs `order_attribution` 1,056 — an unexplained
  ~25 % gap between two serve tables that no answer surfaced.)
- `attribution_paths` carries `first_touch_channel_source` and `last_touch_channel_source`, so
  channel-mix disagreement **is** computable: FT mix ig 1,456 / google 728 / fb 300 / … vs
  LT ig 1,468 / google 732 / fb 292 / …; **first≠last channel on 28/795 = 3.5 % of orders**
  (per-model rows; the table duplicates each path across 4 models).

### c06 — regions + top-5 city AOV → **PARTIAL**
- **Part 1 not answered:** *"The drilldown query returned 30 region rows (evidence available)"*
  — no region values were printed. GT (`serve.sales_all_channels.total_sales`):
  MAHARASHTRA 653,466 · KARNATAKA 451,575 · DELHI 298,333 · TELANGANA 232,599 · HARYANA 180,344.
- **Part 2 half-answered:** top-5 cities by the agent's `attributed_net_revenue`
  (blank 1,903,687 · MUMBAI 279,976 · BANGALORE 200,547 · HYDERABAD 144,697 · DELHI 109,734)
  reconciles to `serve.commerce_orders` net sales within 1–5 %
  (different metric — MUMBAI 288,483 · BANGALORE 211,080 · HYDERABAD 146,042 · DELHI 113,956 · GURGAON 86,708) —
  but **AOV was null for all top-5 rows** and the answer gave no AOV at all.
  GT AOV: MUMBAI ₹1,873.26 · BANGALORE ₹1,788.81 · HYDERABAD ₹2,147.67 · DELHI ₹1,675.83 ·
  GURGAON ₹1,806.41 (spread ₹1,676–₹2,148, i.e. +28 % Hyderabad vs Delhi).
- **Data quality surfaced:** the #1 "city" is blank with ₹1.9 M — should have been flagged as
  a coverage defect rather than ranked as a city.

### c07 — COD vs prepaid → **FAIL (fabricated bucket by subtraction)**
Agent: prepaid **120** ✓, total orders **1,056** (from `order_attribution`, not commerce),
**COD = 1,056 − 120 = 936**, COD net sales / AOV / refunds *"unavailable — query returned
stale/failed"*.

Ground truth (`serve.commerce_orders`, same window, placement orders):
`payment_bucket=manual` **895** orders / ₹1,623,678.96 / AOV ₹1,814.17 ·
`online` **120** / ₹205,369.26 / AOV ₹1,711.41 (matches agent's prepaid ✓) ·
`cod` **68** / ₹107,417.21 / AOV ₹1,579.66.
`is_cod=1` metric agrees: **68 COD orders, ₹107,417.21**.

So the derived COD count is wrong by **13.8×** (936 vs 68) — it silently absorbed the
895 `manual` orders. Refund share GT (orders refunded ÷ orders, `order_date` cohort):
manual 115/895 = 12.9 % · cod 16/68 = 23.5 % · online 10/120 = 8.3 % — none of it delivered.

### c08 — gross profit / COGS / margin % by channel → **PARTIAL (values exact, margin % wrong)**
Channel values are an **exact match** to both Cube and `serve.channel_pnl` for 2026-09-01..09-28:

| channel | gross profit (agent = GT) | gross COGS (agent = GT) | gross sales | ad spend |
|---|---|---|---|---|
| meta | −196,011.45 | 444,687.76 | 1,041,741.62 | 793,065.31 |
| google | −12,181.62 | 233,239.04 | 538,582.56 | 317,525.14 |
| organic | 22,622.52 | 16,187.41 | 38,809.93 | 0 |
| unattributed | 271,935.87 | 203,971.96 | 475,907.83 | 0 |

But the margin % contradicts the agent's own stated formula `gp / (gp + cogs)`:

| channel | agent | stated formula | on gross sales | denominator that reproduces the agent |
|---|---|---|---|---|
| meta | −30.62 % | **−78.82 %** | −18.82 % | `cogs + \|gp\|` → −30.59 % |
| google | −4.97 % | **−5.51 %** | −2.26 % | `cogs + \|gp\|` → −4.96 % |
| organic | 58.33 % | 58.29 % | 58.29 % | ✓ |
| unattributed | 57.15 % | 57.14 % | 57.14 % | ✓ |

The sign of a negative gross profit is being flipped in the denominator — only the two
negative-margin channels are affected, and they are the two that matter most here.

### c09 — top-10 campaigns by spend + ROAS → **PARTIAL (5/10 ROAS missing)**
Spend is exact vs `serve.ad_channel_pnl_daily` for 2026-09-16..09-29:
Demand Gen ₹17,134.09 · SUSPENDER-17JULY ₹15,934.90 · PAWTECH ₹15,896.59 ·
WINGBIRD ₹15,812.20 · SUSPENDER-26SEP ₹13,902.80 · Brand Search ₹13,372.84 ·
PMax-Seasonal ₹11,167.81 · DOG TOY ₹9,452.87 · BLUEJAY ₹8,783.11 · SCRATCHLOUNGE ₹8,696.21.

ROAS joined for only **5/10**; the other five were reported *"no matching ROAS row … unavailable"*
even though the agent had fetched them (evidence ids listed). GT:

| campaign | agent ROAS | GT ROAS |
|---|---|---|
| PAWTECH | 1.9741 | 1.97415 ✓ |
| WINGBIRD | 1.3977 | 1.39773 ✓ |
| SUSPENDER-26SEP | 1.3557 | 1.35573 ✓ |
| BLUEJAY | 1.3115 | 1.31145 ✓ |
| SCRATCHLOUNGE | 1.8571 | 1.85705 ✓ |
| Demand Gen | unavailable | **0.000** (₹0 sales on ₹17.1 k spend) |
| SUSPENDER-17JULY | unavailable | **0.428** |
| Brand Search | unavailable | **−3.356** |
| PMax-Seasonal | unavailable | **−6.978** |
| DOG TOY | unavailable | **0.618** |

Two of the three worst ROAS campaigns in the top-10 (Brand Search, PMax) were reported as
"unavailable" instead of strongly negative.

### c10 — high-traffic worst-converting landing pages → **PARTIAL (+ 700 s hang)**
Agent returned a 2-row answer: `/` with **conversion_rate null** (22,112 sessions) plus a
`None` path artefact, and attributed the nulls to data quality.

Ground truth (`serve.session_funnel`, 2026-08-30..09-30): `session_conversion_rate` **does**
exist per `landing_page_path` — `/` = 22,921 sessions, **0.327 %** (75 purchases);
product pages 1.1 %–3.5 % (e.g. `pocketpup-tote-bag` 1.225 %, `snugsole-pet-boots` 1.112 %,
`wildtrail-boots` 1.180 %, `crabdash` 3.511 %). The Cube probe returns these rows directly.
So the ranking conclusion ("/ is the worst high-traffic page") is right by luck, but every
conversion number asked for is missing, the comparative table was never built, and the
"joining produced nulls" explanation was offered as a data-quality problem rather than a
parsing problem.

Also: **1,496 evidence rows**, `result_json` **155 KB**, and the client waited the full
**700 s deadline** because no `run.completed` was ever streamed (see §5).

### c11 — best hour / best weekday → **PARTIAL**
- **Hour: correct rank, wrong rate.** Agent: hour **15**, conversion **2.7459954 %**.
  Ground truth hour-15 aggregate over 2026-08-31..09-29 = **1.5285 %** (45/2,944;
  on the probe window 08-30..09-30 = 1.4834 %). The 2.7459954 % is the
  **(hour=15, weekday=Monday) cell** = 12/437 = 2.746 % — a cell rate reported as an
  hour-level rate. Hour ranking itself is right (hour 15, then 5, 3, 4, 7).
- **Weekday: wrong conclusion.** Ground truth day-level rollup (same window):
  **dow 7 = Sunday 1.324 %** (183/13,826) · dow 5 Friday 1.148 % · dow 1 Monday 1.049 %.
  The agent first wrote *"day_of_week = 7 (Sunday)"*, then concluded
  *"best day-hour pair is Monday 15:00"* and stated a day-level query was not available —
  it is: `session_conversion_rate` by `session_day_of_week` returns exactly the rows above.

### c12 — unattributed share of ad-channel net sales, WoW → **FAIL (no share computed)**
Agent fetched a weekly `attributed_net_revenue` series, then stopped: *"Missing input: total
net sales … without total net sales I cannot calculate the unattributed share."*
Status was set to **`completed`**.

Ground truth (`serve.ad_channel_pnl_daily`, brand 20):

| week | unattributed net sales | all-channel net sales | share |
|---|---|---|---|
| 2026-08-30 | 10,445.50 | 300,632.08 | **3.47 %** |
| 2026-09-06 | 139,109.14 | 682,370.67 | **20.39 %** |
| 2026-09-13 | 54,785.00 | 193,564.03 | **28.30 %** |
| 2026-09-20 | 113,504.44 | 296,394.71 | **38.30 %** |
| 2026-09-27 | −237,988.57 | −948,361.99 | 25.09 % (negative-base week) |

Orders: 238/1,099 = **21.66 %** unattributed. The denominator is one `sum()` away in the
same table the agent had already queried (the agent also used a different metric
— `attributed_net_revenue` — than "ad channel net sales").

### c13 — Aug→Sep margin bridge → **PARTIAL (headline ✓, dominant driver missing)**
Verified correct: margin **48.36 % → −157.83 % (−206.19 pp)** exactly reproduces
`serve.canonical_pnl` (`gp/ns`: 1,357,689.12/2,807,402.86 and −645,564.39/409,037.14);
Sep gross sales 2,101,479.24 ✓; net COGS 1,449,713.74 / 1,054,601.53 ✓.

Verified wrong / missing:
- **Returns were never fetched** (agent said so) — yet returns are the second-largest driver:
  Aug ₹1,034,422.97 → Sep ₹1,508,184.92 (**+46 %**), against gross sales 4,049,917.49 →
  2,101,479.24 (−48 %) and net sales 2,807,402.86 → 409,037.14 (**−85.4 %**).
- Agent states *"net_sales ≈ 48 % lower"* — that is the **gross sales** decline; actual
  net sales fell **85.4 %**.
- Aug gross sales quoted as 4,025,319.91 (from `gross_sales_all_channels`) vs
  `canonical_pnl.gross_sales_excl_tax` 4,049,917.49 — 0.61 % cross-source drift, unreconciled.
- Discounts only as a two-month total ₹151,958.55, and that does not reconcile with
  `canonical_pnl` (Aug 70,760.24 + Sep 105,795.89 = **176,556.13**).
- **No flag that September is incomplete**: 26/29 days `is_final=1`, max day 2026-09-29.

### c14 — new vs repeat mix over 90 days → **FAIL (level, not change)**
Agent: new ₹6,505,106.82 = 68.7 %, repeat ₹2,963,724.04 = 31.3 % for 2026-07-02..09-29.
Aggregate level reconciles with `serve.commerce_orders` (Jul–Sep new share ≈ 70 % including
Jul 1). But the question asked **how the mix changed**; there is no second time point, no
trend, no pp movement — only *"New customers contributed a larger share … a 37.4
percentage-point advantage"*, which is a cross-section, not a change.

Ground truth (monthly, `dashboard_net_sales_excl_tax`, brand 20):

| month | new rev | repeat rev | new share |
|---|---|---|---|
| 2026-04 | 3,620,298 | 801,123 | 81.9 % |
| 2026-05 | 2,876,967 | 779,068 | 78.7 % |
| 2026-06 | 2,695,667 | 753,942 | 78.1 % |
| 2026-07 | 3,427,904 | 969,930 | 77.9 % |
| 2026-08 | 2,144,253 | 1,189,939 | 64.3 % |
| 2026-09 | 1,103,648 | 695,154 | 61.4 % |

The real story — **new-customer revenue share fell 16.5 pp from July to September**,
i.e. the mix moved *toward repeat* — was not delivered. Also, the SSE stream for this run
stalled at 60.0 s (see §5).

---

## 4. Defects found this round

| id | defect | evidence | affected |
|----|--------|----------|----------|
| **D1** | **Grain mixing across tables in one table** — session-grain denominators with event-grain numerators; PV/session > 100 % | c01 (156.5 %, 350 %); `web_events_daily` vs `session_funnel` vs `funnel_daily` | c01, any funnel question |
| **D2** | **Relative date resolved to a year with no data**; no fallback to most recent occurrence; gave up instead of re-resolving | c02 answered 2024-09-14 vs data starting 2026-06-01 | all relative dates |
| **D3** | **Drilldown join contract failure** — model must string-join artefacts; keys don't match, joins silently yield null/empty | c04 ("combinator … not in the simple row format"), c06 AOV null, c09 5/10 ROAS missing, c10 null conversion | **4/14 questions** |
| **D4** | **Bucket derived by subtraction across sources** (`COD = total − prepaid`) absorbed 895 `manual` orders | c07: 936 vs true 68 (13.8×) | any COD/prepaid split |
| **D5** | **Ratio denominator sign flip** — `gp/(cogs+\|gp\|)` instead of `gp/(gp+cogs)` | c08 meta −30.62 % vs −78.82 %; google −4.97 % vs −5.51 % | any negative-margin group |
| **D6** | **Cell rate reported as aggregate rate; wrong top-level conclusion** | c11 2.746 % cell vs 1.529 % hour; Monday vs Sunday | best-X questions |
| **D7** | **Named drivers not fetched before answering a decomposition** | c13 returns omitted; net-sales decline stated as 48 % instead of 85.4 % | c13, any bridge/why question |
| **D8** | **Trend question answered with a single cross-section** | c14 no time comparison at all | any "change over time" |
| **D9** | **Underspecified "not available"** — alternative dimensions/tables not explored before declaring unavailable | c05 (`attribution_paths` has 4 models + FT/LT sources), c11 (day rollup exists), c12 (denominator in same table) | c05, c11, c12 |
| **D10** | **`status=completed` while the question is unanswered** | c12 (no share), c14 (no change), c10 (no conversion numbers) | 3/14 |
| **D11** | **Evidence unbounded** | c10 1,496 rows / 155 KB `result_json`; c06 668 / 64 KB | c06, c10, c11, c04 |
| **D12** | **SSE terminal delivery failures** | c10: `answer.completed` at 85.8 s but **no `run.completed`** → client waited 700 s; c14: stream **stops at 60.0 s** mid-answer while mission is `completed` server-side | 2/14 |
| **D13** | **Zero tool events on the streamed path** | 0 tool events across all 14 runs (Round-1 R-finding reproduced) | 14/14 |
| **D14** | **Claims layer never populates** | `claims: []` in `result_json` for **14/14** | 14/14 |
| **D15** | **`trace.validation` / `trace.intent` / `trace.complexity` never persisted** | trace keys are only `elapsed_seconds, langsmith_run_id, langsmith_run_url, request_id, session_id, steps` — 0/14 | 14/14 |
| **D16** | **MCP `range` end-inclusive vs SQL end-exclusive** | c02 organic 6,578 (MCP) vs 5,867 (SQL) for the "same" 7-day week | every windowed comparison |

---

## 5. Reliability, latency, observability

**Run outcomes:** 11 `completed` / 3 `partial` / 0 `failed`. No rate-limit or fallback
exhaustion errors (Round 1 recorded 22 `RateLimitError` + 22 exhausted fallbacks at
concurrency 4; this round ran at `CONCURRENCY=2`).

**Latency (from submit → client):**

| metric | median | mean | max |
|---|---|---|---|
| client wall clock | **90.2 s** | 207.9 s | **700.2 s** (2 deadline hits) |
| first `answer.delta` | **77.2 s** | — | 228.9 s |
| server `trace.elapsed_seconds` | **37.6 s** | — | 186.1 s (c06) |
| steps | 16 (med) | — | 60 (c07) |

- Server work is ~2.4× faster than what the client experiences (37.6 s vs 90.2 s medians) —
  queueing + SSE + post-completion flush, consistent with Round 1.
- Worst: c06 = 398.8 s client / 186.1 s server; c10 and c14 never received a terminal event
  and consumed the full 700 s deadline (**D12**).
- Round-1 comparison: server-side exec median 31.9 s → this round 37.6 s (comparable);
  Round-1 first-token median 26.0 s vs this round first-`answer.delta` 77.2 s measured from
  submit — definitions differ, treat as indicative only.

**Observability gaps (all reproduced from Round 1):**

| signal | this round |
|---|---|
| `agent.tool_started/completed` events on SSE | **0 / 14** |
| `trace.validation` present | **0 / 14** |
| `trace.intent` present | **0 / 14** |
| `trace.complexity` present | **0 / 14** |
| `claims` non-empty | **0 / 14** |
| `limitations` non-empty | 1 / 14 |
| `GET /v1/missions/{id}` per-step timing | steps only, no timestamps per step |
| query classes seen | `trend` 9 · `diagnostic` 3 · `comparison` 2 |
| questionable classification | c02 (two-week comparison) → `trend`; c14 (mix change) → `trend` |
| evidence rows | 0 … 1,496 (c10) |
| `result_json` size | 4.3 KB … 155 KB (c10), 64 KB (c06) |

---

## 6. Data-quality gaps (database-side)

- `serve.session_funnel.device_type` — **100 % NULL (89,356/89,356)**; `browser_family`,
  `geo_*` likewise unpopulated. `platform`, `channel`, `landing_page_path` are fine.
  → Device/browser questions are unanswerable; the agent handled c03 correctly.
- `shipping_city` blank on the largest attributed-revenue bucket (₹1.9 M in c06) —
  should be reported as a coverage gap, not ranked as a city.
- `serve.canonical_pnl` September is **partial**: 26/29 days `is_final=1`, max day 2026-09-29;
  `ad_channel_pnl_daily` week 2026-09-27 has **negative** net sales (−948,361.99) from
  non-final adjustments — any WoW share computed there needs an `is_final` caveat.
- Two serve tables disagree on order counts for the same window: `attribution_paths` 795/model
  vs `order_attribution` 1,056 (last touch only) — a ~25 % gap that no tool explains.
- `serve.commerce_orders.payment_bucket` has three values (`manual` 895 / `online` 120 /
  `cod` 68); `is_cod=1` agrees with `cod`. Any answer that maps "not prepaid" to COD is wrong.
- Metric id `sessions` is still not a catalogue metric (`web_sessions` is); cross-cube
  multi-measure queries still return 0 rows (Round-1 finding, unchanged).

---

## 7. Coverage excluded this round

- **Prescriptive** ("what should we do about …", budget reallocation, pricing actions) —
  no planning/recommendation tool exists; Round 1 showed the model improvises or refuses.
- **Predictive** (forecast next month, expected lift) — no forecast/scenario tool.
- **Causal attribution** beyond descriptive comparisons — `causal.py::_load_evidence` is
  still all-or-nothing (Round-1 R-finding; 8 fabricated ids killed a 366-row estimate).
- These will stay out of scope until the tools exist; adding them now would only measure
  hallucination.

---

## 8. Recommendations (Round 2 — R16…R33)

- [ ] **R16** Add a **grain guard** to `query_metrics`/answer assembly: refuse to divide a
      session-grain denominator by an event-grain numerator; assert PV/session ≤ 1 when both
      claim session grain (fixes D1).
- [ ] **R17** Resolve relative dates to the **most recent window with data**; if the resolved
      window returns 0 rows, re-resolve (e.g. latest 7-day week) before emitting
      `INSUFFICIENT_EVIDENCE`, and always print the resolved absolute window (fixes D2).
- [ ] **R18** Replace model-side artefact string-joining with a server-side **`join`/`lookup`**
      or a single multi-measure query returning canonical keys; add a regression test that
      joins two 60-row drilldowns (fixes D3 — 4/14 questions).
- [ ] **R19** Forbid deriving a bucket by subtracting metrics from different sources;
      require the dimension's own values (`payment_bucket`, `is_cod`) (fixes D4).
- [ ] **R20** Ratio validator: recompute every derived % server-side, reject
      `|margin| > 100 %` when denominator sign is ambiguous, unit-test negative-`gp` groups
      (fixes D5).
- [ ] **R21** "Best X" answers must use the **aggregate** for X; if a cell rate is shown,
      label it `hour+weekday cell` (fixes D6).
- [ ] **R22** Decomposition/bridge questions: block completion until every **named** driver
      (gross sales, COGS, discounts, returns) has evidence (fixes D7).
- [ ] **R23** Trend/change questions require ≥ 2 distinct time buckets in evidence; fail
      validation otherwise (fixes D8).
- [ ] **R24** Before answering "unavailable", enumerate the candidate dimensions/tables
      (e.g. `first_touch_channel_source`, `session_day_of_week`, same-table denominator)
      and report which were checked (fixes D9).
- [ ] **R25** Introduce `completed_partial` / answer-coverage gate so `completed` means the
      asked questions were answered (fixes D10).
- [ ] **R26** Emit `run.completed` for every terminal path; fix the c14-class mid-stream stall
      (missing terminal → client hangs 700 s) (fixes D12).
- [ ] **R27** Restore `event_stream_handler` on the streamed path — 0 tool events in 14/14
      runs (fixes D13).
- [ ] **R28** Persist and expose `trace.validation`, `trace.intent`, `trace.complexity`
      (0/14) (fixes D15).
- [ ] **R29** Populate `claims` (0/14 non-empty) or remove claims from the product surface.
- [ ] **R30** Cap evidence rows per mission (e.g. ≤ 100) and dedupe by metric×dimension×time;
      1,496 rows / 155 KB per mission is unbounded growth (fixes D11).
- [ ] **R31** Either backfill `device_type`/`browser_family`/`geo_*` or drop them from the
      catalogue so questions fail fast with a documented gap (c03 handled it correctly).
- [ ] **R32** Surface non-final/partial periods in the scope line (`is_final`, days covered,
      negative-adjustment weeks) for any month/week boundary answer.
- [ ] **R33** Fix the MCP `range` end-inclusive vs SQL end-exclusive mismatch, or document it
      in the tool schema so windows are reproducible (fixes D16).

---

## 9. Verification checklist (unchecked = open)

**Correctness — answers vs ClickHouse**

- [ ] c01: sessions per channel match `serve.session_funnel` (verified ✓)
- [ ] c01: product views sourced from session grain, not `web_events_daily`
- [ ] c01: ATC sourced from session grain, not `web_events_daily`
- [ ] c01: purchases sourced from session grain (`session_funnel` / `funnel_daily` reconciled)
- [ ] c01: no conversion rate > 100 % (observed 156.5 %, 350 %)
- [ ] c01: checkout step delivered (requested, omitted)
- [ ] c01: per-channel step rates recomputed and equal to GT within 0.1 pp
- [ ] c02: window resolved to 2026, not 2024
- [ ] c02: overall ATC rate WoW reported (GT 1.659 % → 3.385 %, +1.73 pp)
- [ ] c02: channel-level deltas reported (PMax −2.22 pp is largest decline)
- [ ] c02: false premise ("rate fell") challenged with evidence
- [ ] c03: device availability checked before refusing (GT 89,356 NULL ✓)
- [ ] c03: refusal language contains no fabricated device numbers (verified ✓)
- [ ] c03: refusal offers actionable next steps (verified ✓)
- [ ] c04: per-product return rate delivered (GT: T-Shirts/Pet Beds 33 %)
- [ ] c04: return + cancel revenue removed delivered (GT: Pet Footwear ₹188,561.79)
- [ ] c04: join of 3 drilldowns succeeds (failed — 180 evidence, 0 output)
- [ ] c05: orders claimed by all 4 attribution models (GT 795 each)
- [ ] c05: avg touch count reconciled across sources (1.9904 vs 1.9975)
- [ ] c05: first-touch channel mix delivered (available in `attribution_paths`)
- [ ] c05: channel-mix disagreement quantified (GT 3.5 % of orders differ)
- [ ] c05: 795 vs 1,056 source gap explained or flagged
- [ ] c06: top shipping regions with numbers (GT MAHARASHTRA ₹653,466 …) — omitted
- [ ] c06: top-5 city AOV delivered (GT ₹1,676–₹2,148) — null
- [ ] c06: blank-city ₹1.9 M flagged as data gap, not ranked as a city
- [ ] c07: COD order count = 68 (reported 936)
- [ ] c07: COD net sales/AOV delivered (₹107,417.21 / ₹1,579.66) — "unavailable"
- [ ] c07: prepaid figures correct (120 / ₹205,369.26 / ₹1,711.41 ✓)
- [ ] c07: refund share per bucket delivered (GT cod 23.5 %, manual 12.9 %, online 8.3 %)
- [ ] c08: gross profit by channel matches GT exactly (verified ✓, 4/4)
- [ ] c08: gross COGS by channel matches GT exactly (verified ✓, 4/4)
- [ ] c08: meta margin % = −78.82 % per stated formula (reported −30.62 %)
- [ ] c08: google margin % = −5.51 % (reported −4.97 %)
- [ ] c08: margin formula documented consistently with the numbers shown
- [ ] c09: top-10 spend matches `ad_channel_pnl_daily` (verified ✓)
- [ ] c09: ROAS present for all 10 (5/10 missing)
- [ ] c09: negative ROAS reported as negative (Brand Search −3.36, PMax −6.98 missing)
- [ ] c10: conversion rate per landing page delivered (GT `/` 0.327 %) — null
- [ ] c10: comparative worst-converter table built (2 rows only)
- [ ] c10: null-conversion explained as join failure, not data quality
- [ ] c11: best hour = 15 (verified ✓)
- [ ] c11: hour-15 rate is the aggregate 1.5285 % (reported 2.746 % cell)
- [ ] c11: best weekday = Sunday 1.324 % (agent concluded Monday)
- [ ] c11: day-level rollup attempted (claimed unavailable; exists)
- [ ] c12: weekly unattributed share delivered (GT 3.47/20.39/28.30/38.30/25.09 %)
- [ ] c12: orders share delivered (GT 21.66 %)
- [ ] c12: denominator fetched instead of asking the user
- [ ] c12: status not `completed` while unanswered
- [ ] c13: Aug/Sep margin % and pp delta correct (verified ✓ −206.19 pp)
- [ ] c13: Sep gross sales / net COGS correct (verified ✓)
- [ ] c13: returns included as a named driver (GT +46 % MoM) — omitted
- [ ] c13: net-sales decline stated as 85.4 % (stated 48 %)
- [ ] c13: Aug gross sales reconciled across sources (4,049,917 vs 4,025,320)
- [ ] c13: discounts split by month and reconciled (176,556 GT vs 151,959 quoted)
- [ ] c13: September flagged as partial (26/29 days final)
- [ ] c14: new/repeat revenue level reconciles (verified ✓ ~68.7 %)
- [ ] c14: change over time delivered (GT 77.9 % → 64.3 % → 61.4 %) — omitted
- [ ] c14: ≥ 2 time points in evidence
- [ ] c14: status not `completed` while unanswered

**Reliability / latency / observability**

- [ ] every run emits `run.completed` (2/14 did not)
- [ ] no client deadline hits (2/14 hit 700 s)
- [ ] SSE stream survives to terminal (c14 stalled at 60.0 s)
- [ ] tool events present on streamed path (0/14)
- [ ] `trace.validation` persisted (0/14)
- [ ] `trace.intent` persisted (0/14)
- [ ] `trace.complexity` persisted (0/14)
- [ ] `claims` non-empty (0/14)
- [ ] `limitations` populated when scope is partial (1/14)
- [ ] per-step timestamps exposed via API (absent)
- [ ] evidence rows capped (max 1,496)
- [ ] `result_json` size bounded (max 155 KB)
- [ ] query-class labels correct for comparison questions (c02/c14 labelled `trend`)
- [ ] no rate-limit errors at concurrency 2 (verified ✓ 0 errors)
- [ ] server/client latency gap measured and explained (37.6 s vs 90.2 s medians)

**Data / contract**

- [ ] `device_type` backfilled or removed from catalogue (100 % NULL)
- [ ] blank `shipping_city` handled explicitly
- [ ] `is_final` / partial-month caveats surfaced
- [ ] `attribution_paths` vs `order_attribution` count gap reconciled (795 vs 1,056)
- [ ] `payment_bucket` vs `is_cod` mapping documented
- [ ] MCP `range` inclusive/exclusive semantics fixed (D16)
- [ ] catalogue metric id `sessions` either added or rejected with a suggestion
- [ ] cross-cube multi-measure queries return rows (0-row behaviour unchanged)

---

## 10. Reproduction

```bash
cd /tmp/opencode/seleric_test
python3 gt.py                 # → results2/gt.json (MCP + ClickHouse ground truth)
CONCURRENCY=2 python3 harness2.py \
  c03_device_split,c04_returns_by_product,c05_attribution_models,c06_geo_aov,c07_cod_prepaid,\
c08_channel_margin,c09_campaign_roas,c10_landing_pages,c11_diurnal,c12_unattributed,\
c13_margin_bridge,c14_new_vs_repeat        # → results2/*.json, results2/all.json
python3 show.py c01_funnel_channel c02_atc_drop   # answer ⇄ ground-truth diff
```

Round 1 report: `docs/audits/2026-09-29_15_question_smoke_test.md`
Mirror of this report: `/tmp/opencode/seleric_test/REPORT2.md`
