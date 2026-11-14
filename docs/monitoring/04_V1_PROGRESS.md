# V1 progress (brand 20, targets only)

Last updated 2026-11-03 (second pass). Nothing here is committed. The database part is live, and the runner has been run by hand inside the `business-state-cron` container; nothing runs on a schedule yet.

## Decisions added 2026-11-03

| # | Owner's words | Applied as |
|---|---|---|
| D13 | "create in postgres docker Seleric agent, and go in" | The `monitor` schema lives in the Agent's Postgres (`seleric_agent-postgres-1`, database `seleric_swarm`). The earlier ClickHouse SQL (`03_monitor_schema.sql`) is superseded |
| D17 | "use postgres for all database needs for now" | Every table this system writes goes in Postgres. ClickHouse is read-only: the source of the metrics, reached through the semantic layer |
| D18 | Source: "Semantic layer (Recommended)" | Metrics are the semantic layer's `ssot_*` metrics (the SSOT P&L view, Shopify + Amazon). Legacy `net_sales`, `net_profit`, `gross_profit` are marked deprecated there and are not used |
| D20 | "YES DELIBERATE, GO ON" (the ads-off period 2026-09-14 to 2026-09-22) | The pause was a business decision. Registered in `monitor.known_periods` (kind `ads_paused`); breaches inside it show as information with the reason, not as alerts. The period is outside the seed window, so the targets are unaffected. "Go on" read as: wire the evaluator to live data |
| D21 | "every hour, app no limit, let breakeven at 0 now, 18 return rate" | Runs every hour (at 5 past); findings are shown in the app with no cap; net profit and net margin targets stay at break-even 0 (stored as owner-set versions); return-rate ceiling set to 0.18 (read as 18%, see open items) |
| D19 | Date axis: "Event date (current default)" | Moot: the `ssot_*` metrics carry one built-in hybrid axis (placement on order date, returns and cancels on their event dates, D-07). No `date_axis` is passed |

## What exists now

