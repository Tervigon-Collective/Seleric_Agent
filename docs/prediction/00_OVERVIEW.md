# 00 - Prediction Agent Overview

The Prediction Agent answers **"what happens next if this continues?"** as a
**forecast orchestration** subsystem - not a forecasting LLM.

## The rule

```
registered production model   ->   approved statistical baseline   ->   INSUFFICIENT_PREDICTIVE_EVIDENCE
```

Every number - point, interval, horizon, scenario - comes from a registered
model or an approved deterministic statistical baseline. The reasoning model may
write a short plain-language reading of a forecast that already exists; it never
produces a number (`allow_llm_numeric_fallback: false`). When neither the model
nor the baseline can serve the target, the result is
`INSUFFICIENT_PREDICTIVE_EVIDENCE` with **no claim**.

## What it emits (the Skeptic already validates these)

- `ForecastArtifact` - target, prediction, interval, horizon, `model_id` +
  `model_version`, `feature_set_id`, `training_window`, `backtest_metrics`,
  `drift_status`, `applicability_status`, `llm_generated=False`
- `Claim[]` - `claim_type="forecast"`, `forecast_refs=[...]`, `model_refs=[...]`,
  `metadata.predictive_confidence`, `metadata.source`, `metadata.applicability`
- `ScenarioProjection[]` - base / optimistic / pessimistic, each a function of the
  model interval + how strongly the diagnosed cause is expected to persist

## Confidence tiers

```
INSUFFICIENT_PREDICTIVE_EVIDENCE
WEAK
MODERATE
STRONG        (registered model + tight interval + backtest MAPE below threshold)
```

## Boundaries

| Concern | Owner |
| --- | --- |
| Orchestration / state | LangGraph (`graph.py`) |
| Which model / baseline | `model_selection.py` fallback ladder |
| The numbers | `ForecastModelService` (registered) or `StatisticalBaselineForecaster` (approved fallback) |
| Regime / in-domain check | `applicability.py` |
| Narrative only (never numbers) | LLM (`ReasoningModel`) |

## Read next

- `01_ARCHITECTURE.md` - modules + LangGraph flow + Mermaid
- `02_FALLBACK_LADDER.md` - model gates, baseline, INSUFFICIENT
- `03_APPLICABILITY_AND_SCENARIOS.md` - regime check + scenario construction
- `04_INTEGRATION.md` - Coordinator, swarm bridge, Skeptic handoff
