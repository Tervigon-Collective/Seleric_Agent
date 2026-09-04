# 02 - The Fallback Ladder

`model_selection.select_and_forecast` walks `policies.fallback_order`
(default `[registered_model, statistical_baseline, insufficient]`) and returns
the first `ForecastRun` it can produce.

## Rung 1 - registered production model

A `ModelRecord` (from the `ModelRegistry` port) whose `target` matches, gated by
`prediction_policies.yaml -> model`:

| gate | skip reason recorded |
| --- | --- |
| `status` in `require_status` (default `approved`, `production`) | `status '<x>' not in [...]` |
| `require_backtest` and `backtest_available` | `has no backtest metrics` |
| `last_validated_at` within `require_recent_validation_days` | `last validated <n>d ago` |
| `require_feature_set` and `FeatureStore.resolve(model_id)` returns a set with no missing features | `no feature set registered` / `missing [...]` |

If all gates pass, `ForecastModelService.forecast(ForecastModelQuery)` is called.
`TemplateForecastModelService` (default, offline) returns a scenario
`forecast_truth` dict; a production service fits/serves the real model.

## Rung 2 - approved statistical baseline

`StatisticalBaselineForecaster` - deterministic, no LLM. `method` in
`{drift_projection (default), last_value, linear_trend}`. Requires
`baseline.min_history_points` (default 8) points of history and
`baseline.approved: true`. Interval is `+/- z * residual_std * sqrt(steps)` of
the fitted method (`interval_z` default 1.28 ~ 80%). `backtest_metrics` carries
the in-sample MAPE.

History is taken from `request.history` if given, else synthesised in `intake`
from `current_value` + `change_pct` (`_synth_history`).

## Rung 3 - INSUFFICIENT

`synthesis.build_insufficient` -> `PredictionResult` with
`source="insufficient"`, `confidence="INSUFFICIENT_PREDICTIVE_EVIDENCE"`,
**no `ForecastArtifact`, no `Claim`**, and the full list of skip reasons in
`limitations`.

## Confidence tier (`synthesis._confidence`)

| condition | tier |
| --- | --- |
| applicability in reject statuses, or drift in reject statuses | `INSUFFICIENT_PREDICTIVE_EVIDENCE` |
| no interval and `interval.required` | `WEAK` |
| `registered_model` + tight interval + backtest MAPE < `strong_requires_backtest_mape_below` | `STRONG` |
| `registered_model` or `statistical_baseline` + tight interval | `MODERATE` |
| otherwise | `WEAK` |

"tight" = relative interval width `|hi-lo| / (2*|prediction|)` ≤
`interval.max_relative_width` (default 0.6).