* **Postgres schema `monitor`** (migrations `016_monitor_schema.sql`, `017_monitor_metric_kinds.sql`, applied through the repo's own `seleric-migrate`; recorded in `schema_migrations`): `metrics`, `target_versions` (append-only history) with view `targets_current`, `evaluations`, `findings`, `finding_events`, `labels`.
* **12 metrics and 12 seeded targets for brand 20** in `monitor.metrics` / `monitor.target_versions`: orders, net sales, gross profit, net profit, ad spend (day); gross margin, net margin, gross ROAS, net ROAS, CAC, conversion rate, return rate (trailing 7-day pooled). Values: see the table in `02_TARGET_SEEDS_BRAND20.md` for the volume metrics; the ratio metrics were re-seeded on pooled 7-day windows (below).
* **Code:** `src/seleric_swarm/services/monitoring/evaluate.py` (pure; no I/O, no metric names), `config/monitoring.yaml` (trust-gate thresholds), `tests/unit/test_monitoring_evaluate.py` (22 tests, passing, lint clean).

### Targets as loaded (floor / target / ceiling)

| Metric id | Floor | Target | Ceiling | Bad direction |
|---|---|---|---|---|
| `ssot_orders` | 31 | 50 | 74 | down |
| `ssot_net_sales` | 50,572 | 87,187 | 127,936 | down |
| `ssot_gross_profit` | 30,425 | 51,499 | 75,835 | down |
| `ssot_net_profit` | -15,951 | **0 (placeholder)** | 10,414 | down |
| `ssot_ad_spend` | 42,732 | 53,057 | 79,807 | both |
| `gross_margin` | 0.5552 | 0.5732 | 0.5864 | down |
| `net_margin` | -0.1873 | **0 (placeholder)** | 0.0111 | down |
| `gross_roas_pooled` | 1.48 | 1.60 | 1.84 | down |
| `net_roas_pooled` | 0.75 | 0.87 | 1.02 | down |
| `cac_pooled` | 1,680 | 2,008 | 2,368 | up |
| `conversion_rate` | 0.0080 | 0.0104 | 0.0171 | down |
| `return_rate_pooled` | 0.0228 | 0.0408 | 0.0473 | up |

Derived from the 32 clean days of 2026-09-29 to 2026-11-02. Ratios use trailing-7-day pooled windows (29 overlapping windows, about 4-5 independent weeks). For a one-way metric the good side of the band is reported as information, never as a breach.

## Replay on the real history (2026-09-14 to 2026-11-02, read-only)

Series pulled from the semantic layer for brand 20, targets read from Postgres, evaluator run for each day.

* **Data events found:** four bulk-return days (09-28: 1,077,233 closed returns on 67,302 gross sales; 10-13; 10-15; 10-30), set aside as untrusted for the settling-money metrics. **Ads off 2026-09-14 to 2026-09-22** (spend 1.2k-10.5k a day against a 66.4k baseline) reported as its own event; business metrics stay trusted because the fall in orders is real.
* **Business breaches on those days:** orders, net sales, gross profit and ad spend breached the floor on the ads-off days, as they should.
* **Noise check:** in the 29 normal days (2026-10-05 to 2026-11-02), 10 days had at least one critical finding, 25 in total. They cluster in the week of 10-11 to 10-17, which is a visibly weak week in the data (orders 26 on 10-11; the 10-13 and 10-15 return lumps), not scattered noise.
* **Noisiest metrics** (to review with the owner): `return_rate_pooled` (14 critical days over the whole replay, band is tight at 4.08%-4.73% because pooled windows overlap), `net_margin` (below target every day because the placeholder target is 0 and the business ran at a loss), `ssot_ad_spend` (22 warnings: two-sided band on a series that trends up), `cac_pooled` (19 days below its floor flag).
* Two defects found and fixed during the replay: the ads-off detector's baseline was contaminated by the outage itself; a two-sided metric (spend) raised criticals. Spend breaches are now warnings.

## What is not done

* **Runner** (`services/monitoring/runner.py`): fetches the series through the semantic layer, evaluates every enabled metric for yesterday (IST), writes `monitor.evaluations`, and keeps `monitor.findings` in step (open, ongoing, reopen, resolve, each with a `finding_events` row). First live run, brand 20, as of 2026-11-02: 12 evaluations; 3 warnings (gross margin 0.5662 vs target 0.5732, return rate 4.11% vs target 4.08%, ad spend 98,359 vs ceiling 79,807; the first two provisional); 6 informational (orders 95, net sales 211,863 and others above their ceiling, a very strong day); 0 critical. A second run produced 4 updates and no duplicate findings.
* **Scheduled hourly (2026-11-03).** New compose service `monitoring-cron` in `docker-compose.yml`: reuses the `business-state-cron` image, mounts `services/monitoring` and `config/monitoring.yaml` read-only, runs the runner for brand 20 at 5 past every hour. No existing service was touched or restarted. Undo: `docker compose rm -sf monitoring-cron` and delete the service block. Each hourly run re-judges **yesterday** (IST) as late data arrives; judging today so far is the intraday pace, not built yet.
* **App delivery:** `GET /v1/monitoring/targets` (open findings worst first, resolved kept apart, latest value against its band for every metric, known periods) is written, tested and checked against the live database, but **not deployed**: the api image must be rebuilt to serve it. The Ops-tab panel that draws it is not written. No target editor yet; `monitor.known_periods` and targets are edited by SQL for now.
* Intraday pace (V1.2), breach drill-down and recommendations (V1.3), delivery and the target editor (V1.4).
* `p_beyond` (probability the true value is beyond the line) is not computed yet; findings are deterministic position against the band.
* `settles_after_days` is a guess (7 for net sales, profit and margins, 14 for return rate) and must be measured from how long returns keep arriving.

## State of the repo and the running services

* The three migration files (016, 017, 018) and the new code are **uncommitted** in the live checkout (branch `monitoring`, which already carries other uncommitted work). Nothing was committed.
* The migrations were applied by copying them into the running `api` container and running `seleric-migrate`. The container's copy disappears if it is recreated; the repo copy is what a rebuild uses. The runner ignores applied migrations whose file is absent, so this is safe.
* Live rows in `monitor`: 12 metrics, 12 target versions, 1 known period, 24 evaluations and 4 findings from the two manual runs (brand 20, as of 2026-11-02).
* The runner module and `config/monitoring.yaml` were also copied by hand into the running `business-state-cron` container (its Python package folder and `/app/config`) for those runs. That copy is lost if the container is recreated; it does not change how the container behaves.
* The throwaway scripts used for the probe and the replay are in the session scratchpad, not in the repo.
