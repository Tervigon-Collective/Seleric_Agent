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

- [x] `MetricState` + `StateRequest` schemas (`schemas/metric_state.schema.json`,
      `schemas/state_request.schema.json` + Pydantic models
      `seleric_swarm.domain.models.MetricState` / `StateRequest`)
- [x] Series fetch contract: grain, gap policy, `min_points` per capability
      (documented in 03 §3; frozen into `config/business_state_profiles.yaml`
      `series:` block — `max_lookback_days: 90`, `gap_policy: drop`;
      per-feature `min_points` already in the `features:` list)
- [x] `config/business_state_profiles.yaml` with one `default_v1` profile
- [x] Quality flag enum frozen (`MISSING_DATA`, `STALE`, `SPARSE_HISTORY`, …,
      `CROSS_AXIS_RATIO_UNSUPPORTED` — [validated finding](06_DATA_VALIDATION_FINDINGS.md)) —
      `seleric_swarm.domain.models.QualityFlag`, mirrored in
      `schemas/metric_state.schema.json#/$defs/quality_flag`
- [x] Evidence/Anomaly/Forecast mapping table (03 §6 — unchanged, reviewed as final)
- [x] Pilot metrics chosen + golden fixture series (`metric.net_sales`,
      `metric.spend` — `tests/fixtures/business_state/net_sales_series.json`,
      `spend_series.json`; arithmetic verified, see fixture `notes`)
