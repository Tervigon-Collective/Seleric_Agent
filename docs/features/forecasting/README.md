# Forecasting backends

`models/service.py::forecast_series` is the single entrypoint. It owns the guards
(horizon, nulls, minimum rows, non-finite check, collapsed-interval warning,
rounding). A **backend** (`models/backends/`) does only the math and returns
`BackendForecast(point, lower, upper, method)`.

| backend | module | dependency | status |
|---|---|---|---|
| `holt` | `backends/holt.py` | statsmodels (core) | baseline, approved for 4 metrics |
| `statsforecast` | `backends/statsforecast_backend.py` | `forecast-stats` extra | candidate |
| `seasonal_naive` | `backends/seasonal_naive.py` | none | benchmark only |
| `<name>+conformal` | `backends/conformal.py` | none | opt-in wrapper, not enabled |
| `timesfm`, `chronos` | not yet implemented | torch extra | only if backtest justifies |

## Registry fields (`config/model_registry.yaml`)
- `backend:` — backend name (default `holt`).
- `fallback:` — model id of another **approved** record for the same target. Used only
  when the primary raises `policy:model_unavailable` (dependency/weights missing). The
  stored `PredictionArtifact` names the model that actually produced the number, and a
  warning records the fallback.
- Candidates stay `status: candidate` (refused by `toolsets/models.py`) until promoted.

## Go/no-go rule for promotion (fixed in advance)
Run `uv run python -m scripts.backtest_forecasters --csv <history.csv>` (columns
`date,metric,value`; `--synthetic` for a smoke test). A candidate is promotable for a metric only if
- MASE beats Holt by >= 5% at **both** h=7 and h=14, and
- 95% interval coverage is within [0.90, 0.98].

Promotion is a human edit: set `status: approved`, `last_validated_at`, and attach the
backtest report. Nothing promotes automatically.

## Findings (2026-09-29, 365d real history, brand 20; net_sales/orders/sessions/spend)
- Rolling-origin backtest (h=1,7,14): Holt, seasonal-naive and AutoETS are within a few %
  of each other at h=7/14 (MASE > 1 for all, i.e. no better than repeating last week).
  No candidate met the go/no-go rule -> **Holt stays the approved model; no registry change.**
- Holt intervals over-cover (0.98-1.00 at h>=7); seasonal-naive/ETS under-cover (0.73-0.91).
- `holt+conformal` narrows intervals 25-50% and fixes net_sales coverage (0.91-0.94), but
  under-covers orders/sessions/spend at longer horizons (0.83-0.85) and costs ~300 ms/fit.
  Kept available, not enabled. Decision: option 1 (keep plain Holt).
- TimesFM/Chronos deliberately not pursued: gains over simple baselines are absent at
  these horizons; revisit with longer/richer history.
- Run backtests with `.venv\Scripts\python -m scripts.<name>` (not `uv run`, which re-syncs
  and can fail on locked DLLs on Windows). Data: `scripts/export_history.py` -> `history.csv`.

## Not done yet
- Live-MCP data source for the backtest (CSV/synthetic only).
- TimesFM / Chronos backends and their extra.
- Prediction-vs-actual feedback job (`models/evaluation.py` is the measurement half only).
