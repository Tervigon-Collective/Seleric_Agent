# Business State Ready Store — KPI Curation & Implementation Plan

## Problem

High-level queries ("How is the business doing?" "What are our key metrics?") currently trigger the full agent loop → MCP → Cube path, requiring many sequential tool calls, each with network latency. A single high-level question can burn 10-50+ tool calls just to aggregate data.

## Solution

A **read-optimized, pre-aggregated data store** refreshed hourly that can be directly queried without going through the agent loop or Cube.

```
┌─────────────────────────────────────────────────────────┐
│                    Refresh Pipeline                       │
│  (Cron / Scheduled Worker)                               │
│                                                          │
│  Cube MCP ──► Pre-Aggregator ──► Business State Store    │
│  (metrics_query)   (compute features,   (Postgres JSONB) │
│                     anomalies, deltas,                   │
│                     rankings)                             │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│                  Query Fast Path                          │
│                                                          │
│  User: "How's the business?"                             │
│       │                                                  │
│       ▼                                                  │
│  Intent Classifier ──► HIGH_LEVEL_LOOKUP                 │
│       │                                                  │
│       ▼                                                  │
│  BusinessStateStore.get_snapshot(domain, as_of)          │
│       │  (single read, no agent loop)                    │
│       ▼                                                  │
│  LLM Answer Formatter (fast model tier)                  │
│       │  (receives structured snapshot + user question)   │
│       ▼                                                  │
│  Natural language answer with embedded metrics            │
└─────────────────────────────────────────────────────────┘
```

### Latency Comparison

| Path | Tool Calls | Typical Latency |
|------|-----------|-----------------|
| **Current** (agent loop → MCP → Cube) | 10-50+ | 30-120s |
| **Fast path** (snapshot → LLM format) | 0 (pre-computed) + 1 LLM call | 2-5s |

---

## Curated KPI List

### 1. Commerce Domain (Revenue & Orders)

| KPI | Metric ID | Grain | Features to Pre-Compute |
|-----|-----------|-------|------------------------|
| **Net Sales** | `metric.net_sales` | day | current_value, period_delta_pct, rolling_mean_7d, rolling_std_7d, freshness_age |
| **Gross Sales** | `metric.gross_sales` | day | current_value, period_delta_pct, rolling_mean_7d, freshness_age |
| **Orders** | `metric.orders` | day | current_value, period_delta_pct, rolling_mean_7d, freshness_age |
| **Returns/Cancels** | `metric.returns_cancels` | day | current_value, period_delta_pct, freshness_age |
| **Refunded Amount** | `metric.refunded_amount_excl_tax` | day | current_value, period_delta_pct, freshness_age |
| **Cancelled Orders** | `metric.cancelled_orders` | day | current_value, period_delta_pct, freshness_age |
| **New Customer Orders** | `metric.new_customer_orders` | day | current_value, period_delta_pct, freshness_age |
| **Total Sales (pre-discount)** | `metric.total_sales` | day | current_value, period_delta_pct, freshness_age |
| **Discounts** | `metric.discounts` | day | current_value, period_delta_pct, freshness_age |

### 2. Finance Domain (P&L)

| KPI | Metric ID | Grain | Features to Pre-Compute |
|-----|-----------|-------|------------------------|
| **Net Profit (All Channels)** | `metric.net_profit` | day | current_value, period_delta_pct, rolling_mean_7d, freshness_age |
| **Net Profit (Shopify)** | `metric.net_profit_shopify` | day | current_value, period_delta_pct, freshness_age |
| **MER** | `metric.mer` | day | current_value, period_delta_pct, rolling_mean_7d, freshness_age |
| **Gross Margin %** | `metric.gross_margin_pct` | day | current_value, period_delta_pct, freshness_age |
| **Contribution Margin** | `metric.contribution_margin` | day | current_value, period_delta_pct, freshness_age |
| **Contribution Margin %** | `metric.contribution_margin_pct` | day | current_value, period_delta_pct, freshness_age |
| **Net Margin %** | `metric.net_margin_pct` | day | current_value, period_delta_pct, freshness_age |
| **RTO Cost** | `metric.rto_cost` | day | current_value, period_delta_pct, freshness_age |
| **Operating Cost** | `metric.operating_cost` | day | current_value, period_delta_pct, freshness_age |
| **Net COGS** | `metric.net_cogs` | day | current_value, period_delta_pct, freshness_age |
| **Shipping Cost** | `metric.shipping_cost` | day | current_value, period_delta_pct, freshness_age |
| **Packaging Cost** | `metric.packaging_cost` | day | current_value, period_delta_pct, freshness_age |
| **Payment Gateway Fees** | `metric.payment_gateway_fees` | day | current_value, period_delta_pct, freshness_age |
| **Gross Profit** | `metric.gross_profit` | day | current_value, period_delta_pct, freshness_age |

