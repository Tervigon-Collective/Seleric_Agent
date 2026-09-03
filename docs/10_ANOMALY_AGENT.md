# 10 - Anomaly Agent

## Question

**What changed unusually?**

## LLM role

- choose metric/dimensions/window,
- select or request appropriate anomaly model,
- interpret outputs,
- compare related anomalies,
- generate investigation priorities.

## Model role

- expected baseline,
- deviation magnitude,
- confidence/significance,
- change point or anomaly window,
- relevant dimensions.

## Model router examples

- seasonal robust z-score
- STL residual detection
- EWMA/CUSUM
- Bayesian/change-point detection
- Isolation Forest
- multivariate detector
- domain-specific learned detector

## Artifact fields

See `schemas/anomaly_artifact.schema.json`.

## Rule

The LLM must not label a metric anomalous solely from intuition when a supported quantitative detector is available.
