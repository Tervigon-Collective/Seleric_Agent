# Unified business monitoring and anomaly system: plan

Status: **plan only; scope narrowed 2026-11-03 (see section 2b).** Nothing is built and nothing in production is changed. Companions: `01_DATA_FEASIBILITY.md` (what the data supports), `02_TARGET_SEEDS_BRAND20.md` (proposed targets), `03_monitor_schema.sql` (prepared, not applied).

## 1. Goal

One system that tells the owner, **at entity level (ad, campaign, product, geo, cohort), what moved, how sure we are, what it costs in rupees, why, and what to do about it**. It runs on maths (expected values, probabilities, targets), not on hand-set thresholds per metric.

## 2. Decisions so far (owner's words)

| # | Question | Decision |
|---|---|---|
| D1 | Primary job | "tell me at the unit entity level what moved, like ads or campaigns declining or any other trend, so diagnosis becomes easier. Or tell me what should I do" |
| D2 | Speed | Hourly, intraday |
| D3 | Scale | Thousands+ of series (campaigns, products, SKUs) |
| D4 | On alert | Alert + auto-diagnose (existing diagnosis engine) |
| D5 | Entities | Ad/creative, ad set/campaign, product/SKU, channel/brand/geo/cohort |
| D6 | Reference | Own seasonal history **and** target. "we need to define north star metrics, which are configurable, and have a target input, user can give one or many, start with default" (the sentence was cut off after "That "; read as above, confirmed "yes defaults" on 2026-11-02) |
| D7 | Trend windows | Intraday hours; 3-7 days; 2-4 weeks; month vs plan |
| D8 | Actions | Ranked next steps; a human decides (no auto-act) |
| D9 | Target shape | One target per brand plus a floor and a ceiling ("floor ceiling with a target"): floor <= target <= ceiling. Per-entity overrides and time-varying plans were **not** chosen |
| D10 | Default north stars | Net profit / contribution margin; net sales + orders; blended efficiency (MER, CAC, platform ROAS); conversion and quality (CVR, return/RTO rate). Configurable later |
| D11 | No-target rule | Inherit the parent's target, then fall back to own seasonal history |
| D12 | Action families | Budget up/down/shift; pause/rotate/refresh creative; product-side (stock, price, promo, PDP fix); fix tracking or data |

### 2b. Answers of 2026-11-03 and the V1 scope they set

| # | Owner's words | Reading used here |
|---|---|---|
| D13 | "yes add monitor" | Approval to add a `monitor` schema (answer to Q4). **Not applied**: the only login available to me is read-only. DDL is prepared in `03_monitor_schema.sql` for an admin to run. Needs confirmation that this is the intended meaning (Q4) |
| D14 | "brand 20" | First release covers brand 20 only (answer to Q6) |
| D15 | "no past events" | No owner-supplied backtest truth (answer to Q5). Testing uses synthetic injected anomalies plus two real episodes found in the history (ads off 09-15 to 09-22; bulk returns lump on 09-28), whose causes the owner can confirm |
| D16 | "lets make it for specific targets only now seeing the historical data and can be adjusted" | **V1 monitors the default north-star metrics against floor / target / ceiling only.** Targets are seeded from the history (`02_TARGET_SEEDS_BRAND20.md`) and are editable. No scan of every ad, product and geo in V1; the broad entity scan (old P2, P3) is deferred. Needs confirmation that "specific targets only" means this (Q8) |

**V1 in one paragraph.** For brand 20, evaluate about a dozen north-star metrics against owner-editable floor / target / ceiling. Volume metrics (orders, sales, spend, sessions) are also tracked hourly as intraday pace against the expected cumulative curve, with an end-of-day projection. Ratio metrics (MER, CAC, CVR, returns %) are judged on a trailing 7-day pooled value, because daily ratios are noisy. Profit and returns are daily and flagged provisional until settled. Only when a metric breaches (or is likely to breach) is a targeted drill-down run: which campaigns, ad sets, ads and products drive the gap (exact part-wise split plus the existing diagnosis engine), then a ranked recommendation. That keeps the entity-level answer the owner asked for, without testing thousands of series, so false-discovery control is not needed until the full scan returns. A trust gate (section 5, S0) runs first, and a bulk-return lump is reported as its own data event, not as a business breach.

## 3. What the data allows (summary)

Full evidence in `01_DATA_FEASIBILITY.md`. The facts that shape the design:

