# 04 — Domain Health Snapshots (resolved business state, per domain)

**Status:** design / pre-implementation — no code in this doc.

**Validated against live `seleric-mcp` data on 2026-09-14** — see
[06_DATA_VALIDATION_FINDINGS.md](06_DATA_VALIDATION_FINDINGS.md) for the raw
queries. Four corrections below are a direct result of that check, marked
inline as **[validated]**.

## Problem

"How are we doing today?", "what's our status?", "what needs attention?" are
**overview-level** questions. Today every such question would fan out live:
Coordinator → domain agents → MCP → aggregation, all inside the request. That
is too slow and too expensive for a question that doesn't need fresh-second
data — daily/hourly grain metrics don't change between two overview asks
five minutes apart.

**Goal:** a background job (cron) pre-resolves each domain's health into a
small structured snapshot, stored with a timestamp. Overview queries **read**
the latest snapshot(s) and let the LLM narrate; they do not recompute.
Anything the overview snapshot doesn't cover, or that the user asks a
sharper follow-up about, is fetched **live, in parallel**, same as today.

**[2026-09-14 decision] Storage: JSON files for now, Postgres later.** The
snapshot store writes one JSON file per `(domain, as_of)` to disk
(`snapshot_store.py`) instead of a new Postgres table. This is a deliberate
sequencing choice, not a rejection of the original JSONB design — the shape
below (`DomainStateSnapshot`) is unchanged either way, only the write/read
implementation differs, so moving to Postgres later is a `snapshot_store.py`
rewrite behind the same interface, not a schema migration for callers. Move
when there's a real reason to (multi-brand rollout needing cross-snapshot
queries, concurrent-write safety, or an ops need to query snapshot history
that `ls`-ing JSON files can't answer) — not a hard blocker for the Sprint 3
vertical slice.

This is **not** a new metrics engine. It is a consumer of
[`BusinessStateService`](02_SELERIC_AGENT_INTEGRATION.md) (`MetricState`,
freshness, quality flags) plus a thin **resolution layer** that turns N
`MetricState`s into one `DomainStateSnapshot` per domain, on a schedule.

```text
cron ──> DomainStateResolver (per domain) ──> BusinessStateService.get_metric_state() x N
                                           └─> DomainStateSnapshot (JSON file for now) ──> snapshot store
overview query ──> read latest snapshot(s) ──> LLM synthesis ──> answer
                └─(if question needs it)──> live domain-agent call, in parallel, merged in
```

## Reuse — do not rebuild

| Need | Already exists | Reuse as-is |
|---|---|---|
| Certified metric values, freshness, quality flags | `BusinessStateService.get_metric_state` ([01](01_ARCHITECTURE.md), [03](03_DEFINITIONS_TO_MAKE_FUNCTIONAL.md)) | Yes — the snapshot resolver is a caller, like Observer/Anomaly/Prediction |
| Domain → metric ownership | `config/metric_registry.yaml` (`domain:` field per metric) | Yes — snapshot metric set is a filtered view, not a new list |
| Domain → MCP module pin, ontology | `config/agent_registry.yaml` (`seleric_module`, `ontology` per domain agent) | Yes |
| Anomaly detection | `services/business_state/detectors.py` (robust z-score) | Yes — flags "needs attention" per metric |
| Quality flag enum (`STALE`, `SPARSE_HISTORY`, …) | 03_DEFINITIONS_TO_MAKE_FUNCTIONAL §5 | Yes — a snapshot is `DEGRADED` if any underlying metric carries one |
| Persistence | n/a for V0 — JSON files on disk, see storage decision above | No — file read/write is stdlib `json` + `pathlib`, no new persistence framework. `persistence/postgres.py`'s JSONB pattern is the reference for the *later* migration, not built now. |
| Evidence provenance shape | `EvidenceArtifact` / Claim Gate | Yes — snapshot values that get quoted in an answer still need evidence refs |

**Do not build:** a second metric catalogue, a second scheduler framework, a
new microservice, a vector/cache DB. One table + one resolver module +
one cron entry point.

## What's new (small)

1. **`DomainStateSnapshot`** — one record per `(domain, as_of)` (a JSON file
   for now, a JSONB row later — see storage decision above), holding a
   handful of resolved metrics + their state, not raw series.
2. **`DomainStateResolver`** — per domain, decides *which* metrics constitute
   "health" for that domain (below), calls `BusinessStateService` for each,
   assembles the snapshot, writes it.
