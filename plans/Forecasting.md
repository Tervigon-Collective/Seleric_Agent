# Governed multi-target forecasting on the existing Seleric planner (Chronos-2 + feature framework)

> **Status 2026-10-10:** P0–P4 implemented. How to use (including UI):  
> [`docs/features/forecasting/README.md`](../docs/features/forecasting/README.md).  
> P4: candidate Chronos bundles from the live backtest, calibrated h14 totals, forecast trace, golden questions.  
> Bundles stay `candidate` until a human promotes them.

## Context

Today a question classified `kind="forecast"` gets no plan and no prefetch: `runner.py` only prefetches
`kind=="analysis"`. The agent can call `models.forecast` (`toolsets/models.py`), but that tool:
- needs history the agent already fetched,
- handles one metric only,
- uses ETS with no seasonality,
- returns a single value at day +h.

The Chronos-2 service (`chronos/`) works, but it is standalone and univariate. It sends no covariates and no
co-targets, and accepts no missing values. `models/evaluation.py` scores predictions but never feeds model
selection.

**Goal:** go from a user's forecast question to the right certified target(s) and a validated, leak-free,
quality-gated feature frame, then to Chronos-2 (with statistical fallbacks), and end with per-day P10/P50/P90
plus a traceable artifact and an explanation. Feature bundles are chosen offline by backtests, never by an LLM
during the request.

**Decisions from the interview:**
- **Targets:** certified metrics, discovered dynamically through the catalogue, then filtered by a separate
  eligibility policy.
- **Levels:** account and entity level (channel, platform, campaign, product, SKU), only where the data
  supports it.
- **Derived metrics:** ROAS, MER, AOV and similar are recomposed from component forecasts.
- **Release order:** net_sales and orders first.
- **Known-future inputs:** only a deterministic calendar (India holidays and festivals). Every metric
  covariate is past-only.
- **Output:** per-day bands, backtest accuracy, and a feature contribution that is ablation-based, not causal.
- **Compute:** the current 2 vCPU / 4 GiB CPU box.
- **Latency:** about 30 s end to end.
- **Data lag:** each target has its own `maturity_days`. Context ends at the last mature day.
- **Promotion:** a backtest CLI writes a report and a proposed policy. A human approves it by PR.
- **Horizon:** daily grain, 1–90 days. Weekly and monthly views are aggregated from the daily forecast.
- **Policy location:** eligibility and policy live in an Agent-side registry. Core is unchanged in v1. MCP
  end-user auth is a separate track.
- **Eligible but unapproved targets:** they get a provisional forecast (univariate + calendar, with a quick
  on-request backtest), labelled provisional.

## Chronos-2: what matters for this design

Sources: arXiv 2510.15821 and the amazon-science/chronos-forecasting README.

- **Model:** encoder-only, T5-style, patched inputs. Time attention runs within each series and *group
  attention* across a group of series. That lets one forward pass learn in context from:
  - co-targets (multivariate),
  - past-only covariates,
  - known-future covariates.
- **Zero-shot:** there is no training in our loop. Feature selection therefore means choosing what goes into
  the group, which is exactly what offline backtesting should decide.
- **Context:** up to 8,192 steps (pretrained on 2,048). Two years of daily history (730 days) fits easily.
- **Output:** 21 quantiles (0.01…0.99), so P10, P50 and P90 come for free.
- **Missing values** are masked natively. Masked days (outages, immature tails) should be sent as `null`, not
  dropped or zero-filled.
- **Covariate encoding:** in `predict_df`, any column in `context_df` that is not a target is a past covariate.
  A column that also appears in `future_df` is a known-future covariate. Categorical covariates are supported.
- **Sizes:** `amazon/chronos-2` has 120M parameters. `autogluon/chronos-2-small` has 28M, runs about 2× faster
  and scores within about 1 point on GIFT-Eval. Both fit in 4 GiB.
- **Covariate gains:** the paper reports large gains on retail tasks from promotion and holiday covariates. It
  reports only modest multivariate in-context-learning gains when histories are long. So the default bundle
  is univariate + calendar, and extra metric covariates must earn their place in backtests.
- **Limitations:**
  - Quantiles are per step; there are no sample paths. Horizon totals and ratios therefore need calibration
    (see §7).
  - The paper gives no guidance on when covariates *hurt*, which is another reason for backtest gating.