* Hourly delivery (spend, impressions, clicks, landing-page views) exists for Meta at ad level and for Google at campaign level only. About 45 minutes lag.
* Hourly outcomes exist only for the **brand**: about 1.9 orders per hour and about 108 sessions per hour for the one brand with orders.
* Per ad, the median active ad-hour has 2 clicks and 0 add-to-carts. The median ad has 2 attributed orders in 14 days. Per product, the median is 2 order lines in 14 days.
* Profit and returns are daily and settle with a lag.

**Consequence for D2 and D5:** "hourly at entity level" is true for delivery metrics and for brand orders and sessions. Entity outcome metrics (orders, CPA, ROAS, profit, returns) are daily or multi-day, with pooling. The owner should expect this and agree to it (open question Q1).

## 4. Three cadence tiers

| Tier | What | Cadence | Method family |
|---|---|---|---|
| A. Brand pulse | Orders, revenue, sessions, add-to-cart, checkout, spend, CVR | Hourly, intraday vs expected cumulative curve | Count/rate models, CUSUM, end-of-day projection |
| B. Delivery entities | Spend, impressions, clicks, CTR, CPC, CPM, landing-page views, Meta ATC/IC per ad, ad set, campaign (Google: campaign) | Hourly, with ad-level shrinkage | Hierarchical (partial pooling) rate models, false-discovery control, roll-up |
| C. Outcome entities | Orders, revenue, CAC, ROAS, CVR, returns/RTO, profit for ad, ad set, campaign, product/SKU, state, cohort | Daily, 3-7 day and 2-4 week windows, month-to-date | Pooled posterior rates, Theil-Sen/Mann-Kendall, changepoints, pacing projection |

North-star targets (D9, D10) apply to tiers A and C. Tier B compares to expected only, plus a derived gap through the parent (section 6).

## 5. Pipeline

```
sources (serve.* views, read-only)
  │
S0 trust gate ── freshness, completeness, duplicates, is_final ──▶ data findings (suppress dependent business findings)
  │
S1 series store ── additive parts per node and hour/day (never stored ratios)
  │
S2 expected model ── seasonal expected value + interval, per node, pooled to parent
  │
S3 detectors ── spike (hour), shift (CUSUM), trend (slope), changepoint, pacing
  │
S4 gap layer ── gap to expected, gap to floor/target/ceiling, posterior probability, rupee impact
  │
S5 rank and group ── false-discovery control, roll-up to the explaining node, top-K, lifecycle
  │
S6 diagnose ── existing diagnosis engine on each material finding (what / where / why)
  │
S7 recommend ── playbooks matched to the finding, with impact and confidence
  │
S8 deliver ── alert, daily digest, Ops UI, agent tool
  │
S9 feedback ── human labels (useful / noise / wrong action) → tuning and calibration reports
```

### S0 Trust gate
Rules, not statistics. Per source: `data_as_of` within its SLA, expected hours present (no gaps), no duplicate keys, row counts within a loose band of the prior week. A failed gate produces a **data finding** and marks every dependent business series "untrusted", so a pipeline outage is never reported as a sales drop. Non-final hours (`is_final = 0`) are either excluded from scoring or scored with a widened band.

### S1 Series store
Every node of the entity tree stores **additive parts** per hour or day: spend, impressions, clicks, orders, revenue, COGS, returns. Ratios (CTR = clicks / impressions, CPC, CAC, ROAS, CVR, margin) are always computed from summed parts, so a parent equals the sum of its children and any ratio can be rolled up exactly. Trees: brand > channel > platform > campaign > ad set > ad; brand > product type > product > SKU; brand > geo (country, state, city); brand > cohort (new/repeat, payment bucket).

Metric names, parts, formulas and directions come from the catalogue and a config file. **No metric name or keyword is hardcoded, and no regex is used in new code** (standing owner rule).

### S2 Expected model
Per node, per hour of week (IST):

* Counts (orders, clicks, impressions): negative-binomial GLM with log link: `log mu = level + weekday x hour profile + event flags`. `level` is a slowly updated EWMA over about 14 days. Event flags (sale days, festival) come from a calendar, not guesses.
* Money (spend, revenue): same GLM on a gamma or log-normal family, or on counts of units times average price.
* Rates (CTR, CVR, return rate): beta-binomial on the numerator and denominator parts.
* Partial pooling: a thin node's profile and level are shrunk toward its parent's: `w = n / (n + k)`, with `k` estimated by empirical Bayes. An ad with 20 impressions behaves like its ad set; an ad with 20,000 behaves like itself.
* Intraday: expected cumulative share by hour `F(h)` from history; end-of-day projection = `observed_cumulative / F(h)`, with a Poisson-Gamma interval that is wide early in the day and tight late.
* Forecast engine (optional upgrade): the existing Chronos service for series with enough history, scored by the existing forecasting backtest.