### 3. Performance Domain (Paid Media)

| KPI | Metric ID | Grain | Features to Pre-Compute |
|-----|-----------|-------|------------------------|
| **Total Ad Spend** | `metric.spend` | day | current_value, period_delta_pct, rolling_mean_7d, freshness_age |
| **Net ROAS** | `metric.net_roas` | day | current_value, period_delta_pct, rolling_mean_7d, freshness_age |
| **Gross ROAS** | `metric.gross_roas` | day | current_value, period_delta_pct, rolling_mean_7d, freshness_age |
| **CAC** | `metric.cac` | day | current_value, rolling_mean_7d, rolling_std_7d, freshness_age |
| **CPM (Meta)** | `metric.cpm` | day | current_value, period_delta_pct, freshness_age |
| **CPC (Meta)** | `metric.cpc` | day | current_value, period_delta_pct, freshness_age |
| **CTR (Meta)** | `metric.ctr` | day | current_value, period_delta_pct, freshness_age |
| **Meta Spend** | `metric.meta_spend` | day | current_value, period_delta_pct, freshness_age |
| **Google Spend** | `metric.google_spend` | day | current_value, period_delta_pct, freshness_age |
| **Meta Impressions** | `metric.meta_impressions` | day | current_value, period_delta_pct, freshness_age |
| **Google Impressions** | `metric.google_impressions` | day | current_value, period_delta_pct, freshness_age |
| **Meta Clicks** | `metric.meta_clicks` | day | current_value, period_delta_pct, freshness_age |
| **Google Clicks** | `metric.google_clicks` | day | current_value, period_delta_pct, freshness_age |
| **Google CTR** | `metric.google_ctr` | day | current_value, period_delta_pct, freshness_age |
| **Google CPC** | `metric.google_cpc` | day | current_value, period_delta_pct, freshness_age |
| **Google CPM** | `metric.google_cpm` | day | current_value, period_delta_pct, freshness_age |
| **Meta Video Completion Rate** | `metric.meta_video_completion_rate` | day | current_value, period_delta_pct, freshness_age |
| **Meta Landing Page Views** | `metric.meta_landing_page_views` | day | current_value, period_delta_pct, freshness_age |
| **Meta Cost per Landing Page View** | `metric.meta_cost_per_landing_page_view` | day | current_value, period_delta_pct, freshness_age |

### 4. Attribution Domain

| KPI | Metric ID | Grain | Features to Pre-Compute |
|-----|-----------|-------|------------------------|
| **Attributed Net Revenue** | `metric.attributed_net_revenue` | day | current_value, period_delta_pct, rolling_mean_7d, freshness_age |
| **Attributed Gross Revenue** | `metric.attributed_gross_revenue` | day | current_value, period_delta_pct, freshness_age |
| **Attributed Refund Amount** | `metric.attributed_refund_amount` | day | current_value, period_delta_pct, freshness_age |
| **Attributed Orders** | `metric.attributed_orders` | day | current_value, period_delta_pct, freshness_age |
| **Attributed New Customer Orders** | `metric.attributed_new_customer_orders` | day | current_value, period_delta_pct, freshness_age |
| **Attributed AOV** | `metric.attributed_aov` | day | current_value, period_delta_pct, freshness_age |
| **Meta Attributed Orders** | `metric.meta_attr_orders` | day | current_value, period_delta_pct, freshness_age |
| **Meta Attributed Net Revenue** | `metric.meta_attr_net_revenue` | day | current_value, period_delta_pct, freshness_age |
| **Meta Attributed Gross Revenue** | `metric.meta_attr_gross_revenue` | day | current_value, period_delta_pct, freshness_age |
| **Meta Attributed AOV** | `metric.meta_attr_aov` | day | current_value, period_delta_pct, freshness_age |