## End-to-end flow

```
question → understand.py (kind=forecast + ForecastSlots)
        → agent/forecast_plan.py   compile ForecastPlan (targets, entities, horizon, cutoff, policy refs) — code only
        → forecasting/pipeline.py  (deterministic, called from runner prefetch and from the forecast tool)
             policies → candidate features → eligibility/temporal gates → assembler (Cube via MCP)
             → quality gates → engine router (Chronos-2 | ETS | seasonal-naive) → derive/aggregate
             → ForecastArtifact + ForecastTrace + PredictionArtifact(horizon total, back-compat)
        → agent narrates from an ANSWER SKELETON (numbers only from the artifact)
offline: forecasting/backtest CLI → report + proposed policy → PR → config/forecast_policies.yaml
```

## Components

### 1. Understanding: forecast slots — `agent/understand.py`
- Add a `forecast: ForecastSlots | None` field to `Understanding`. It is filled only when `kind=forecast`:
  - `targets` reuses `MetricSlot`.
  - `horizon` has the form `{n, unit: day|week|month, until_iso, period_word: next_n|rest_of_month|next_month|this_quarter}`.
  - The entity comes from the existing `entity_dimension` and `breakdown_dimensions`, plus the scope's
    `value_filters`.
- Add one instruction line. The prompt stays one call.
- New `forecasting/horizon.py` turns the slots into a `[start, end]` window from `as_of`. It uses the IST date
  basis via `services/time_range.py`. The default is 14 days, clamped to the policy's `max_horizon_days`. This
  is needed because the time resolver handles past windows only.

### 2. Forecast plan compiler — new `agent/forecast_plan.py`
`compile_forecast_plan(understanding, catalogue, resolver, as_of, scope) -> ForecastPlan` follows the
`plan_from_slots` pattern: the LLM supplies only slots, and code decides everything else.
- **Metric resolution:** reuse `plan._resolve_metrics` (the catalogue concept resolver), then
  `deps.canonical_metric_id`.
- **Derived targets:** a ratio target that `forecast_policies.derived` maps to components (net_roas, aov, mer,
  conversion_rate) becomes its component targets plus a recomposition step. A ratio with no derivation is
  forecast directly only when policy marks it `direct_ok` (validated against the recomposed version).
- **Eligibility:** each target goes through `policies.eligibility(target, entity)`. The result is
  `validated | provisional | refused(reason_codes)`.
- **Entity dimension:** checked with `catalogue.carries(target, dim)`. Entities are chosen deterministically
  (step 4: top-N by volume and history).
- **Output:** a typed `ForecastPlan`, rendered into the existing advisory plan block and stored via
  `_store_plan_artifact`.

### 3. Policy registry — new `config/forecast_policies.yaml` + `forecasting/policies.py`
- Keyed by **canonical catalogue metric ids**. Validated by a Pydantic schema at load time; an invalid file
  fails CI.
- Rules enforced by the schema:
  - Only `calendar.*` may appear in `known_future`.
  - Every metric id must exist in the catalogue snapshot (checked at warm-up).
- Shape:
```yaml
version: 1
defaults: {grain: day, context_days: 730, min_history_days: 120, max_horizon_days: 90,
           quantiles: [0.1, 0.5, 0.9], provisional: {folds: 4, engine: chronos-2-small}}
promotion: {min_skill_vs_seasonal_naive: 0.0, min_wql_gain_vs_ets: 0.05, min_fold_win_rate: 0.6,
            coverage_80_band: [0.70, 0.90], min_feature_gain: 0.02}
targets:
  net_sales:
    eligibility: {status: validated, maturity_days: 21, nonnegative: true, date_basis: order_date}
    entities:
      lt_platform: {max_horizon_days: 30, max_entities: 6, min_volume_share: 0.05}
    bundles:
      - {id: net_sales.v1, status: approved, engine: chronos-2,
         co_targets: [orders], past_covariates: [ad_spend, sessions],
         known_future: [calendar.dow, calendar.festival, calendar.payday],
         backtest: {report: reports/forecasting/…/net_sales.json, wql: …, mase: …, coverage_80: …,
                    total_error_quantiles: {h7: […], h14: […], h30: […]}},
         approved_by: …, approved_at: …}
derived:
  aov:      {op: ratio, numerator: net_sales, denominator: orders}
  net_roas: {op: ratio, numerator: <attributed revenue id>, denominator: ad_spend}
blocked_features: {<metric_id>: "<reason>"}
```
- `config/model_registry.yaml` keeps governing **engines**. Add `forecast.chronos2` and
  `forecast.chronos2_small` entries, each pinned to a Hugging Face revision sha, plus the existing ETS
  entries. `unbacked_tools()` in `agent/agent.py` shows the forecast tool when any target is
  validated or provisional-eligible.

