# 32 - Model and Feature Registries

## Model registry entry

Each production model should record:

- id/version,
- owner,
- task type,
- target,
- supported entities/domains,
- training window,
- feature-set version,
- validation metrics,
- calibration information,
- applicability conditions,
- drift thresholds,
- fallback policy,
- artifact location,
- status: candidate/staging/production/disabled.

## Feature registry entry

Each feature should record:

- id/version,
- entity/grain,
- source lineage,
- transformation,
- lookback,
- leakage classification,
- freshness SLA,
- validation,
- owners,
- consumers.

An example is in `config/model_registry.example.yaml`.