### 5. Funnel Domain (Website)

| KPI | Metric ID | Grain | Features to Pre-Compute |
|-----|-----------|-------|------------------------|
| **Sessions** | `metric.sessions` | day | current_value, period_delta_pct, rolling_mean_7d, freshness_age |
| **Purchase CVR** | `metric.purchase_cvr` | day | current_value, rolling_mean_7d, rolling_std_7d, freshness_age |
| **Checkout Rate** | `metric.checkout_rate` | day | current_value, period_delta_pct, freshness_age |
| **ATC Rate** | `metric.atc_rate` | day | current_value, period_delta_pct, freshness_age |
| **PDP View Rate** | `metric.pdp_view_rate` | day | current_value, period_delta_pct, freshness_age |
| **ATC to Purchase Rate** | `metric.atc_to_purchase_rate` | day | current_value, period_delta_pct, freshness_age |
| **Bounce Rate** | `metric.bounce_rate` | day | current_value, period_delta_pct, freshness_age |
| **Funnel Conversion Rate** | `metric.funnel_conversion_rate` | day | current_value, period_delta_pct, freshness_age |
| **Funnel Purchases** | `metric.funnel_purchases` | day | current_value, period_delta_pct, freshness_age |

### 6. Product Domain

| KPI | Metric ID | Grain | Features to Pre-Compute |
|-----|-----------|-------|------------------------|
| **Product Net Revenue** | `metric.product_net_revenue` | day | current_value, period_delta_pct, rolling_mean_7d, freshness_age |
| **Product Gross Margin %** | `metric.product_gross_margin_pct` | day | current_value, period_delta_pct, freshness_age |
| **Units Sold** | `metric.units_sold` | day | current_value, period_delta_pct, freshness_age |
| **Product Orders** | `metric.product_orders` | day | current_value, period_delta_pct, freshness_age |

### 7. Customer Domain

| KPI | Metric ID | Grain | Features to Pre-Compute |
|-----|-----------|-------|------------------------|
| **Repeat Rate** | `metric.repeat_rate` | day | current_value, period_delta_pct (via snapshot diff), freshness_age |
| **Customers** | `metric.customers` | day | current_value, period_delta_pct, freshness_age |
| **Repeat Customers** | `metric.repeat_customers` | day | current_value, period_delta_pct, freshness_age |
| **First Orders** | `metric.first_orders` | day | current_value, period_delta_pct, freshness_age |

---

## Pre-Computed Feature Set (Per Metric)

| Feature | Strategy | Window | Description |
|---------|----------|--------|-------------|
| `current_value` | `current_value` | — | Latest daily value |
| `period_delta_pct` | `period_delta_pct` | `compare` | WoW / DoD % change |
| `rolling_mean_7d` | `rolling_mean` | `7d` | 7-day rolling average |
| `rolling_std_7d` | `rolling_std` | `7d` | 7-day rolling std dev |
| `freshness_age` | `freshness_age` | — | Hours since Cube last refresh |

---

## Pre-Computed Anomaly (Per Metric)

| Field | Source |
|-------|--------|
| `observed` | Latest value |
| `expected` | Median of 28-day history |
| `expected_range` | [median - 3*MAD, median + 3*MAD] |
| `deviation_pct` | % from median |
| `score` | Robust z-score |
| `direction` | up/down/flat |
| `is_anomaly` | abs(z) >= 3.0 |
| `adverse` | direction == direction_bad |

---

## Cross-Domain Headline Metrics (Top 10)

These are the **10 metrics** that should always be in the ready store for instant answers to "How's the business?":