### 4. Feature framework — new package `src/seleric_swarm/forecasting/`
It is built domain by domain, every decision carries a reason code, and nothing is silent.

- **`features.py`: candidate generation** (deterministic). The sources are:
  - `OntologyService.related_metrics(target)` (`services/ontology.py`, Core `catalogue_related_metrics`),
  - same-module additive metrics,
  - a domain prior file, `config/forecast_features.yaml`.

  The prior file lists which domains may drive which. For example:
  - commerce ← paidmedia (spend, impressions), webanalytics (sessions), calendar;
  - operations/returns ← commerce orders, lagged.

  This only narrows the candidate pool. Backtests make the choice.
- **`gates.py`: eligibility and temporal gates**, each with a reason code:
  - `T_CUTOFF`: every series ends at `cutoff = min(as_of − 1 day, last mature day of target)`. Today, still in
    progress, is never in context.
  - `T_FEATURE_MATURITY`: a covariate's own immature tail is masked to null at the cutoff.
  - `T_FUTURE_ROLE`: a metric can never be known-future. Only generated calendar features can.
  - `T_IDENTITY`: a covariate in the target's `formula.composition` or `depends_on` tree (read through
    `get_metric_definitions`, as `toolsets/composition.py` does) is allowed as a co-target only, never as a
    covariate.
  - `T_DATE_BASIS`: co-targets must share the target's date basis (`catalogue.date_basis_for`). A covariate
    on a different basis is allowed only when policy lists it.
  - `T_GRAIN`: the metric must be queryable at day grain.
  - `T_UNIT`: units must be consistent.
  - `T_CERTIFIED`: `status: certified` is required.
  - `T_POLICY_BLOCKED`.
- **`calendar.py`** plus a versioned **`config/calendar_in.yaml`** with festival and holiday dates for
  2023–2027 (Diwali, Holi, Eid, Raksha Bandhan, national holidays, and brand sale days if added later).
  Features generated: dow, dom, month_end, payday window, festival and pre-festival flags. Calendar features
  are the only known-future covariates.
- **`assembler.py`: point-in-time feature frame.**
  - Fetch daily series for targets and features through `semantic.raw_query_metric` (the single MCP path),
    in parallel. For entity level, use dimensions plus filters, as `executor._with_named_values` does.
  - Reindex to a complete daily index. Missing days become null; never `dropna`. The existing
    `query_metric_series` drops rows and caps at 60 days, so it is not suitable here. Reuse its
    fetch-and-parse helpers (`row_date`) only.
  - Output: `FeatureFrame` (targets, past covariates, future covariates, masks, per-series source queries,
    content hash).
  - Persisted as a `forecast_input` artifact so every forecast is reproducible.
- **`quality.py`: data-quality gates per series.** The verdicts are `block`, `mask` and `warn`.

  | Code | Rule | Action |
  |---|---|---|
  | `Q_STALE` | last data day earlier than expected freshness | block |
  | `Q_SHORT_HISTORY` | fewer than `min_history_days` observed days | block (target) / drop (feature) |
  | `Q_COVERAGE` | under 90% of context days present | block / drop |
  | `Q_OUTAGE_ZERO_RUN` | 2 or more consecutive zeros where the trailing p05 is above 0 | mask as null |
  | `Q_INCIDENT` | range listed in `config/data_incidents.yaml` | mask (versioned, reviewable) |
  | `Q_NEGATIVE` | negative value on a nonnegative metric | block |
  | `Q_OUTLIER` | robust z-score above 6 | warn only (never auto-removed) |
  | `Q_LEVEL_SHIFT` | changepoint in the last 90 days | warn; policy may trim context |
  | `Q_CONSTANT` | zero variance | drop feature |
  | `Q_SPARSE_ENTITY` | zero share above 50%, or volume share below the minimum | refuse entity, offer the aggregate |

