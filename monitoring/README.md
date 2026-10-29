# Monitoring: the Ops tab and the Monitoring page

Two things in the Seleric UI, one backend.

* **Ops tab** (beside Conversations and Office): every domain on one page, scrolled domain by domain. Each selected metric shows its latest value, what was expected for that weekday, 28 days of history and whether it needs attention.
* **Monitoring page** (side rail of the Ops tab, and a link in the conversations sidebar): per domain, choose which catalogue metrics to watch, when each raises an alert, which way is bad, and whether it goes into the **business snapshot** the agent reads for "how is the business doing".

Everything is read from the live metric catalogue through the MCP. Nothing on these pages is sample data.

## How it fits together

```
Monitoring page ──PUT /v1/monitoring/config/{domain}──▶ overrides.json   (shared volume, validated, versioned)
                                                            │ read by
business-state cron (every minute, runs only what is due) ◀─┘
   └─ per metric: MCP series (28 days) ─▶ judge.py ─▶ snapshot per domain ─▶ `business` headline snapshot
Ops tab  ◀──GET /v1/monitoring/state── latest snapshots          agent `business_snapshot` tool ◀── `business`
```

* Defaults live in `config/domain_health_profiles.yaml` (titles, shipped metrics, headline list). Saved choices are *overrides* stored beside the snapshots in `…/domain_health_snapshots/_config/overrides.json`, so the api writes and the cron reads without a redeploy. "Back to default" deletes the override.
* A save immediately resolves that domain, so the page shows real numbers at once; otherwise the cron picks the change up within a minute.
* Limits: 40 metrics per domain (each is one MCP query per refresh), 20 s between manual refreshes of one domain.

## What it does about the first ready store's defects

Found by reading the live snapshots on 2026-10-29. All are fixed or contained in this branch.

| Defect seen in the live `business` / `finance` snapshots | Fix |
|---|---|
| `net_profit_drop … period_delta_pct -448.3%` raised as the headline signal: a percent change off a negative base. `net_margin_pct` read -377%. | Change is stated in the unit of the metric's *shape*: percent for additive, percentage points for shares, the metric's own unit for signed. Shape comes from the catalogue unit and the data (a metric that has gone negative is signed). `period_delta_pct` is null unless the base is positive. |
| `purchase_cvr` flagged `is_anomaly` with direction **up** although down is bad, i.e. good news raised as an anomaly. | An alert needs the move to go the metric's bad way. The other way is shown as "better than usual". |
| Catalogue silently defaults `direction_bad` to "up", so a metric nobody classified was alerted as if "up is bad". | Missing direction means *watch only*, never alert, and the page says so. Obvious cost and creative metrics carry a direction in the YAML; the Monitoring page can set any. |
| Expected range below zero (net sales -12,643 to 125,754; CAC 114 to 4,089) and blind to weekdays. | Same-weekday baseline (trailing fallback), spread measured around each weekday's own median, a 5 % floor so a flat history is not hair-trigger, band clamped at zero for non-negative metrics, whole-event floor for counts. |
| Duplicate tiles under different names: `gross_margin_pct` = `contribution_margin_pct`, `gross_profit` = `contribution_margin`, `net_profit_shopify` = `net_profit`, `funnel_conversion_rate` = `purchase_cvr`, `attributed_net_revenue` / `product_net_revenue` = `net_sales`, `attributed_orders` / `product_orders` = `orders`, `first_orders` = `new_customers`. | Removed from the defaults. A test fails if the shipped config repeats a measure. |
| Value is the last complete day but the label said a 7-day window. | Every tile states "data to <day>"; the series and expected value are per day. |
| The cron resolved all 8 domains about every 2 to 3 minutes (about 100 MCP queries each) although the design says hourly. | `scheduler` runs only domains older than `BUSINESS_STATE_MIN_INTERVAL_S` (default 900) or whose saved config is newer than their snapshot, and decides that *before* building the runtime. |
| Metrics fetched one after another (8 metrics = 35 s). | Bounded parallel fetch (5 at a time per resolver). |
| One young metric with under 15 days of history marked the whole domain DEGRADED. | Short history is its own state ("not enough history"), not a fault. |
| `ALL_DOMAINS` hardcoded in two places, so a new domain would never be resolved or found. | Domains are the keys of the profile file. |
| `business` headline was rebuilt from only the domains resolved in that pass. | Rebuilt from the latest snapshot of every enabled domain. |
| A metric with no row for yesterday (live: `refunded_amount_excl_tax`, last row 10-27) showed its older value under "data to 10-28" and was judged as yesterday's. | The judgement carries the day the value belongs to. If that is not the snapshot day the metric gets no verdict, with the reason "no value for 10-28; the latest is from 10-27" (also passed to the agent). Days without a row are not zero-filled: that is right for sums but wrong for levels such as stock, and the catalogue does not say which is which. |
| MER shown as 152 %. | Display kind: shares as percent, multiples (MER, ROAS) as `1.53x`. |

