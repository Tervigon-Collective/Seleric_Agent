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