- [x] Feature class split: `daily_series` (existing 5 features) vs
      `windowed_point` (compare snapshot-to-snapshot, no daily series exists
      — required for `repeat_rate`-shaped metrics, see
      [06](06_DATA_VALIDATION_FINDINGS.md#4-repeat_rate-has-no-daily-grain)) —
      documented in `config/business_state_profiles.yaml` header;
      `windowed_point` strategy implementation deferred to Sprint 4
- [x] Cross-view ratio rule: before computing any ratio from two separately
      fetched metrics, confirm same view + same date axis via
      `catalogue_get_metric`, else `CROSS_AXIS_RATIO_UNSUPPORTED` — encoded as
      a quality flag; enforcement lands with `features.py` in Sprint 1+

**Exit criteria:** schemas + one profile YAML exist and are reviewed; no
agent code changed yet. **Met** — `domain/models.py` gained two new Pydantic
types and three Literal aliases (contracts only, no caller wired), plus two
new schema files, one new config file, and two fixture files. No existing
agent, coordinator, or bootstrap code was touched.

---

## Sprint 1 — Business State facade (MVP, 1–2 metrics)

- [x] `src/seleric_swarm/services/business_state/` package: `facade.py`,
      `series.py`, `features.py`, `profiles.py` — **no package-local
      `models.py`**: `MetricState`/`StateRequest` already live in
      `domain/models.py` beside `EvidenceArtifact` since Sprint 0; a second
      copy would just be duplication, not a missing piece
- [x] `series.py`: normalized `{ts, value, finality?}` fetch via existing
      MCP gateway (`metrics_query`) — reuses the same measure/module
      resolution shape as the Observer fetch path
      (`agents/intelligence/observer.py::_fetch_seleric`), adds
      `granularity` (Observer never fetches a real series, only point/window
      aggregates). Live-verified against `commerce_net_revenue_daily` and
      `total_ad_spend`: the date key is always `<view>.<dim>.day` regardless
      of view/dimension name — that's the row-parsing heuristic used.
- [x] 5 features only: `current_value`, `period_delta_pct`, `rolling_mean`,
      `rolling_std`, `freshness_age` (`freshness_age` sourced from query
      provenance via `classify_freshness`, not the series — per Sprint 0's
      validated finding that freshness is already computed server-side)
- [x] `BusinessStateService.get_metric_state(need=[actual, features])` —
      unit-tested end-to-end against a fake MCP shaped exactly like the live
      response for `metric.net_sales`; not yet smoke-tested against a live
      MCP call in this sprint (series.py itself *is* live-verified above,
      via direct `metrics_query` calls used to design it)
- [x] `runtime.business_state` wired in `bootstrap.py` (built after
      `SwarmRuntime` construction since `BusinessStateService` holds a
      runtime reference, not the reverse)
- [x] Unit test: fixture series → expected mean/delta
      (`tests/unit/test_business_state.py`, runs the real
      series.py/features.py/facade.py path against the Sprint 0 golden
      fixture, no framework beyond the repo's existing pytest)
- [x] Observer can call it and emit evidence
      (`Agent.business_state_evidence` in `observer.py`) — additive method,
      not wired into `observe()`'s production path; proves the caller
      contract without touching `_comparison_deltas` or any existing test

**Exit criteria:** matches 02_SELERIC_AGENT_INTEGRATION.md "Definition of
done (first PR)" — live for ≥1 metric, Claim Gate still blocks ungrounded
claims, no new deployable/DB. **Met and live-smoke-tested**:
`runtime.business_state.get_metric_state` called against the real
`seleric-mcp` for `metric.net_sales` (2026-09-01..07, brand_id=20) returned
`status=OK`, `actual=71727.93` (net sales for 2026-09-07), all 5 features
populated, no quality flags. First attempt hit a transient
`CubeError: Too many simultaneous queries` / "still building" from shared
Cube load — surfaced correctly as `status=UNAVAILABLE` +
`quality_flags=[MCP_ERROR]`, no crash, no fabricated value; a retry a few
seconds later succeeded. No retry/backoff is implemented in `series.py` yet
— every transient Cube hiccup currently surfaces as one `UNAVAILABLE` call
rather than being retried internally; add a retry ladder if this proves
noisy under real mission traffic (not blocking for Sprint 1's single-call
scope).

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

- [x] `detectors.py`: robust z-score / MAD strategy — `robust_zscore()`, a
      pure function (median + MAD, no I/O), unit-tested against a hand-picked
      fixture (`tests/fixtures/business_state/net_sales_anomaly_series.json`)
      where the expected values were computed by the function itself and
      cross-checked, not hand-derived
- [x] `evaluate_anomaly()` wired on `BusinessStateService` — thin wrapper
      around `get_metric_state(need=[..., "anomaly"])`, returns just the
      anomaly subset. Strategy also registered as a selectable
      `AnomalyDetector` implementation: `RobustZScoreDetector` in
      `detectors.py` conforms to the `AnomalyDetector` Protocol
      (`swarm/providers/base.py`) today, so it can be dropped into
      `ProviderBundle.anomaly` directly. **Not** wired into
      `build_hybrid_bundle()` — the config-driven *selection* of it over
      `TemplateAnomalyDetector` is still Sprint 2.5, deliberately untouched
      here.
- [x] Extend pilot set toward the domains Sprint 4 will need — added
      `metric.net_profit` (finance domain,
      `tests/fixtures/business_state/net_profit_series.json`), joining
      `metric.net_sales` (commerce) and `metric.spend` (performance) from
      Sprint 0/1. Unlike the other two fixtures, this one's series is real
      live data pulled from `seleric-mcp` (brand_id=20, 2026-09-01..07,
      deeply negative test-tenant values) with expected features computed by
      script — a useful edge case: features must work on negative series
      without sign special-casing, and they do (verified).
- [x] Quality-flag gating verified: `SPARSE_HISTORY` → `anomaly=None` +
      flag, never a fabricated score
      (`tests/unit/test_business_state_anomaly.py::test_anomaly_sparse_history_never_fabricates_a_score`).
      **Deviation from the literal 03 §5 wording** ("→ `UNAVAILABLE`"):
      implemented as *capability-scoped* — if `anomaly` lacks history but
      `actual`/`features` resolved fine, `MetricState.status` is `PARTIAL`
      with `anomaly=None`, not a blanket `UNAVAILABLE` that would discard
      valid features. `UNAVAILABLE` stays reserved for "no series at all"
      (unchanged from Sprint 1). Flagged here explicitly since it reads
      stricter in the doc than what's implemented — revisit if a consumer
      actually needs the stricter blanket behavior.
- [x] Anomaly's history window (`anomaly.window: 28d` in the profile) is
      independent of whatever window the caller's `time_range` asks for —
      `get_metric_state` widens the *fetch* window internally when
      `"anomaly"` is in `need`, so a caller asking about "yesterday" still
      gets a real 28d history fetched underneath. Live-verified against
      `metric.spend`: a 1-day request widened to a 2026-08-10..09-07 fetch,
      returned a real (non-anomalous) z-score of 0.64.

**Exit criteria:** Anomaly agent emits real `AnomalyArtifact`s (via the new
strategy) for at least one metric per domain in scope; Skeptic still
validates provenance. **Partially met, honestly**: `RobustZScoreDetector`
produces real `AnomalyFinding`s end-to-end (unit-tested) and is
Protocol-conformant, but it is not registered in `ProviderBundle` /
`build_hybrid_bundle()`, so `AnomalyAgent` does not call it yet in any live
mission — that registration is explicitly Sprint 2.5's job, not skipped by
oversight. "One metric per domain in scope" was not attempted beyond the 3
pilot metrics (commerce/performance/finance); broadening further is Sprint 4
per the plan's own sequencing, not this sprint.

---

## Sprint 2.5 — Pluggable anomaly / forecast providers

Small, decoupled from the Business State build — can run in parallel with
Sprint 1/2 once picked up. Closes the "anomaly/prediction not working
properly" gap: the agents are fine, the provider choice is hardcoded.
Full spec: [03 §10](03_DEFINITIONS_TO_MAKE_FUNCTIONAL.md#10-provider-configurability-anomaly--forecast).

- [x] Provider-selection config (`{domain | metric_id} ->
      {anomaly_strategy, forecast_strategy}`, default fallback) — new file
      `config/provider_registry.yaml` (didn't extend `agent_registry.yaml`:
      that file's schema is per-*agent*, this mapping is per-domain/metric, a
      different axis — a new file is smaller than bolting on an
      unrelated shape). Loader: `registry/provider_registry.py::ProviderRegistry`,
      same pattern as `AgentRegistry`. Resolution order: metric override >
      domain override > default. Ships with real overrides already active
      (`metric.spend`, `metric.net_profit`, domain `commerce` → `robust_zscore`),
      not just a placeholder.
- [x] `build_hybrid_bundle()` reads the config and instantiates the selected
      `AnomalyDetector` instead of hardcoding `Template*` — no change to
      `swarm/specialists/anomaly.py`, it still just calls
      `self.providers.anomaly.detect(...)` once. New
      `swarm/providers/provider_selection.py::ConfiguredAnomalyDetector`
      does the dispatch: groups readings by resolved strategy per call,
      delegates each group to `TemplateAnomalyDetector` or
      `BusinessStateService`'s `RobustZScoreDetector`, merges results — so
      one mission can legitimately use both detectors for different metrics
      in the same anomaly pass. `build_hybrid_bundle()` gained two new
      optional kwargs (`business_state`, `provider_registry`); every
      existing caller that doesn't pass them keeps identical
      `TemplateAnomalyDetector`-only behavior (graceful degrade, logged, if
      config asks for `robust_zscore` but no `business_state` was wired —
      never crashes). `coordinator/graph.py`'s call site now passes
      `runtime.business_state`.
      **Forecast axis**: `forecast_strategy` exists in the config schema for
      forward-compatibility but is not wired — `BusinessStateService` has no
      `forecasts.py`/`Forecaster` implementation yet, so there is nothing to
      select besides `TemplateForecaster`. Revisit once a forecast strategy
      exists.
- [x] Deleted `agents/intelligence/anomaly.py` and
      `agents/intelligence/prediction.py` — re-confirmed zero importers
      (grep clean, including no dynamic/reflective loader keyed off
      `agent_id`) immediately before deleting.
- [x] One test: config selects a non-default strategy → `build_hybrid_bundle`
      returns that implementation, not the Template one —
      `tests/unit/test_provider_selection.py`, 5 tests: default resolves to
      template, the shipped YAML's real override resolves correctly,
      `build_hybrid_bundle()` returns a `ConfiguredAnomalyDetector` (not a
      bare `TemplateAnomalyDetector`), a mixed-metric call routes one metric
      through `BusinessStateService` (live MCP, `data_origin=BUSINESS_STATE`)
      and another through Template in the *same* `detect()` call, and the
      no-`business_state`-wired degrade path. All pass, all live where MCP
      access matters (uses the `runtime` fixture, not a fake gateway).

**Exit criteria:** anomaly/forecast strategy is a config choice, not a code
change; dead stub files removed; no regression to the live specialists.
**Met for anomaly; forecast has no alternative strategy to select yet (see
above — not a gap in this sprint, just nothing to wire).** Regression check:
`test_mcp_hybrid_providers.py`, `test_diagnostic_swarm_bridge.py`,
`test_bridge_idempotency.py` all still pass unchanged against the new
`ConfiguredAnomalyDetector`-wrapped bundle.

**Mission-level integration test (added after this sprint, not part of the
original checklist):** `tests/unit/test_business_state_mission_integration.py`
drives a real diagnostic mission ("Why did ad spend increase over the last 3
days?") through the actual `coordinator.graph.run_swarm_v2_mission` — real
LLM classification, real MCP, real LangGraph cycle — with a thin spy wrapped
around `BusinessStateService.get_metric_state` (still calls straight through
to the real implementation) since `SwarmMissionResult.artifacts` only exposes
artifact *ids*, not enough to see which detector produced an anomaly finding.
This caught a real, previously-invisible bug: **`AnomalyAgent` never actually
passed a usable time window to any detector.** `mission.context["time_range"]`
is never populated anywhere in the mission graph — `TemplateAnomalyDetector`
never needed it (it only uses `reading.value`/`reading.baseline`), so the gap
was silent until `RobustZScoreDetector` needed real history and got
`{"kind": "none"}` with no dates, failing with
`"time_range did not resolve to concrete dates"` inside a live mission
despite every module-level test passing. Fixed in
`swarm/specialists/anomaly.py::AnomalyAgent.run` — falls back to
`mission.time_range` (the field that actually holds the resolved window)
when `mission.context` doesn't have one. One-line root cause, not a
detector-side workaround. Regression-checked: `test_mcp_hybrid_providers.py`,
`test_diagnostic_swarm_bridge.py`, `test_bridge_idempotency.py`,
`test_api_scenario_matrix.py` (same pre-existing 3 live-data failures as
before, no new ones) all still pass.

**Unrelated instability observed while verifying this sprint:** partway
through, `seleric_swarm.main`'s import chain and `Observer._fetch_seleric`
briefly broke (`_entity_named_in_query` missing from `catalogue_grounding.py`,
then a `supported=`/`catalogue_unit=` signature mismatch in `observer.py`)
and then resolved on their own a few minutes later without any change from
this work. That's a different process editing the same branch/files
concurrently (`coordinator/catalogue_grounding.py`, `agents/coordinator.py`,
`coordinator/intake/llm_classifier.py`, `observer.py` were already showing as
modified before this session even started). Not touched here — flagging so
it isn't mistaken for something this sprint broke.

---

## Sprint 3 — Domain Health Snapshot: single domain vertical slice

Pick **one** domain (recommend `commerce` — smallest, clearest core metric
set, matches the pilot metric already live from Sprint 1).

- [x] `DomainStateSnapshot` contract frozen — `services/domain_health/models.py`
      (`DomainStateSnapshot`, `ResolvedMetric`); carries `brand_id`, defaults
      to `"20"` per resolver call (never relies on the MCP's silent
      default). Simplified from the doc's sketch: no `unit`/`is_anomaly`/
      `anomaly_score`/`finality` fields yet — Sprint 3's commerce config
      doesn't request anomaly, add when a domain does.
- [x] `src/seleric_swarm/services/domain_health/` package scaffolded
      (`resolver.py`, `models.py`, `snapshot_store.py`, `__init__.py`) —
      `scheduler.py` deliberately not added yet, that's Sprint 4
- [x] `config/domain_health_profiles.yaml` with a `commerce` block only —
      metric list (`metric.net_sales`, `metric.gross_sales`, `metric.orders`,
      `metric.returns_cancels` — real `metric_registry.yaml` ids, not the
      doc sketch's catalogue ids) + per-metric feature list +
      `health_signals` thresholds live in YAML; `resolver.py` is generic
      over the config, adding a domain in Sprint 4 is a YAML block, no code
      change
- [x] `DomainStateResolver.resolve(domain, time_range, brand_id="20")` —
      reads the config block, calls `BusinessStateService.get_metric_state`
      per listed metric, assembles one `DomainStateSnapshot`
      (`tests/unit/test_domain_health.py`, fake `BusinessStateService` since
      the MCP round-trip itself is already covered by Sprint 1/2's tests)
- [x] **[2026-09-14 decision] JSON files, not a new Postgres table** — one
      file per `(domain, as_of)` on disk via `snapshot_store.py`
      (`get_latest(domain)` / `save(snapshot)`), no migration this sprint
- [x] Manual/cron-less trigger — `DomainStateResolver.resolve()` is a plain
      async method, called by hand or a test; no scheduler wired
- [x] `headline_signals` rule for commerce (threshold-based, deterministic)
      — only `period_delta_pct_below` implemented (the one rule kind the
      commerce config uses); other rule kinds (anomaly-based, etc.) land
      when a domain's config actually needs one

**Exit criteria:** running the resolver once produces a valid snapshot for
commerce with correct `status` rollup (`OK`/`DEGRADED`/`UNAVAILABLE` from the
underlying `MetricState.status`es), readable back via
`SnapshotStore.get_latest("commerce")`. **Met, unit-tested**
(`test_resolve_commerce_snapshot_flags_net_sales_drop`,
`test_resolve_degrades_status_on_partial_metric`,
`test_snapshot_round_trips_through_store`) — not yet live-smoke-tested
against real `seleric-mcp` data the way Sprint 1/2 were; do that before
Sprint 4 extends the pattern to 7 more domains.

---

## Sprint 4 — Cron wiring + remaining domains

- [x] **Cron mechanism decision:** no new scheduler dependency, no
      long-running in-process loop. `services/domain_health/scheduler.py`
      exposes a plain async `run_once()` + a `python -m
      seleric_swarm.services.domain_health.scheduler` entry point; an
      OS-level cron / Windows Task Scheduler invokes it once per run. This
      repo has no existing ops-scheduling infra to reuse (no `mage-ai`,
      `celery`, `apscheduler`, etc. found), so "lightweight, stdlib-only" was
      the only real option, not a close call.
- [x] `config/domain_health_profiles.yaml` gets 7 more domain blocks
      (finance, performance, attribution, funnel, product, customer,
      operations) — `resolver.py` did **not** need to change for 6 of them,
      confirming Sprint 3's config schema was general enough
- [x] Resolver extended to all 7 — mechanical for finance/performance/
      attribution/funnel/product/operations. Two real deviations from the
      04 doc's exact metric list, both because they need a capability that
      doesn't exist yet, not because they were skipped by oversight:
      - **performance's cross-module concern was already a non-issue**:
        `metric.spend`/`metric.net_roas`/`metric.cac` already carry
        `seleric_module: null` (unscoped) in `metric_registry.yaml`, and
        `metric.cpm` is genuinely `paidmedia` — the resolver never needed a
        module allowlist per domain, only the metric list it already had.
      - **attribution/product/customer's dimensioned metrics are dropped
        for now**: `channel_orders`-by-channel (attribution),
        SKU-level concentration/negative-margin views (product), and the
        `new_customer_orders`-vs-`orders` retention ratio (customer) are all
        "query pattern" metrics per the 04 doc (dimension breakdown + top-N,
        or a cross-view ratio), not plain catalogue scalars —
        `BusinessStateService.get_metric_state` fetches one undimensioned
        series per call today. Attribution/product snapshots use the plain
        aggregate metrics instead (`attributed_net_revenue`,
        `product_net_revenue`, `product_gross_margin_pct`); customer is
        `repeat_rate` only. Revisit once a dimensioned/top-N or cross-view-
        ratio capability exists — flagged, not silently dropped
        (`config/domain_health_profiles.yaml`'s header comment).
      - customer's `repeat_rate` **is** the `windowed_point` case: no
        `report_date` axis, so `resolver.py` computes `period_delta_pct`
        itself as this-run-vs-previous-snapshot (`_windowed_point_delta_pct`
        in `resolver.py`, reading the prior value via
        `resolve(..., store=...)`) instead of asking
        `BusinessStateService` for daily-series features. Live-verified:
        first run against a fresh store → `period_delta_pct=None` (no
        prior); second run → a real (0.0%, same-value) delta, not
        fabricated.
      - `_headline_signals` gained a `period_delta_pct_above` rule
        direction (operations' `refund_spike`) alongside the existing
        `_below` — same threshold shape, opposite comparator.
- [x] Snapshot cadence: **daily for every domain, for now** — the simplest
      thing that works; 04 doc's per-domain hourly question (performance/
      funnel) is deferred until a domain actually needs it (re-running
      `scheduler.run_once` more often is a cron-line change, not code).
- [x] Inventory/procurement/technical stay excluded (no MCP module) — not
      in `ALL_DOMAINS`.

**Exit criteria:** all 8 buildable domains produce a snapshot; staleness is
visible via `computed_at`, not silently stale. **Met, live-verified**: `python
-m seleric_swarm.services.domain_health.scheduler`-equivalent
(`scheduler.run_once`) run against real `seleric-mcp` resolved all 8
domains to `status=OK` with real numbers (commerce/finance/funnel each
correctly flagged a real health signal off live data). Not yet wired to an
actual OS cron entry (that's an ops/deploy step outside this repo, not
blocking the code). 16 `domain_health` tests total
(`tests/unit/test_domain_health.py`, 15 — all 8 domains parametrized,
`_above`/`_below` rules, windowed_point delta, `scheduler.run_once`;
`tests/unit/test_domain_health_live.py`, 1 — live commerce smoke test from
Sprint 3, unchanged).

---

## Sprint 5 — Overview answer path (Coordinator integration)

- [x] **Coordinator overview-intent classification: already existed.** The
      LLM classifier (`coordinator/intake/llm_classifier.py`) already
      produces an `executive_health` intent for "how are we doing today?"
      -style queries, and `decomposition/templates.py`'s `executive_health`
      template already scopes it to 5 branches (commerce, performance,
      funnel, finance, operations) — that scope is reused as-is
      (`coordinator/overview.py::OVERVIEW_DOMAINS`), no new classification
      work needed. What was missing was the branch that reads snapshots
      instead of running the full live fan-out; `coordinator/overview.py`
      + a ~25-line early-return in `graph.py::run_swarm_v2_mission` (right
      after the existing `unsupported_reason` early-return, same pattern)
      is that branch. `is_overview_query()` gates on `set(normalized.intents)
      == {"executive_health"}` (a *pure* overview ask, nothing else
      requested).
- [x] Snapshot read + synthesis — **[Sprint 5 simplification]** deterministic
      template narration straight from `DomainStateSnapshot.headline_signals`
      /`.status` (`overview.py::narrate_overview`), not an LLM call. Doc
      says "LLM synthesis reads the snapshot(s)"; both are equally
      Claim-Gate-safe since the numbers are real either way, this just
      skips a round trip a template already answers. Swap in an LLM
      narration pass later if stakeholders want more natural phrasing.
- [x] **Parallel live-fetch fallback — built differently, same outcome.**
      No new parallel-dispatch/merge system. Instead, `is_overview_query`
      only takes the shortcut for a *pure* overview ask; any query with a
      specific metric/domain/diagnostic intent (a drill-down) never
      qualifies and falls straight through to the existing full live
      pipeline, unchanged. Live-verified: "why did net sales drop this
      week?" classifies to `intents=['diagnostic']`,
      `is_overview_query(...) == False`. Satisfies "a drill-down follow-up
      still gets a live, accurate answer" without a merge system to build
      or test.
- [x] `UNAVAILABLE` handling: `read_overview_snapshots()` returns
      `(fresh_snapshots, unavailable)` — every domain that's missing or
      stale beyond `MAX_SNAPSHOT_AGE_HOURS` (36h — daily cron cadence +
      buffer) lands in `unavailable` with a reason, never silently dropped;
      `build_overview_result` turns that into a `limitations` line per gap
      and downgrades `status` to `partial` whenever any domain is missing.
      **One scope decision, documented in `overview.py`'s module docstring:**
      if *zero* snapshots exist for *any* branch domain (nothing to answer
      from at all — e.g. the scheduler has never run), the fast path is
      skipped entirely and the query falls through to the normal live
      pipeline, rather than returning an all-`UNAVAILABLE` non-answer.

**Exit criteria:** an overview query is answered primarily from stored
snapshots with correct latency improvement vs. full live fan-out, and a
drill-down follow-up still gets a live, accurate answer. **Met,
live-verified end-to-end**: populated real snapshots for all 5 branch
domains via `scheduler.run_once` against live `seleric-mcp`, then ran
`run_swarm_v2_mission(runtime, query="How are we doing today?")` for real —
returned `status=completed`, `team=[]`, all `artifacts` empty (proof the
full LangGraph fan-out never ran), and a `final_response` built entirely
from the live snapshot data (real headline signals for commerce/funnel/
finance that day). A drill-down query ("why did net sales drop this
week?") classified to `diagnostic` only and correctly did not take the
shortcut. 8 new unit tests (`tests/unit/test_overview.py`) cover
`is_overview_query`'s gating, missing/stale detection, narration, and
status rollup. No regressions: 78 passed / 1 pre-existing-flaky deselected
across `tests/coordinator/` + the `business_state`/`domain_health` suites
(confirmed the 2 flaky failures — a CAC diagnostic live-data issue —
reproduce identically on the base branch, unrelated to this sprint).

**[Bug found + fixed, 2026-09-15, #1] The shortcut never fired in production.**
Traced the exact `/v1/missions` request the Swagger UI's own example body
sends for "How are we doing today??" (`full_diagnostic`/`full_prediction`/
`full_skeptic`/`full_strategy` all `true`, `execution_mode=production`) and
found `is_overview_query`'s original `and not forced` clause always
evaluated `forced=True` — `main.py`'s `MissionRequest` defaults all four
flags to `True` for *every* request, not just ones that intend an
escalation, so the "explicit override" theory behind that clause was wrong
for real traffic. Confirmed live, pre-fix: the same query ran the full
174-second pipeline, got misrouted to `commerce_agent` chasing a
`google_clicks` causal hypothesis (nothing to do with the question asked),
produced 308 artifacts, and ended `status=partial` with a REJECTED claim
— a slow, wrong, low-confidence answer to what should be a cheap snapshot
read. Fix: `is_overview_query()` no longer takes a `forced` argument at
all — it now looks only at `normalized.intents` (the classifier's raw,
query-specific output, computed before `apply_full_flags` folds the
API-wide-default booleans in), which is the correct signal a pure
"how are we doing" ask actually took place. Re-verified live post-fix:
same request, `7.4s`, `status=completed`, `team=[]`, correct per-domain
snapshot narration. Regression test added:
`test_is_overview_query_ignores_full_flag_defaults`
(`tests/unit/test_overview.py`).

**[Bug found + fixed, 2026-09-15, #2] The shortcut ignored which domain was
asked about.** Live trace via the actual `/v1/missions` endpoint: "how is
attribution doing" hit the overview shortcut (classifier still returns
`intents=["executive_health"]` for a single-domain health question, same
as a fully generic ask) and answered with the fixed `OVERVIEW_DOMAINS`
5-domain dump (commerce/performance/funnel/finance/operations) —
attribution was never mentioned, because it isn't even in that list, and
the classifier's own `candidate_domains` for that query didn't include it
either. Fix: `overview_domains_for_query()` deterministically checks
whether the query names one of `services.domain_health.scheduler
.ALL_DOMAINS` (all 8, not just the 5-branch template) via word-boundary
match and scopes the snapshot read to just that domain when it does,
falling back to `OVERVIEW_DOMAINS` only for a truly generic ask. No LLM
involved — same deterministic style as `narrate_overview`. Re-verified
live: "how is attribution doing" → `attribution: attributed_revenue_drop:
metric.attributed_net_revenue period_delta_pct -97.6% below threshold
-15.0%` (correct domain, correct real number). 6 new regression tests
(`test_overview_domains_for_query_*` in `tests/unit/test_overview.py`),
including one that checks every `ALL_DOMAINS` entry resolves correctly,
not just the 5 in the executive_health template.

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