3. **Cron entry point** — invokes all resolvers on a schedule; no new
   scheduler dependency, a simple periodic job (existing infra: whatever
   already runs `mage-ai` / ops cron, or a lightweight in-process
   scheduler — infra choice is a sprint-1 decision, not this doc).
4. **Overview intent path** — Coordinator recognizes overview-shaped queries
   and reads snapshot(s) first instead of decomposing into domain missions.

## Repo placement (service + config)

Same pattern as `business_state` (02's "Repo placement") — a service
package under `services/`, config-driven, no logic hardcoded in Python that
a reviewer would need to touch to tune a threshold or add a metric:

```text
src/seleric_swarm/services/domain_health/
  __init__.py
  resolver.py          # DomainStateResolver — reads the config below,
                        # calls BusinessStateService per metric, assembles
                        # + writes DomainStateSnapshot
  models.py             # DomainStateSnapshot, HealthSignal
  snapshot_store.py      # [2026-09-14] JSON file read/write for now — one
                        # file per (domain, as_of) under a data dir (e.g.
                        # var/domain_health_snapshots/{domain}/{as_of}.json).
                        # Same get_latest(domain) / save(snapshot) interface
                        # a Postgres-backed store would expose, so swapping
                        # the implementation later doesn't touch resolver.py
                        # or any caller.
  scheduler.py           # cron entry point (Sprint 4)

config/domain_health_profiles.yaml
```

**Deferred, not built now:** `migrations/00X_domain_state_snapshots.sql` and
the Postgres JSONB table — see the storage decision above. `persistence/postgres.py`'s
`CAST(:x AS JSONB)` pattern is the reference to follow *when* that migration
happens, not a Sprint 3 dependency.

`config/domain_health_profiles.yaml` is the config-driven version of the
domain tables above — the markdown tables are the human-readable spec, this
file is what `resolver.py` actually reads. It **selects and labels**
existing catalogue metrics (already defined in `metric_registry.yaml`); it
does not redefine any metric formula:

```yaml
domains:
  commerce:
    brand_id_scope: pinned      # see brand_id decision below
    metrics:
      - { metric_id: commerce_net_revenue_daily, feature_class: daily_series,
          features: [period_delta_pct, rolling_mean_7d] }
      - { metric_id: gross_sales, feature_class: daily_series,
          features: [period_delta_pct] }
      - { metric_id: orders, feature_class: daily_series,
          features: [period_delta_pct] }
      - { metric_id: returns_cancels, feature_class: daily_series,
          features: [current_value] }
    health_signals:
      - { id: net_sales_drop, metric_id: commerce_net_revenue_daily,
          rule: period_delta_pct_below, threshold: -0.15 }
      - { id: order_volume_drop, metric_id: orders,
          rule: period_delta_pct_below, threshold: -0.20 }
  customer:
    metrics:
      - { metric_id: repeat_rate, feature_class: windowed_point, features: [] }
      # ... one block per domain, same shape
```

Threshold changes, adding a metric to a domain's snapshot, or excluding one
— all a YAML edit, no code review of `resolver.py` needed. This is the
"easy to configure" requirement: the resolver is generic over the config,
the config is what changes per domain/threshold/rollout.

## Domain-wise health definition

For each domain, "health" = what a real domain owner/analyst would check
first. Metrics are pulled from the **existing** `metric_registry.yaml`
domain buckets (commerce 10, performance 19, finance 14, funnel 27,
attribution 10, product 4, customer 4, operations 1) — the snapshot does not
invent new metrics, it selects and labels a subset as "overview-worthy."

Legend: **Core** = always in the snapshot. **Derived feature** = from
`BusinessStateService` features (`period_delta_pct`, `rolling_mean_7d`, …),
not a new metric definition. **Health signal** = the condition that flips
this domain to "needs attention."

### Commerce (`commerce_agent`, module `commerce`)

| Core metric | Derived feature | Health signal |
|---|---|---|
| `commerce_net_revenue_daily` (= `metric.net_sales`) | `period_delta_pct` (DoD), `rolling_mean_7d` | net_sales down >X% vs rolling mean, or `direction_bad` breach |
| `gross_sales` | `period_delta_pct` | large gap vs net_sales (discount/refund spike) |
| `orders` **[validated]** — `commerce_orders` view, daily via `order_date` | `period_delta_pct` | order volume collapse independent of AOV |
| `returns_cancels` **[validated]** — a count, event-date axis (`event_date`), not a rate | current value | spike vs rolling mean |

**[validated correction]** there is no pre-built "return/cancellation
**rate**" metric — `returns_cancels`/`cancelled_orders`/`refunded_orders`
are counts on `event_date`, while `orders` is a count on `order_date`. A
rate needs client-side division of two series on **different date axes**
(see Operations below for why that's a real gap, not a detail).

**Owner question answered:** "Are we selling at the normal pace today, and is anything getting returned/cancelled more than usual?"

### Finance (`finance_agent`, module `finance`)

| Core metric | Derived feature | Health signal |
|---|---|---|
| `net_profit_all_channels` **[validated id]** | `period_delta_pct`, `rolling_mean_7d` | negative or below rolling mean by threshold |
| `gross_margin_pct` **[validated id]** | current value | margin compression |
| `mer` **[validated id]** | `period_delta_pct` | MER worsening trend |
| `rto_cost` **[validated id]** | current value | RTO spike eating margin |

**Owner question answered:** "Are we profitable today, and is margin holding?"

### Performance / Paid Media (`performance_agent`, module `paidmedia`)

| Core metric | Derived feature | Health signal |
|---|---|---|
| `total_ad_spend` **[validated — lives on `canonical_pnl`, `finance` module, NOT `paidmedia`]** | `period_delta_pct` | spend pacing off vs plan/rolling mean |
| `net_roas` / `net_roas_all_channels` **[validated — also `finance` module]** | `period_delta_pct`, anomaly (robust z-score) | ROAS below anomaly threshold |
| `cac` (not found under `paidmedia` search — confirms `metric_registry.yaml`'s `seleric_module: null`, unscoped) | `rolling_mean_7d` | CAC trending up |
| `meta_cpm` / `google_cpm` / `amazon_ads_cpm` (delivery frontier, genuinely in `paidmedia`) | current value | delivery cost spike (frontier signal, not outcome) |

**[validated correction]** the "performance domain" snapshot is **not
single-module** — its outcome metrics (spend, ROAS, CAC) live in the
`finance` module's `canonical_pnl`, only its delivery metrics (CPM, CTR)
are actually in `paidmedia`. A `DomainStateResolver` for performance must
query both modules; it cannot be pinned to one MCP module allowlist the way
`agent_registry.yaml`'s `seleric_module: paidmedia` implies.

**Owner question answered:** "Is paid media spending efficiently today, or did delivery/efficiency break?"

### Attribution (`attribution_agent`, module `attribution`)

| Core metric | Derived feature | Health signal |
|---|---|---|
| `attributed_net_revenue` **[validated id]** | `period_delta_pct` | attributed revenue diverging from commerce net_sales (mismatch = tracking issue) |
| `channel_orders` **[validated id]** dimensioned by `channel` | current value, share of total | one channel share collapse (tracking/creative issue) |

**Owner question answered:** "Is attribution tracking working, and is channel mix normal?"

### Funnel / Web Analytics (`funnel_agent`, module `webanalytics`)

| Core metric | Derived feature | Health signal |
|---|---|---|
| `web_sessions` **[validated id]** | `period_delta_pct` | traffic collapse |
| `funnel_conversion_rate` or `session_conversion_rate` **[validated ids — both exist, both daily]** | `rolling_mean_7d`, anomaly | CVR anomaly (site/checkout break) |
| `session_checkout_rate` / `session_checkout_to_purchase_rate` **[validated ids]** | current value | stage-specific collapse (payment gateway down) |

**Owner question answered:** "Is the site converting normally, or is a funnel stage broken?"

### Product (`product_agent`, module `product`)

| Core metric | Derived feature | Health signal |
|---|---|---|
| `product_net_revenue` dimensioned by `sku`, sorted desc, limit N **[validated — no pre-built "concentration" metric, this is a query pattern]** | current value | concentration risk / hero SKU stockout signal |
| `product_gross_margin_pct` **[validated id]** dimensioned by `sku`, filtered/sorted for negatives | current value | negative-margin SKUs selling |

**Owner question answered:** "Is any SKU actively losing money or is revenue dangerously concentrated?"

### Customer (`customer_agent`, module `customer`)

| Core metric | Derived feature | Health signal |
|---|---|---|
| `new_customer_orders` (commerce module) vs `orders` | `period_delta_pct` on the ratio | returning-customer share dropping (retention risk) |
| `repeat_rate` **[validated id]** | **none** — see correction below | trending down vs prior snapshot's point value |

**[validated correction]** `repeat_rate` has `supported_dimensions:
[brand_id]` only — **no `report_date` dimension**. A live query confirmed
it: passing a `time_range` still works, but it silently becomes the query
*window* (e.g. "repeat rate over the last 30 days"), returning **one row**,
not a daily series. `period_delta_pct` / `rolling_mean_7d` as designed
(daily-series features) **do not apply** to this metric — the only valid
"trend" is comparing this cron run's windowed value to the previous cron
run's windowed value, i.e. snapshot-over-snapshot, not `BusinessStateService`
series features. Document this as a distinct feature class
("windowed_point", not "daily_series") in `business_state_profiles.yaml`
(03 §4) before wiring it — the feature engine currently assumes a series.

**Owner question answered:** "Are we retaining customers, or living quarter to quarter on new acquisition?"

### Operations / Returns (`operations_agent`, module `operations`)

| Core metric | Derived feature | Health signal |
|---|---|---|
| `refund_count` **[validated id]**, event axis `refund_date` | `period_delta_pct`, anomaly | refund spike |
| `refunded_amount_excl_tax` **[validated id]**, event axis `refund_date` | current value | disproportionate refund value vs order value |

**[validated correction — real gap, not a nuance]** there is **no**
"refund rate" catalogue metric. A live `metrics_query(["refund_count",
"orders"])` came back `composed=true` with two `parts[]` on **different
date axes** (`refund_events.refund_date` vs `commerce_orders.order_date`)
and an explicit tool warning: *"do not join or sum rows across parts —
grains/axes may differ."* Computing "refund rate" as
`refund_count / orders` per day is exactly the join the tool is warning
against — the two series are not aligned same-day denominators. This needs
an explicit decision before implementation: either (a) accept the axis
mismatch and document the approximation, or (b) rate against a same-axis
denominator (e.g. `refund_count` vs `returns_cancels`-adjacent same-event-date
order count, if one exists) — **not yet resolved, add to 03 §3 (series
contract) as an explicit "cross-view ratio" rule.**

**Owner question answered:** "Are returns/refunds under control today?"

### Inventory / Procurement / Technical — **not buildable yet**

`agent_registry.yaml` has these `enabled: false` with the comment "no Seleric
MCP module yet." A domain snapshot cannot be resolved without a certified
metric source — per the golden rule (`02_SELERIC_AGENT_INTEGRATION.md`, "no
fallback raw SQL"), these domains are **excluded from V0 snapshots**, not
stubbed with placeholder numbers. Revisit when `catalogue_search_metrics`
exposes a module for them. This matches [Serve capability gaps memory] —
inventory/discount-code/SLA questions need ingestion work outside this
service's scope.

## `DomainStateSnapshot` shape (contract sketch, not final)

```text
domain               # commerce | finance | performance | attribution | funnel | product | customer | operations
brand_id              # [scope decision, 2026-09-14] PINNED to "20" (Tilting Heads) for all
                       # V0 sprints. `catalogue_list_brands` confirms 5 active brands (20/26/25/27/24)
                       # exist in the data — this is a deliberate scope cut, not a data
                       # limitation. Field stays in the schema (and every resolver call
                       # passes it explicitly rather than relying on the MCP's silent
                       # default) so multi-brand is a config change later, not a schema
                       # migration — but no sprint before this decision is revisited
                       # builds snapshots for brand_id != "20".
as_of                # timestamp this snapshot represents (data as-of, not compute time)
computed_at           # when the cron resolved it
window                # e.g. {grain: day, compare: DoD}
status                # OK | DEGRADED | UNAVAILABLE  (rolls up MetricState.status across the set)
metrics: [
  {
    metric_id, value, unit,
    period_delta_pct, rolling_mean_7d,
    direction_bad, is_anomaly, anomaly_score?,
    freshness, quality_flags[]
  }, ...
]
headline_signals: [ "net_sales down 18% vs 7d avg", "refund_count anomaly" ]
provenance { mcp_query_ids[], profile_id, catalogue_version, business_state_version }
```

`headline_signals` are **generated deterministically** from `health signal`
rules above (threshold/anomaly flags), not LLM-written — the LLM narrates
them at answer time, it doesn't invent them. This preserves the Claim Gate
rule: the number and the "why it's flagged" both come from deterministic
code.

## Overview answer path (read, not compute)

1. Coordinator classifies query as **overview-shaped** (no specific
   metric/dimension named, asks for status/health/"how are we doing").
2. Read latest `DomainStateSnapshot` for the domain(s) implied (all domains
   for "how's the business", one domain for "how's paid media doing").
3. If a snapshot is missing/stale beyond its own freshness window, that
   domain is `UNAVAILABLE` in the answer — never silently skipped, never
   backfilled with a live call by default.
4. LLM synthesis reads the snapshot(s), produces the narrated answer.
5. **Only if** the user's question needs something no snapshot metric
   covers (a specific SKU, a specific campaign, a non-overview drill-down),
   dispatch that piece to the relevant domain agent **live, in parallel**
   with reading the snapshots — not sequentially blocking the overview.

This is the same shape as the existing mission graph (parallel task
dispatch), just with a snapshot read replacing most of the fan-out.

## Overlap check / DRY

- Does **not** duplicate `BusinessStateService` — it is the only metric
  compute path used.
- Does **not** duplicate `metric_registry.yaml` — snapshots select from it,
  domain field already exists per metric.
- Does **not** duplicate domain agents — domain agents remain the path for
  specific/deep questions; snapshots only serve the overview path in front
  of them.
- Does **not** duplicate anomaly detection — reuses
  `services/business_state/detectors.py` strategy once built (see
  03_DEFINITIONS_TO_MAKE_FUNCTIONAL — anomaly is already scoped there).
- **Modifies, doesn't fork:** Coordinator gets one new intent branch
  (overview classification → snapshot read); no parallel coordinator.
- Persistence is JSON files for V0 ([2026-09-14 decision](#problem)) — no
  new table, no migration, no new persistence framework. The Postgres JSONB
  pattern (`persistence/postgres.py`) is the reference for the later
  migration, behind the same `snapshot_store.py` interface.

## Open questions for sprint planning (not decided here)

- Cron mechanism: reuse whatever already schedules `mage-ai`/warehouse jobs,
  or a small in-process scheduler in the swarm? (infra decision, not
  architecture)
- Snapshot cadence per domain — likely daily for finance/commerce/customer
  (day-grain metrics), hourly for performance/funnel (see
  `[[hourly-grain-timezone-and-ad-spend]]` memory — hourly ad-spend gold
  tables already exist, cube doesn't expose them yet; snapshot resolver can
  read gold directly via MCP once exposed).
- Threshold source for `headline_signals` — static config first
  (`business_state_profiles.yaml`-style), tunable per domain later.
- **[resolved by scope decision, 2026-09-14]** Multi-brand: `catalogue_list_brands`
  confirms 5 active brands (20 Tilting Heads / 26 Sniff Theory / 25 Urthend /
  27 Mannmore / 24 The Billy Company) — so this was never a "no data"
  question, it's a scope call. Decision: **pin `brand_id="20"` for all V0
  sprints**, pass it explicitly on every resolver call (never rely on the
  MCP's silent default-to-20 behavior — that's an implicit fallback, not a
  documented scope choice, and the two must not be conflated). The snapshot
  schema keeps a `brand_id` field so adding brands 26/25/27/24 later is a
  config/loop change, not a schema migration — but building snapshots for
  them is explicitly not in scope until this decision is revisited.
- **[resolved, implemented]** cross-view ratio metrics (refund rate,
  customer retention ratio) — resolved by exclusion, not computation:
  `config/domain_health_profiles.yaml` leaves them out of V0 snapshots
  (see its Sprint 4 scope-cut comments) rather than dividing misaligned
  series. `QualityFlag.CROSS_AXIS_RATIO_UNSUPPORTED` exists in
  `domain/models.py` for when this is revisited.
- **[resolved, implemented]** module pinning — `metric_registry.yaml` sets
  `seleric_module: null` on `metric.spend`/`metric.cac`/`metric.net_roas`
  (with inline comments explaining why), and `services/measure.py`'s
  `module_args()` passes that explicit `None` through
  `protocols/mcp/gateway.py`'s "explicit module in arguments wins" rule,
  overriding `performance_agent`'s `paidmedia` pin. `DomainStateResolver`
  already resolves per metric_id from `metric_registry.yaml`, not a
  per-domain module allowlist.