1. `metric.net_sales` — Commerce revenue
2. `metric.net_profit` — Bottom line
3. `metric.mer` — Marketing efficiency
4. `metric.net_roas` — Paid media efficiency
5. `metric.spend` — Media investment
6. `metric.cac` — Acquisition cost
7. `metric.orders` — Volume
8. `metric.purchase_cvr` — Conversion health
9. `metric.repeat_rate` — Retention
10. `metric.contribution_margin_pct` — Unit economics

---

## Implementation Plan

### Phase 1: Foundation

- [ ] Extend `SnapshotStore` → `BusinessStateSnapshotStore` (Postgres JSONB)
- [ ] Define `BusinessStateSnapshot` Pydantic model (extends `DomainStateSnapshot`)
- [ ] Add `BusinessStateRefresher` service (`services/business_state/refresh.py`)
  - Fetches all 60+ metrics via MCP in parallel
  - Computes features (reuse `features.py`)
  - Detects anomalies (reuse `detectors.py`)
  - Writes snapshot per `as_of` date
  - **Refresh schedule**: Hourly for all metrics that have data available in Cube

### Phase 2: KPI Curation & Config

- [ ] Create `config/business_state_kpis.yaml` with:
  - All 60+ KPIs above organized by domain
  - Feature specs per metric (which of the 5 features to compute)
  - Headline metric list (top 10)
  - Refresh schedule per domain (hourly/daily)
- [ ] Update `business_state_profiles.yaml` to include all KPIs

### Phase 3: Fast-Path API

- [ ] Add `/v1/business-state` endpoint (bypasses agent loop)
- [ ] Implement `BusinessStateFormatter` (`services/business_state/formatter.py`)
  - Selects relevant metrics from snapshot based on question
  - Calls fast-tier LLM with structured context
- [ ] Add intent check in `run_v3_mission()` for `HIGH_LEVEL_LOOKUP`
- [ ] Fallback to agent loop if snapshot stale/missing

### Phase 4: Advanced Features

- [ ] Top Movers computation (biggest `period_delta_pct`)
- [ ] Active Anomalies aggregation (all `is_anomaly=true`)
- [ ] Cross-metric signals (revenue up but MER down = margin pressure)
- [ ] Pre-computed text summary for each domain
- [ ] Incremental refresh (only changed metrics)

### Phase 5: Observability & Polish

- [ ] Freshness SLA monitoring (alert if snapshot > 2h stale)
- [ ] Refresh duration metrics
- [ ] Snapshot versioning / rollback
- [ ] Integration tests for fast path

---

## Existing Components to Reuse

| Existing Component | How It Reuses |
|---|---|
| `business_state/features.py` | Feature computation — called by refresher |
| `business_state/detectors.py` | Anomaly detection — called by refresher |
| `business_state/facade.py` | `BusinessStateService.get_metric_state()` — called per-metric by refresher |
| `domain_health/models.py` | `DomainStateSnapshot` — extended into `BusinessStateSnapshot` |
| `domain_health/snapshot_store.py` | `SnapshotStore` — extended for business state |
| `domain_health/resolver.py` | Domain resolution — reused |
| `services/mcp_query.py` | `call_metrics_query()` — called by refresher |
| `agent/intent.py` | Intent classification — add `HIGH_LEVEL` intent |
| `agent/model.py` | Fast-tier model — used by formatter |

---

## Key Design Decisions

1. **Cube remains the source of truth** — the refresher reads from Cube via MCP, same as today. No direct SQL.
2. **Evidence chain preserved** — each pre-computed value retains its `provenance` (source query, timestamp, metric version).
3. **Graceful degradation** — if the snapshot is stale or missing, fall back to the existing agent-loop path.
4. **No new infrastructure** — reuses existing Postgres, existing MCP gateway, existing feature/detector code.

---

## Open Questions

1. **Scope**: Start with the **top 10 headline metrics** only, or all 60+ at once?
2. **Fallback**: If snapshot is stale (>2h), should it silently fall back to agent loop, or return "data freshing" message?
