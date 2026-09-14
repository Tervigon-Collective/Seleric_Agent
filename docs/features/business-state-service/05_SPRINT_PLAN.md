# 05 — Phased implementation plan (sprints)

**Status:** planning only — no code written against this plan yet.

Scope: get [`BusinessStateService`](01_ARCHITECTURE.md) from "designed" to
"implemented," then layer [Domain Health Snapshots](04_DOMAIN_HEALTH_SNAPSHOTS.md)
on top. Each sprint ends with something runnable and testable — no sprint
ships a half-wired abstraction.

Dependency chain: **Sprint 0/1 (Business State core) blocks everything else.**
Snapshots are a caller of `get_metric_state`; they cannot start until it
returns real `MetricState` for at least one metric.

---

## Sprint 0 — Contracts freeze (no runtime code)

Matches the checklist already in
[03_DEFINITIONS_TO_MAKE_FUNCTIONAL.md](03_DEFINITIONS_TO_MAKE_FUNCTIONAL.md#definition-checklist-order).

- [ ] `MetricState` + `StateRequest` schemas (`schemas/metric_state.schema.json` + Pydantic model)
- [ ] Series fetch contract: grain, gap policy, `min_points` per capability
- [ ] `config/business_state_profiles.yaml` with one `default_v1` profile
- [ ] Quality flag enum frozen (`MISSING_DATA`, `STALE`, `SPARSE_HISTORY`, …,
      `CROSS_AXIS_RATIO_UNSUPPORTED` — [validated finding](06_DATA_VALIDATION_FINDINGS.md))
- [ ] Evidence/Anomaly/Forecast mapping table
- [ ] Pilot metrics chosen + golden fixture series (2–3 metrics, e.g. `metric.net_sales`, `metric.spend`)
- [ ] Feature class split: `daily_series` (existing 5 features) vs
      `windowed_point` (compare snapshot-to-snapshot, no daily series exists
      — required for `repeat_rate`-shaped metrics, see
      [06](06_DATA_VALIDATION_FINDINGS.md#4-repeat_rate-has-no-daily-grain))
- [ ] Cross-view ratio rule: before computing any ratio from two separately
      fetched metrics, confirm same view + same date axis via
      `catalogue_get_metric`, else `CROSS_AXIS_RATIO_UNSUPPORTED`

**Exit criteria:** schemas + one profile YAML exist and are reviewed; no
agent code changed yet.

---

## Sprint 1 — Business State facade (MVP, 1–2 metrics)

- [ ] `src/seleric_swarm/services/business_state/` package: `facade.py`,
      `models.py`, `series.py`, `features.py`, `profiles.py`
- [ ] `series.py`: normalized `{ts, value, finality?}` fetch via existing
      MCP gateway (`metrics_query`) — reuse, do not reimplement, the Observer
      fetch path (`agents/intelligence/observer.py::_fetch_seleric`)
- [ ] 5 features only: `current_value`, `period_delta_pct`, `rolling_mean`,
      `rolling_std`, `freshness_age`
- [ ] `BusinessStateService.get_metric_state(need=[actual, features])` works
      live for `metric.net_sales` against MCP
- [ ] `runtime.business_state` wired in `bootstrap.py`
- [ ] Unit test: fixture series → expected mean/delta (per ponytail: one
      runnable check, no framework)
- [ ] Observer can call it and emit evidence (proves the caller contract,
      doesn't yet replace `_comparison_deltas`)

**Exit criteria:** matches 02_SELERIC_AGENT_INTEGRATION.md "Definition of
done (first PR)" — live for ≥1 metric, Claim Gate still blocks ungrounded
claims, no new deployable/DB.

**Explicitly deferred:** anomaly, forecast, caching — Sprint 2+.

---

## Sprint 2 — Anomaly + broaden metric coverage

**Correction:** the live `AnomalyAgent`/`PredictionAgent`
(`swarm/specialists/anomaly.py`, `swarm/specialists/prediction.py`) are
**not** stubs — they run today, gated by `ProviderBundle.anomaly` /
`.forecaster`, currently hardcoded to `TemplateAnomalyDetector` /
`TemplateForecaster` in `build_hybrid_bundle()`. The `not_implemented`
stubs are dead legacy files (`agents/intelligence/anomaly.py`,
`prediction.py`) not on the live path. This sprint adds a **new** detector
strategy behind the existing provider seam, it does not unstub anything.

- [ ] `detectors.py`: robust z-score / MAD strategy
- [ ] `evaluate_anomaly()` wired; strategy registered as a selectable
      `AnomalyDetector` implementation (see Sprint 2.5 for the config seam
      that lets `build_hybrid_bundle()` choose it over the Template default)
- [ ] Extend pilot set toward the domains Sprint 4 will need (finance,
      performance minimum — Sprint 3 itself only needs `commerce`, this is
      getting ahead of that so Sprint 4 isn't blocked re-discovering metric
      ids already validated in [06](06_DATA_VALIDATION_FINDINGS.md))
- [ ] Quality-flag gating verified: `SPARSE_HISTORY`/`MISSING_DATA` →
      `UNAVAILABLE`, never a fabricated anomaly score

**Exit criteria:** Anomaly agent emits real `AnomalyArtifact`s (via the new
strategy) for at least one metric per domain in scope; Skeptic still
validates provenance.

---

## Sprint 2.5 — Pluggable anomaly / forecast providers

Small, decoupled from the Business State build — can run in parallel with
Sprint 1/2 once picked up. Closes the "anomaly/prediction not working
properly" gap: the agents are fine, the provider choice is hardcoded.
Full spec: [03 §10](03_DEFINITIONS_TO_MAKE_FUNCTIONAL.md#10-provider-configurability-anomaly--forecast).

- [ ] Provider-selection config (`{domain | metric_id} ->
      {anomaly_strategy, forecast_strategy}`, default fallback) — new small
      YAML or an extension of `agent_registry.yaml`, reviewer's call
- [ ] `build_hybrid_bundle()` reads the config and instantiates the selected
      `AnomalyDetector` / `Forecaster` instead of hardcoding `Template*` —
      no change to `swarm/specialists/anomaly.py` / `prediction.py`, they
      already consume `self.providers.anomaly` / `.forecaster` generically
- [ ] Delete `agents/intelligence/anomaly.py` and
      `agents/intelligence/prediction.py` once confirmed unused (grep for
      importers first — confirmed clean in this investigation, but re-check
      at implementation time)
- [ ] One test: config selects a non-default strategy → `build_hybrid_bundle`
      returns that implementation, not the Template one

**Exit criteria:** anomaly/forecast strategy is a config choice, not a code
change; dead stub files removed; no regression to the live specialists.

---

## Sprint 3 — Domain Health Snapshot: single domain vertical slice

Pick **one** domain (recommend `commerce` — smallest, clearest core metric
set, matches the pilot metric already live from Sprint 1).

- [ ] `DomainStateSnapshot` contract frozen (per
      [04_DOMAIN_HEALTH_SNAPSHOTS.md](04_DOMAIN_HEALTH_SNAPSHOTS.md#domainstatesnapshot-shape-contract-sketch-not-final)) —
      schema carries `brand_id`, **pinned to `"20"`** for this sprint (5
      active brands exist per `catalogue_list_brands`, but only Tilting
      Heads is in scope until that decision is revisited); resolver passes
      `brand_id` explicitly, never relies on the MCP's silent default
- [ ] `src/seleric_swarm/services/domain_health/` package scaffolded
      (`resolver.py`, `models.py`, `snapshot_store.py` — per
      [04's Repo placement](04_DOMAIN_HEALTH_SNAPSHOTS.md#repo-placement-service--config))
- [ ] `config/domain_health_profiles.yaml` with a `commerce` block only —
      metric list + feature class + health-signal thresholds live in YAML,
      **not** hardcoded in `resolver.py`; `resolver.py` is generic over the
      config from day one so adding domains in Sprint 4 is a YAML edit
- [ ] `DomainStateResolver` for commerce only: reads the config block, calls
      `BusinessStateService.get_metric_state` per listed metric, assembles
      one snapshot
- [ ] New table (JSONB), following the exact pattern of
      `persistence/postgres.py` / `migrations/001_init.sql` — no new store
      abstraction
- [ ] Manual/cron-less trigger first (a callable function, run by hand or a
      test) — defer actual cron wiring to Sprint 4 so the resolver logic is
      proven before scheduling infra is picked
- [ ] `headline_signals` rule for commerce (threshold-based, deterministic)

**Exit criteria:** running the resolver once produces a valid, queryable
JSONB row for commerce with real numbers and correct `status` rollup.

---

## Sprint 4 — Cron wiring + remaining domains

- [ ] Decide cron mechanism (reuse existing ops scheduling vs. lightweight
      in-process — this is the one open infra decision from 04, resolve it
      here, not earlier)
- [ ] `config/domain_health_profiles.yaml` gets 7 more domain blocks
      (finance, performance, attribution, funnel, product, customer,
      operations) — `resolver.py` itself shouldn't need to change, since it
      was already written generic-over-config in Sprint 3; if it does need
      a code change here, that's a sign Sprint 3's config schema wasn't
      general enough and should be revisited, not patched per-domain
- [ ] Resolver extended to finance, performance, attribution, funnel,
      product, customer, operations (7 more domains — mechanical repetition
      of the Sprint 3 pattern, not new architecture, **except**:
      - performance needs metrics from **two** modules (`finance`'s
        `canonical_pnl` for spend/ROAS/CAC, `paidmedia` for CPM) — resolver
        can't be pinned to one MCP module allowlist, per 04's validated finding
      - customer's `repeat_rate` is a `windowed_point` feature, not
        `daily_series` — needs the feature-class split from Sprint 0, not
        the same code path as the other 6 domains
- [ ] Snapshot cadence per domain (daily vs hourly) per
      04_DOMAIN_HEALTH_SNAPSHOTS.md open question
- [ ] Inventory/procurement/technical stay excluded (no MCP module) —
      explicitly not attempted this phase

**Exit criteria:** all 8 buildable domains produce a snapshot on schedule;
staleness is visible (snapshot `computed_at` age), not silently stale.

---

## Sprint 5 — Overview answer path (Coordinator integration)

- [ ] Coordinator overview-intent classification (status/health-shaped
      query → snapshot read branch, not full mission decomposition)
- [ ] Snapshot read + LLM synthesis for "how are we doing today" /
      "what needs attention" style queries
- [ ] Parallel live-fetch fallback for the part of a question a snapshot
      doesn't cover (drill-down dispatch to the normal domain-agent path,
      run alongside the snapshot read, not after it)
- [ ] `UNAVAILABLE` handling: missing/stale snapshot surfaces as a gap in
      the answer, never silently dropped

**Exit criteria:** an overview query is answered primarily from stored
snapshots with correct latency improvement vs. full live fan-out, and a
drill-down follow-up still gets a live, accurate answer.

---

## Sprint 6 — Hardening (as needed, not required to ship value)

- [ ] Caching/TTL for `get_metric_state` if snapshot resolution load
      justifies it (per 02_SELERIC_AGENT_INTEGRATION.md step 4 — "optional
      cache later")
- [ ] Per-domain profile overrides in `business_state_profiles.yaml`
      (thresholds tuned per domain instead of one global default)
- [ ] Multi-brand rollout: `brand_id` is pinned to `"20"` through Sprint 5 by
      scope decision (not a data gap — 4 other active brands exist per
      `catalogue_list_brands`). If/when the business wants snapshots for
      Sniff Theory / Urthend / Mannmore / The Billy Company, this is a loop
      over `brand_id` in the resolver + cadence/threshold overrides per
      brand — the schema already supports it, this sprint just turns it on.

---

## What stays explicitly out of scope across all sprints

Matches the "deliberately excludes" lists in 01 and 03: separate
FastAPI/worker deployable, ClickHouse state history marts, Control
Plane/Appsmith, outbox event bus, champion/challenger model registry, full
Feast/feature store. If a later sprint needs one of these, that's a new ADR,
not a silent scope creep into this plan.