### S3 Detectors
* **Spike (hour):** tail probability of the observed value under the expected distribution (`p`), converted to a signed score. Randomised-quantile residuals make counts and rates comparable.
* **Level shift (hours to days):** CUSUM on standardised residuals: `S_t = max(0, S_{t-1} + z_t - k)`, alarm when `S_t > h`. `k` and `h` are set from a target in-control average run length, tuned by backtest.
* **Trend (3-7 days):** Mann-Kendall test with autocorrelation correction on weekday-adjusted daily residuals, plus the Theil-Sen slope and its confidence interval.
* **Changepoint (2-4 weeks):** PELT with a BIC penalty (or Bayesian online changepoint), minimum segment length 5 days.
* **Pacing (month vs plan):** month-to-date plus Monte Carlo of the remaining days from the S2 model, giving the probability of finishing below the floor or target.
* Direction (which way is bad) comes from the metric definition, not a global default: a metric without a direction is watched, never alerted.

### S4 Gap layer
* **Gap to expected** says "something changed": standardised residual and its probability.
* **Gap to band** says "it matters": position against `floor <= target <= ceiling` for the brand (D9). Severity: beyond the floor (or ceiling) is critical; between floor and target is a warning; at or better than target is informational.
* **Inheritance (D11):** an entity with no target takes its parent's. Its share of the parent's gap is the part-wise contribution (additive parts make this exact). With no target above it, only the gap to expected applies.
* **Rupee impact:** expected profit difference over a stated horizon (default: 7-day run-rate), computed from the part-wise gap times unit contribution, with an interval. Always reported as a range with its basis.
* **Confidence:** posterior probability that the true value is beyond the expected band (or beyond the floor), not a bare z-score. Low-volume entities therefore stay quiet automatically.

### S5 Rank, group, limit
* Score = rupee impact x confidence. Rank descending.
* **False-discovery control** across the thousands of series per run: Benjamini-Hochberg at a configured `q`; because series are correlated through the tree, use the hierarchical procedure (or Benjamini-Yekutieli) rather than plain BH.
* **Roll-up:** report the **highest node that explains the move**. If a campaign is down and 8 of its 10 ads are down in proportion, the finding is the campaign; the ads appear as evidence, not as 8 more alerts.
* **Alert budget:** at most K new findings per day per brand (configurable), each carrying its rank.
* **Lifecycle:** open > ongoing > resolved with hysteresis, so a flapping series does not re-alert. A finding's identity is `(entity, metric, detector, window)`.

### S6 Diagnose
Each finding above a materiality cut-off runs the existing diagnosis engine (event real or noise, exact decomposition, where, why with DoWhy evidence grades). The alert carries that result. The engine is reused as is; only its trigger changes (a finding instead of a user question).

### S7 Recommend (D8, D12)
A playbook is a pattern over the finding and its diagnosis, an action, evidence, an impact range and a confidence. A human decides.

| Family | Typical trigger (maths form, not a fixed number) | Suggested action | Impact basis |
|---|---|---|---|
| Budget | Entity's efficiency posterior is beyond the floor with high probability for N days, while a sibling's is better with high probability | Reduce / increase / shift budget between siblings | Pooled response curve of spend to outcome per campaign; credible interval |
| Creative | CTR down and frequency up at stable spend (fatigue), or a loser ad with a posterior far below its ad set | Rotate, refresh or pause the creative | Ad's share of the ad set gap |
| Product-side | Sessions and add-to-carts steady, conversion or orders down for a product; margin below floor; stock below cover | Check stock, price, promo, PDP | Product's contribution gap |
| Tracking / data | Tracker double-fire, attribution coverage drop, source stale (S0) | Fix the tracker or the pipeline | Share of the metric affected |

Limits: an ad-level response curve cannot be fitted from 2 attributed orders. Budget recommendations are made at campaign (or ad-set) level with a pooled curve; ad-level advice is limited to creative actions. Each recommendation states what it is based on and what it cannot see.

