# Governed forecasting (Chronos-2 + feature framework)

Natural-language forecast questions → certified targets → leak-free feature
frame → Chronos-2 (or ETS fallback) → per-day P10/P50/P90 + `ForecastArtifact`.

Design plan: [`plans/Forecasting.md`](../../../plans/Forecasting.md).

## Status (2026-10-10)

| Phase | Scope | Status |
| --- | --- | --- |
| **P0** | Account-level `net_sales` / `orders`; slots; policy; gates; assembler; pipeline; runner prefetch; ETS path; Chronos `/predict` | **Done** |
| **P1** | Chronos `/v2/forecast` (covariates, nulls, queue); engine prefers v2; backtest CLI scaffold; eval primitives (`wql`/`mase`/…) | **Done** (approved bundles still need a live backtest PR) |
| **P2** | `forecast_metrics` tool; entity level; provisional for any certified metric; derived MER/CVR; UI fan chart | **Done** |
| **P3** | Daily score job; catalogue `forecast` eligibility; workspace brand pin | **Done** |
| **P4** | Candidate Chronos bundles + h14 calibration; forecast trace; explain CLI; golden questions | **Done** (bundles stay candidate until a human promotes them) |

## Ask through the UI

**Yes.** Use the normal conversation shell (Office UI → thread composer). There
is no separate forecast screen.

1. Open the UI against a live API (`SELERIC_API_URL=… npm run dev`, or your
   deployed host).
2. Ask in plain language, for example:
   - `forecast net sales for the next 14 days`
   - `what will orders be next week`
   - `forecast net sales by platform for the next 14 days`
   - `what will AOV be next week`
3. The mission path is the same as any other question
   (`POST /v1/threads/.../messages` → `run_v3_mission`).
4. When understand sets `kind=forecast`, the runner:
   - compiles a `ForecastPlan` (code only),
   - runs `forecasting.pipeline.run_forecast`,
   - prepends an **ANSWER SKELETON** (numbers only from the artifact),
   - lets the agent narrate from that skeleton.
5. Follow-ups ("and Meta only?") use the `forecast_metrics` tool against the
   same pipeline.

**What you see today**

- A normal assistant text answer with daily bands / horizon totals when the
  pipeline succeeds.
- A **fan chart** (`chart_spec`: actuals + P50 + P10–P90 ribbon) attached to
  the answer; `ForecastArtifact` is typed in office-ui contracts.
- Office walk animation may route the avatar to the forecast station when the
  tool/intent looks like forecast.
- Entity-level forecasts (platform/channel/campaign/…) with sparse-entity
  refusal; derived ratios (AOV, ROAS, MER, conversion rate) recomposed in code.
- If history is too short, Chronos is down, or quality gates block, the answer
  **refuses** with reason codes (no invented numbers).

**What must be up**

| Dependency | Role |
| --- | --- |
| API + MCP (`seleric-mcp`) | Daily series via `raw_query_metric` |
| `CHRONOS_BASE_URL` (optional) | Chronos service, e.g. `http://127.0.0.1:8112` — if unset, ETS fallback |
| Chronos container | `docker compose -p seleric_chronos -f chronos/docker-compose.yml up -d` |
| Policy | `config/forecast_policies.yaml` — `net_sales` / `orders` are `validated` |

## End-to-end flow

```text
question
  → understand.py (kind=forecast + ForecastSlots)
  → agent/forecast_plan.py     compile ForecastPlan
  → forecasting/pipeline.py    policies → frame → gates → Chronos|/predict|ETS
  → ForecastArtifact + PredictionArtifact (horizon total)
  → ANSWER SKELETON in the mission prompt → agent narrates
```

Offline:

```bash
python -m seleric_swarm.forecasting run --target net_sales --as-of 2026-10-01 --dry-run
python -m seleric_swarm.forecasting.backtest --target net_sales --dry-run
```

## Key files

| Path | Role |
| --- | --- |
| `config/forecast_policies.yaml` | Eligibility + bundles (Agent-side) |
| `config/calendar_in.yaml` | India holidays / festivals (only known-future covariates) |
| `config/forecast_features.yaml` | Domain priors for candidate covariates |
| `src/seleric_swarm/forecasting/` | Horizon, gates, quality, assembler, entities, engines, pipeline |
| `src/seleric_swarm/agent/forecast_plan.py` | Slot → typed plan |
| `src/seleric_swarm/toolsets/forecasting.py` | `forecast_metrics` follow-up tool |
| `src/seleric_swarm/forecasting/score.py` | Daily re-score of stored forecasts vs matured actuals |
| `chronos/app.py` | `/predict` + `/v2/forecast` |
| `agent/artifacts.py` | `ForecastArtifact` |

## Policy notes

- Targets are keyed by **catalogue** ids (`net_sales`, `orders`), not `metric.*`.
- Only `calendar.*` may appear in `known_future` (schema-enforced).
- Bundles stay `candidate` until a backtest report is reviewed and promoted by PR.
- Derived ratios (`aov`, `net_roas`) recompose from component forecasts in code.
- **Maturity** (`maturity_days: 21` for net_sales/orders) only moves the cutoff
  back so immature order-date tails are out of context. It is not a coverage rule.