## Judging a metric (`services/domain_health/judge.py`)

Observed is the latest complete day. Expected is the median of the same weekday over the last 4 weeks (trailing median if there are fewer than 3 such days). Normal range is expected ± 3 spread. Needs 10 prior days.

Per-metric alert rule, chosen on the Monitoring page:

* **Outside its normal range the bad way** (default).
* **Worse than expected by N**: N in percent (counts, amounts), percentage points (shares), or the metric's own unit (signed metrics).

## API (`/v1/monitoring`, API-key auth like the rest of `/v1`)

`GET state` · `GET config` · `GET catalogue/{domain}` · `PUT config/{domain}` (`expected_version` for optimistic concurrency, 409 on conflict) · `DELETE config/{domain}` · `POST refresh/{domain}`

The PUT validates at the boundary: metric ids must exist in the live catalogue and be daily, alert kinds are a fixed list, thresholds finite numbers in (0, 1000], filters and date axes are not user-settable, unknown keys are rejected.

## Verify

```
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_domain_health*.py tests/unit/test_monitoring_*.py tests/unit/test_business_state_*.py
cd office-ui && npx vitest run && npm run build
```

Checked against live data on 2026-10-29 through a separate preview container (own snapshot volume): all 8 domains resolved from the real MCP; yesterday's Meta clicks 1,683, impressions 97,529 (CTR 1.726 %) and sale orders 45 and total sales 124,072 match ClickHouse (`gold.mart_meta_ad_daily_performance`, `serve.fct_order_commerce`). Net sales 102,067.40 matches paid sales incl. tax 120,439.45 ÷ 1.18 = 102,067.33 (assumes a uniform 18 % tax; 7 paise apart). The catalogue's order and net-sales definitions differ from raw `gold.fct_orders` (49 orders, net revenue 71,502, before the sale-order rule and exchange exclusion); the tiles follow the catalogue.

## Deploy (not done yet)

1. Merge this branch into the branch the stack builds from. It touches `office-ui/src/App.tsx`, which also has uncommitted work in the live checkout.
2. Rebuild **both** `seleric_agent-api` (API and UI) and `seleric_agent-business-state-cron` (due logic). Tag rollbacks first (`seleric_agent-api:rollback-…`, `…-business-state-cron:rollback-…`). Old snapshots on the volume still load; new fields are optional.
3. The first cron pass after deploy resolves every domain once (no snapshot has a series yet).

## Known limits

* Undimensioned metrics only. Per-ad hook rate, per-section leak and per-SKU stock cover need dimensional fan-out, which does not exist in the ready store yet.
* Gaps are not filled (see the defect table), so a sparse metric can show "no verdict" on days it has no row.
* The latest complete day only, not today. Intraday would need the hourly metrics.
* Anyone who can load `/ui/` gets the shared API key (existing MVP behaviour, see `main.py` `/ui/config.js`), so anyone who can reach the UI can edit monitoring. Per-user roles are not part of this change.
* The catalogue has no direction for many metrics; the proper fix is `agent_policy.yaml` in Agent Core (not writable from here).
* `ops-panel-mockup.html` is the design mockup (invented sample data).