### S8 Deliver
Daily digest and immediate alert (channel to be decided, Q3); a ranked findings feed and entity drill-down in the existing Ops tab; a tool the agent can call ("what moved today"); a config page for north stars, targets, floor/ceiling and alert budget. The current Monitoring page is the base for config.

### S9 Feedback
Each finding can be labelled useful / noise / wrong action. Reports: precision by detector and tier, detection delay, interval coverage, recommendation hit rate. Labels tune `q`, `k`, `h`, K and the playbook triggers. Without labels, tuning rests on backtests only.

## 6. Configuration

One file (and a UI over it), validated at the boundary, versioned.

```yaml
north_stars:                 # D10 defaults; list is editable
  - id: <catalogue metric id>
    direction_bad: down | up | both   # explicit, never defaulted
    level: brand             # D9: targets are per brand
    floor: <number>          # D9
    target: <number>
    ceiling: <number>
    seed: history | owner    # how the number was set
detection:
  fdr_q: 0.10
  daily_alert_budget_per_brand: <K>
  min_history_days: 28
  untrusted_data_policy: suppress
```

Default values: no target numbers have been supplied. Proposal (needs owner approval, Q2): seed floor / target / ceiling from the trailing 8 weeks of daily values (10th percentile / median / 90th percentile), mark them `seed: history` and show them as "auto-seeded, unconfirmed" until the owner edits them.

## 7. Phases

Each phase ends with a demo on real data and a written result. Nothing is deployed until the owner says so.

**V1 (targets only, brand 20) comes first and replaces the order below until it is done:**

| Step | Work | Exit criteria |
|---|---|---|
| V1.0 | Settle the open data items (IST vs UTC, finality window, ROAS source). Apply `monitor` schema once an admin runs it. Load seeded targets (`02_...`), re-seeding ratios from rolling 7-day pooled values | `monitor.targets` holds the agreed rows; seeds reproduce the daily totals in `ssot_pnl_daily` |
| V1.1 | Evaluate every north star daily against the band; trust gate S0; lump-return and ads-off episodes reported as data events | Replay of 2026-09-07 to 2026-11-02: the 09-15 to 09-22 drop and the 09-28 lump are found; no other critical finding that the owner calls wrong |
| V1.2 | Hourly intraday pace and end-of-day projection for orders, sales, spend, sessions | 90 percent intervals contain about 90 percent of actual end-of-day totals on the last 8 weeks |
| V1.3 | Breach drill-down: part-wise gap split to campaign, ad set, ad, product; diagnosis engine; playbook recommendation with impact range | For each replayed breach, the owner marks the named drivers and the suggested action useful or not |
| V1.4 | Deliver: digest and alert, Ops feed, agent tool, target editor. Two weeks shadow with labels | Precision and delay reported; owner signs off |

The remaining phases are the full entity scan and are deferred:

| Phase | Work | Exit criteria |
|---|---|---|
| P0 Foundation | Verify the open data items (feasibility doc, "verify before building"). Build a read-only history extract from `serve.*` to a local store. Entity tree and parts schema. Config schema. Backtest harness with synthetic anomaly injection (spike, level shift, ramp, dropout) | Extract reproduces known daily totals from the P&L and orders views; harness runs end to end |
| P1 Tier A brand pulse | S2 for brand hourly orders, sessions, spend, funnel. Intraday cumulative curve and end-of-day projection. CUSUM | On the last 90 days: 90 percent intervals contain about 90 percent of actuals; injected shifts found within a stated delay; false findings per week under a stated budget |
| P2 Tier B delivery entities | Hierarchical pooling, BH/hierarchical FDR, roll-up, top-K, lifecycle, S0 gate | Injected campaign-level and ad-level changes found and rolled up correctly; no alert on entities under the volume floor |
| P3 Tier C outcome entities | Pooled posterior rates for ad, ad set, campaign, product, state, cohort; targets and gap layer; trend, changepoint and pacing detectors; rupee impact | Backtest on known past events (owner supplies a list, Q5); impact numbers reconcile to the P&L views |
| P4 Diagnose + recommend | Wire findings to the diagnosis engine; playbooks with impact ranges | For a sample of findings, the owner marks the diagnosis and the recommendation useful or not |
| P5 Deliver and shadow | Digest, alert, Ops feed, agent tool, config UI. Shadow mode: findings are produced and labelled but do not page anyone | Two weeks of labelled output; precision and delay reported |
| P6 Switch over | Replace the old domain-health monitoring. Keep infra alerting (Prometheus) separate; its data-quality probes feed S0 | Owner sign-off; old path removed |