- **Q_COVERAGE** measures density from the *first observed day* to cutoff (leading
  empty days from a long `context_days` request no longer fail the gate). Recent
  90 days must be ≥90% present (block); full span only blocks below 80% and
  warns between 80–90% (live net_sales was ~89.8% over two years).

## Tests

```bash
.venv/bin/pytest tests/unit/forecasting/ tests/unit/test_understand.py -q
python -m seleric_swarm.forecasting.score --if-due
```

A catalogue metric may carry a `forecast` block (`status`, `maturity_days`,
`entities`). When it does, that block decides eligibility and the YAML
registry is the fallback. A workspace `brand_id` on the mission context pins
every forecast fetch to that brand.

## Dataset preparation, feature engineering and validated features

Every forecast runs these stages in order (`forecasting/dataset.py` records them
in the answer's evidence):

1. frame (target, daily grain, date basis, horizon)
2. window: max history (policy `context_days`, min `min_history_days`) ending at
   `cutoff_lag_days` before the forecast date
3. regularise: continuous daily index, nulls never zero-filled, the cutoff-to-horizon
   gap kept as unobserved days
4. clean: outages / incidents / dips masked, outliers flagged
5. align: candidate covariates pass the same cutoff and gates (no look-ahead)
6. engineer: calendar known-future features (weekday, payday, month-end, festival,
   pre-festival); no manual lags - Chronos-2 attends over the context
7. select: `forecasting/bakeoff.py` runs Chronos-2 on 12 held-out origins with and
   without each candidate covariate and keeps a feature only if it lowers the error
   by >= 3%. Results are cached 6 h per target/cutoff.

Chronos-2 is the primary engine (`CHRONOS_BASE_URL`, default
`http://seleric_agent-chronos-1:8112`); ETS with a backtest-selected driver level is the
fallback when the service is unavailable.

## Evidence-toolset backends

`forecast_metrics` (above) is the conversation path. `models/service.py::forecast_series`
is the evidence-id path used by `toolsets/models.py`. It owns the guards (horizon, nulls,
in-progress periods, outlier replacement, minimum rows, non-finite check, collapsed-interval
warning, rounding). A **backend** (`models/backends/`) does only the math and returns
`BackendForecast(point, lower, upper, method)`.

| backend | module | dependency | status |
|---|---|---|---|
| `holt` | `backends/holt.py` | statsmodels (core) | baseline, approved for 4 metrics |
| `statsforecast` | `backends/statsforecast_backend.py` | `forecast-stats` extra | candidate |
| `seasonal_naive` | `backends/seasonal_naive.py` | none | benchmark only |
| `auto` | `backends/auto.py` | holt always; statsforecast when installed | approved as `forecast.generic.daily` |
| `<name>+conformal` | `backends/conformal.py` | none | opt-in wrapper, not enabled |

`forecast_path` in the same module is the per-day ETS path the Chronos fallback uses.
It is not a backend.

## Registry fields (`config/model_registry.yaml`)

- `backend:` — backend name (default `holt`).
- `fallback:` — model id of another **approved** record for the same target. Used only
  when the primary raises `policy:model_unavailable` (dependency/weights missing). The
  stored `PredictionArtifact` names the model that actually produced the number, and a
  warning records the fallback.
- Candidates stay `status: candidate` (refused by `toolsets/models.py`) until promoted.
- `forecast.generic.daily` (`target: "*"`, `backend: auto`) covers metrics with no
  dedicated entry. It backtests holt, statsforecast and seasonal-naive on the requested
  series and records the scores on `PredictionArtifact.method_diagnostics`.

## Go/no-go rule for promoting a candidate backend

Run `python -m scripts.backtest_forecasters --csv <history.csv>` (columns
`date,metric,value`; `--synthetic` for a smoke test). A candidate is promotable for a metric only if

- MASE beats Holt by >= 5% at **both** h=7 and h=14, and
- 95% interval coverage is within [0.90, 0.98].

Promotion is a human edit: set `status: approved`, `last_validated_at`, and attach the
backtest report. Nothing promotes automatically.

## Findings (2026-09-29, 365d real history, brand 20; net_sales/orders/sessions/spend)

- Rolling-origin backtest (h=1,7,14): Holt, seasonal-naive and AutoETS are within a few %
  of each other at h=7/14 (MASE > 1 for all, i.e. no better than repeating last week).
  No candidate met the go/no-go rule, so Holt stays the approved per-metric model.
- Holt intervals over-cover (0.98-1.00 at h>=7); seasonal-naive/ETS under-cover (0.73-0.91).
- `holt+conformal` narrows intervals 25-50% and fixes net_sales coverage (0.91-0.94), but
  under-covers orders/sessions/spend at longer horizons (0.83-0.85) and costs ~300 ms/fit.
  Kept available, not enabled.
- That backtest did not justify a torch model inside `models/backends/`. Chronos-2 above
  is a separate service, not that backend.
- Run backtests with `.venv\Scripts\python -m scripts.<name>` (not `uv run`, which re-syncs
  and can fail on locked DLLs on Windows). Data: `scripts/export_history.py` -> `history.csv`.