- **Entity selection:** top-N entities by volume over the last 90 days that pass the gates. The rest go into a
  `rest` series so entity forecasts sum to the account level. Any account-versus-entity-sum mismatch above
  5% is reported as a warning, not silently reconciled.

### 5. Chronos-2 service v2 — `chronos/app.py`, `chronos/forecast.py`
- Add a new `POST /v2/forecast`. Keep `/predict` unchanged.
  - **Request:** `tasks[]`, each task holding:
    - `task_id`, `start`, `freq: "D"`;
    - `targets: {name: [float|null]}`;
    - `past_covariates: {name: [float|null]}`;
    - `future_covariates: {name: {past: [...], future: [...]}}`.

    Also at request level: `prediction_length`, `quantile_levels`, `model: chronos-2|chronos-2-small`.
  - **Validation:** equal lengths per task, finite or null values, a future length equal to
    `prediction_length`, and caps on targets plus covariates per task and on tasks per request.
  - **Mapping:** build `context_df` (id, timestamp, targets, covariates) and `future_df` (id, timestamp,
    future covariates), then call `predict_df(..., target=[...])`.
  - **Response:** per task, per target, per step: point forecast plus the requested quantiles. Also return
    `model_id`, `revision`, `input_hash` and `inference_seconds`. Keep the quantile-ordering check.
- Pin the model revision with `MODEL_REVISION`. Load both base and small once (each loaded lazily, then
  cached), which fits in 4 GiB.
- Replace the immediate 429 with a bounded wait queue (`QUEUE_WAIT_SECONDS`, for example 15 s). A
  provisional backtest batches all its folds as tasks in **one** request.
- Extend `chronos/benchmark.py` to cover the v2 payload on the box: 730-day context, groups of 1–6, the base
  and small models. That sets the per-request series caps that fit the 30 s budget.

### 6. Engines and router — `forecasting/engines.py`
- **`ChronosEngine`:** an httpx client to `/v2/forecast`, with timeout, one retry on 429, and a circuit
  breaker. The URL comes from settings.
- **`EtsEngine`:** extend `models/service.py` with `forecast_path()`. It returns per-day point plus interval,
  with additive weekly seasonality once history is 28 days or more. `forecast_series` stays for back-compat.
- **`SeasonalNaiveEngine`** (m=7): always computed in backtests, as the MASE scale and the skill reference.
- **Router ladder,** aligned with `prediction_policies.yaml`'s `fallback_order`:
  1. Approved bundle on its engine.
  2. Provisional univariate Chronos with calendar features.
  3. ETS, when it is approved for the target.
  4. Refuse with reason codes.

  The engine that ran and the reason for any fallback are recorded in the trace.

### 7. Derivation and aggregation — `forecasting/derive.py`, `forecasting/aggregate.py`
- **Horizon totals and week/month roll-ups:**
  - Point = sum of the daily points.
  - Interval = the point × (1 + q) at the **backtest-calibrated relative-error quantiles** for that horizon
    (`total_error_quantiles`, conformal).
  - When no calibration exists (provisional), use the comonotonic sum of the daily quantiles, which is
    conservative, and warn.
- **Derived ratios:**
  - Point = ratio of the component points.
  - Interval from the calibrated errors of the derived metric in backtests. Otherwise use bounds from the
    component quantiles, labelled approximate.
- Everything is computed in code, never by the LLM. The existing `numeric_audit` and `claim_gate` see only
  artifact numbers.

### 8. Offline backtest and promotion — `forecasting/backtest.py` (+ `__main__` CLI), extend `models/evaluation.py`
- **Rolling origin:** a cutoff every 7 days over the last 180–365 days, with each fold's actuals limited to
  mature days. Covariate maturity masking is applied per fold with the same `gates.py` code as live, so
  backtests and live see identical inputs.
- **Metrics:** add `wql()`, `mase()`, `coverage()`, `bias()` and `horizon_total_ape()` to
  `models/evaluation.py` (pure functions, beside `summarize`).
