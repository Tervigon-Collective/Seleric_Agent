# 05 - Model & Forecast Validation

Two validators, both keyed off `ForecastArtifact` (a.k.a. `PredictionArtifact`).

## model (`validators/model_validator.py`)

Audits the model behind the forecast against `ModelRegistry`:

| check | failure verdict |
| --- | --- |
| `model_id` + `model_version` present | `MODEL_METADATA_INCOMPLETE` |
| model found in registry | `MODEL_METADATA_INCOMPLETE` |
| registry status in {approved, production} | `MODEL_DEGRADED` |
| `record.target == forecast.target_metric` | `MODEL_OUT_OF_DOMAIN` |
| available history >= `minimum_history_days` | `MODEL_OUT_OF_DOMAIN` |
| backtest metrics available (policy) | `MODEL_DEGRADED` |
| last validated within `model.require_recent_validation_days` | `MODEL_DEGRADED` |
| drift status not in `forecast.drift_reject_statuses` | `MODEL_DRIFTED` |

`MODEL_DRIFTED` / `MODEL_OUT_OF_DOMAIN` / `MODEL_REJECTED` -> blocking `model`
challenge -> **REJECT**. `MODEL_DEGRADED` / `MODEL_METADATA_INCOMPLETE` ->
warning + gap -> **REVISE**.

## Drift interfaces (spec sec. 29)

`drift_status` on the artifact is the integration point for PSI / KS /
Jensen-Shannon / feature / target / residual / calibration drift monitors.
Implement a drift service that stamps `drift_status`; the Skeptic only reads it.
No drift algorithm is reimplemented here.

## forecast (`validators/forecast_validator.py`)

Audits the forecast *statement*:

- **No LLM numeric forecast.** `forecast.llm_generated=True` and
  `forecast.allow_llm_numeric_fallback=False` -> blocking -> **REJECT**.
- missing interval / horizon / backtest metrics -> warnings.
- `applicability_status` in {out_of_domain, regime_shift, not_applicable} ->
  blocking -> **REJECT**.
- interval width vs prediction -> `interval_quality` signal.

## Fallback policy (spec sec. 30)

```
registered production model
    -> approved statistical baseline
    -> INSUFFICIENT_PREDICTIVE_EVIDENCE
```

A `forecast` claim with no `ForecastArtifact` -> `status=INSUFFICIENT`, blocking
gap + blocking `forecasting` follow-up -> **REVISE**.
