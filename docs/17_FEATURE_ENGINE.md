# 17 - Feature Engine and Feature Store

## Purpose

Support reusable model features for anomaly detection, prediction and causal analysis.

## Feature classes

- raw attributes,
- lags,
- rolling statistics,
- deltas and percentage changes,
- ratios,
- funnel transition rates,
- trend/slope,
- volatility,
- seasonality encodings,
- interaction features,
- contextual/calendar/event features,
- entity-relative features,
- cohort features.

## Governance

Every feature requires:

- feature id/version,
- definition,
- source lineage,
- entity/grain,
- lookback window,
- leakage policy,
- freshness/SLA,
- validation tests.

## LLM role

LLMs may propose candidate features, but proposed features enter a candidate registry and must be deterministically materialized, leakage-checked, evaluated and promoted before production use.