- **Search:** baselines (seasonal-naive, ETS) → Chronos univariate → + calendar → greedy forward selection
  over candidate covariates and co-targets from §4, up to 4 added series. Run on both chronos-2 and
  chronos-2-small. A feature is kept only if it passes the `promotion.min_feature_gain` threshold and the
  fold win-rate.
- **Ablation:** the leave-one-out deltas recorded for each selected feature become the "how much each input
  helped" explanation at answer time.
- **Output:**
  - `reports/forecasting/<date>/<target>[__<entity>].json` and `.md` (folds, metrics, selection path,
    rejected candidates with reason codes);
  - a proposed YAML bundle to stdout, which a human commits by PR.
- **Compute isolation:** the CLI takes `--chronos-url` so it runs against a one-off container from the same
  image (`docker compose -p seleric_chronos_bt run …`) and never starves the live service.
- **Point-in-time caveat:** first verify whether the warehouse keeps revision snapshots. If it does not,
  backtests use today's values for past days. Maturity masking reduces that bias, and the report states it.

### 9. Pipeline, tool, runner and artifacts
- **`forecasting/pipeline.py`:** `run_forecast(deps, plan) -> ForecastOutcome` runs §3–§7, with a time budget
  per stage and fail-closed numbers (refuse rather than guess). It emits progress through
  `emit_progress`.
- **Tool:** new `toolsets/forecasting.py: forecast_metrics(ctx, targets, horizon_days, entity_dimension=None,
  entity_values=None)`. It calls the same compiler and pipeline. This allows follow-ups such as "and Meta
  only?". It fetches the same way `diagnosis` and `composition` do.

  `models.forecast` stays registered for the evidence-only ETS path and is hidden when `forecast_metrics` is
  available, so the model sees one forecast tool.
- **Runner** (`agent/runner.py` around the prefetch block, lines ~1336–1360):
  - When `understanding.kind == "forecast"`: compile, run the pipeline, and append the ANSWER SKELETON
    (a compact table: daily bands, totals, features used, accuracy, status, warnings).
  - Narrow tools to `forecast_metrics`, `generate_visualization` and the data tools.
  - The `forecast` budget (250) stays.
- **Contracts:**
  - New `ForecastArtifact` (`agent/artifacts.py`, `artifact_type="forecast"`, derived). It holds:
    - the plan spec, cutoff and maturity cut;
    - target definitions (id, label, unit, date basis);
    - entity;
    - features with role, bundle id and version, and ablation gain;
    - quality verdicts;
    - engine, model id, version and revision;
    - per-day points (date, mean, p10, p50, p90), totals and derived metrics;
    - backtest summary (policy report or provisional) and `status: validated|provisional`;
    - warnings, input-artifact id and hash, evidence ids.

    Also write a `PredictionArtifact` for the horizon total so the existing `check_prediction` validator and
    `evaluation.py` keep working. Add `score_forecast_artifact()` to pair each day with later actuals.
  - Mirror the type in `conversations/contracts.py` and the office-ui `api/contracts.ts`. Render it as a fan
    chart, reusing the existing chart artifact path, in phase 3.
- **Debuggability:**
  - Persist a `ForecastTrace` artifact (stage timings, every gate decision with its code, candidate →
    selected/rejected features, router decisions).
  - Log `forecast_stats` one line per stage, like `planner_stats`.
  - Add CLI commands `python -m seleric_swarm.forecasting run --target net_sales --as-of 2026-10-01 --dry-run`
    (prints frame, gates and the Chronos payload) and `explain <artifact_id>`. The second rebuilds the
    forecast from the stored input hash.

## Phases (release order)
1. **P0, net_sales and orders at account level:** ✅ **done**
   - contracts; policy registry and schema; `calendar_in.yaml`; horizon; gates; quality; assembler;
   - `forecast_plan.py`; pipeline; runner branch; `ForecastArtifact`;
   - ETS `forecast_path` and the Chronos univariate path through the existing `/predict`.
2. **P1:** ✅ **mostly done** (bundles still candidate until backtest PR)
   - Chronos `/v2/forecast` (covariates, co-targets, nulls, queue, pinned revision) and the benchmark;
   - backtest CLI and evaluation metrics;
   - first approved bundles for net_sales and orders (by PR); calibrated totals.