## 8. Where it lives (proposal, Q4)

* A new package in Seleric_Agent beside the existing services, replacing `services/domain_health` and `services/business_state` at P6. Reuse: the diagnosis engine, the forecasting module and Chronos service, the catalogue/MCP access, the Ops tab and Monitoring page.
* Reads: direct read-only SQL on `serve.*` (about 2-4 thousand series per hour is too many for one-query-per-metric through the semantic layer, which is what the current monitor does).
* Writes: **Postgres only** (owner decision 2026-11-03): schema `monitor` in the Agent's own Postgres, created by migrations 016 and 017. ClickHouse is read-only here. See `04_V1_PROGRESS.md`.

## 9. Acceptance and tests

* Unit tests for every formula with known answers (NB interval coverage, CUSUM run length, BH on simulated nulls, shrinkage weights).
* Property tests: parent equals sum of children after every transform.
* Backtest report per phase (coverage, delay, false findings per week, calibration plot).
* Live re-run of a real question and a cross-check of numbers against ClickHouse before declaring any phase done (standing owner rule).

## 10. Risks and honest limits

1. **Small numbers.** One brand has orders (about 45 per day). Hourly order alerts will be weak; the system will mostly project end-of-day and flag large moves. This is a data limit, not a modelling one.
2. **Attribution.** Only about 46 percent of orders carry an ad id, so ad-level outcome metrics reflect the attributed share. Findings state their coverage.
3. **Settling data.** COGS and returns arrive late. Recent profit days are provisional; profit findings carry that flag and are re-scored when the day settles.
4. **Time zone.** IST versus UTC labels on order timestamps must be settled in P0 or every hour profile is shifted.
5. **Cold start.** Meta hourly begins 2025-09-22 (about 13 months), Google earlier. Seasonality across a full year (festivals) cannot be learned from one cycle; event flags must be supplied.
6. **Multiple testing.** Without FDR control, thousands of hourly tests produce false findings every run. This is why S5 is not optional.
7. **Recommendations can be wrong.** They stay human-decided (D8); labels measure the hit rate before any automation is discussed.
8. **Staging cost.** The history extract and per-run compute will load ClickHouse; the first run is sized and timed in P0 before any schedule is set.

## 11. Open questions for the owner

* **Q1.** Accept that entity-level outcomes (orders, CAC, ROAS, profit, returns) are daily or multi-day, while hourly covers delivery and the brand pulse?
* **Q2.** Seed the floor / target / ceiling from the trailing 8 weeks (10th / median / 90th percentile) until the owner sets real numbers?
* **Q3.** Where do alerts go (email list, Slack, in-app only), and what is the daily alert budget K per brand?
* **Q4.** Approve a new `monitor` schema and a writer account in ClickHouse (or choose Postgres), and the package location above?
* **Q5.** Provide 5 to 10 past events you remember (a campaign that fell off, a tracker break, a stock-out, a sale spike) with dates, to use as backtest truth.
* **Q6.** Which brands are in scope for the first release (brand 20 only has orders; 28 is paid-media only)?
* **Q7.** Is the contribution-margin definition for rupee impact the one in the P&L views (`ssot_pnl_daily`), including returns and RTO on the event-date rule?

### Status on 2026-11-03

| Q | Status |
|---|---|
| Q1 | Not answered directly; V1 stays inside it (daily outcomes, hourly pace only for volume metrics) |
| Q2 | Answered: seed from history, adjustable (`02_TARGET_SEEDS_BRAND20.md`) |
| Q3 | Open: where alerts go and how many per day |
| Q4 | "yes add monitor": schema prepared, not applied, needs an admin login (confirm meaning) |
| Q5 | Answered: no past events |
| Q6 | Answered: brand 20 |
| Q7 | Open |
| Q8 (new) | Does "specific targets only" mean V1 monitors only the north-star targets, with entity drill-down only on a breach? |
| Q9 (new) | What happened 2026-09-15 to 09-22 (both platforms at zero spend)? And what are the 09-28, 10-13, 10-15 and 10-30 bulk return closures? Both change what counts as normal history |
| Q10 (new) | Net profit: history says the typical day loses money. Is break-even (0) the right placeholder target until the owner sets one? |
