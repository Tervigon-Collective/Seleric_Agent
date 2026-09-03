# 12 - Prediction Agent and ML

## Question

**What happens if nothing changes?**

## Flow

```text
Prediction Agent
  -> feature service
  -> model registry
  -> model router
  -> model inference
  -> calibration / intervals
  -> drift check
  -> forecast artifact
```

## Minimum model metadata

- model id/version,
- objective,
- training period,
- feature schema/version,
- last validation date,
- backtest metrics,
- applicability conditions,
- drift status,
- forecast horizon,
- uncertainty interval.

## Fallback hierarchy

1. Valid production model.
2. Approved statistical baseline.
3. Explicit `INSUFFICIENT_EVIDENCE`.

Never use an LLM-generated numerical forecast as an invisible fallback.