3. **P2:** ✅ **done**
   - the provisional path for any eligible certified metric;
   - derived recomposition (AOV, ROAS, MER, conversion rate);
   - entity level: platform/channel first, then campaign, product and SKU with sparse-entity refusal;
   - the `forecast_metrics` tool for follow-ups; the UI fan chart.
4. **Follow-ups (phase 3):** ✅ **done**
   - scheduled re-validation and scoring job (`python -m seleric_swarm.forecasting.score --if-due`, cron);
   - catalogue `raw.forecast` eligibility overrides the Agent YAML registry when Core carries it;
   - forecast fetches pin `workspace_config.brand_id` when the principal's workspace declares one.
5. **Phase 4:** ✅ **done**
   - candidate bundles for `net_sales` and `orders` from the 2026-10-10 backtest (not approved; a human promotes by PR);
   - horizon totals use those bundles' `h14` error quantiles;
   - `forecast_trace` artifact; `python -m seleric_swarm.forecasting explain <id-or-path>`;
   - golden questions in `eval/datasets/forecast_questions.jsonl`.

## Reuse (do not rebuild)
- **Resolution and catalogue:** `agent/plan.py` (`_resolve_metrics`, `plan_from_slots` pattern) and
  `services/catalogue_bootstrap.py`:
  - `CatalogueSnapshot.carries`, `.date_basis_for`, `.aggregation_for`, `.volume_metric_for`,
    `.supported_dimensions_for`.
- **Data access:** `toolsets/semantic.py`:
  - `raw_query_metric` is the single MCP fetch path;
  - `get_metric_definitions` batches definitions;
  - `query_metric_series` has reusable row parsing only.
- **Ontology and composition:** `services/ontology.py` (`related_metrics`) and `toolsets/composition.py`
  (`_composition` tree walk, for the identity gate).
- **Execution helpers:** `agent/executor.py` (`_ctx`, `_with_named_values`, parallel fetch pattern) and
  `agent/progress.emit_progress`.
- **Models and evaluation:** `models/service.py` (registry, ETS) and `models/evaluation.py` (scoring
  primitives).
- **Answer checks:** `services/numeric_audit.py` and `services/claim_gate.py`.
- **Config:** `config/model_registry.yaml` (engines) and `config/prediction_policies.yaml` (fallback order,
  interval rules).

## Verification
- **Unit** (`tests/unit/forecasting/`):
  - every gate and reason code on synthetic series (outage zero-runs, negatives, immature tail, stale,
    sparse entity);
  - a **leakage canary**: a covariate equal to the target shifted −k days must be masked or blocked at the
    cutoff, and a metric declared `known_future` must fail schema load;
  - horizon parsing; derived recomposition; conformal totals; router fallback with Chronos down (httpx mock);
  - schema validation of `forecast_policies.yaml` against the live catalogue snapshot.
- **Chronos:**
  - extend `chronos/test_forecast.py` for `/v2/forecast`: a synthetic target driven by a lagged covariate
    must improve WQL with the covariate; null handling; the future-length check;
  - run `chronos/benchmark.py` on the box to confirm the payload caps fit about 30 s.
- **Backtest:** `python -m seleric_swarm.forecasting.backtest --target net_sales` and `--target orders`.
  Review the reports, then commit the bundles.
- **Evals:** add golden forecast questions to `src/seleric_swarm/evals`:
  - "forecast net sales for the next 14 days";
  - "orders next month by platform";
  - "what will AOV be next week" (derived);
  - "forecast sales for <sparse SKU>" (refusal);
  - "forecast refunds" (maturity).

  Run through the live API, and check the artifact fields, provenance, status label and latency of 30 s or
  less.
- Run the existing suites (`pytest tests/unit tests/api`, office-ui `vitest`) to confirm no regressions in the
  analysis path.

## Notes
- The local `Seleric_Agent_Core` checkout is `main@e702668`, not the reviewed `6f056e05`. Pull before relying
  on `catalogue_related_metrics` and ontology behaviour.
- Core is not edited in v1. `catalogue_v2/` is generated, so a later Core migration goes through
  `catalogue_v2_src` and `scripts/build_catalogue_v2.py`.
