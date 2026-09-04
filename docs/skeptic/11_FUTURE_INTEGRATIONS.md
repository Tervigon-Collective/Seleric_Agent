# 11 - Future Integrations

The Skeptic is built so the not-yet-existing agents plug in through contracts,
with **no Skeptic redesign**. Rule: define contract + `Protocol` + in-memory
adapter + fixture + documented integration point - never a fake service.

## Diagnostic Agent (does not exist yet)

When built it emits:

```
DiagnosticArtifact   (contracts.py - hypotheses, retained/rejected, methodology,
                      limitations, causal_ref)
CausalAnalysisArtifact
Claim[]              (claim_type="causal", causal_refs=[...])
```

Skeptic consumption today:
- `resolver.py` already loads `claim.diagnostic_refs` -> `DiagnosticArtifact` and
  `claim.causal_refs` -> `CausalAnalysisArtifact` (and falls back to mission-wide
  artifacts of that type).
- `validators/causal_validator.py` audits the causal artifact via
  `CausalValidationService`.
- `validators/strategy_validator._diagnosed_mechanism()` already reads
  `DiagnosticArtifact.retained_hypotheses`.

Integration point: register the Diagnostic agent in
`config/agent_registry.yaml`; have it post `CausalAnalysisArtifact` to the
Blackboard with `treatment_started_at` / `outcome_started_at` /
`refutation_results` populated.

## Prediction Agent (does not exist yet)

Emits:

```
ForecastArtifact / PredictionArtifact   (contracts.py)
Claim[]   (claim_type="forecast", forecast_refs=[...])
```

Required metadata for a forecast to pass the Skeptic:
- `model_id` + `model_version` registered and `status in {approved, production}`
- `feature_set_id` + `feature_set_version`
- `training_window`
- `backtest_metrics` (non-empty) - `model.require_backtest`
- `drift_status` not in `forecast.drift_reject_statuses`
- `applicability_status` in-domain
- a two-element `interval`
- `llm_generated=False` (policy `forecast.allow_llm_numeric_fallback=false`)

Integration point: implement `ModelRegistry` against the real model registry and
a drift service that stamps `drift_status`.

## Strategy Agent

`StrategyArtifact` contract + fixtures exist. Skeptic validates mechanism fit and
business constraints independently via `BusinessRuleService`. Integration point:
implement `BusinessRuleService` against finance / inventory / procurement /
technical constraint stores.

## DoWhy / causal service

`CausalValidationService` Protocol + `BasicCausalValidationService` (metadata
audit) + `UnavailableCausalValidationService`. Integration point: wrap
`seleric_swarm.causal.dowhy_service.DoWhyService` to actually run refuters and
return `CausalValidationResult`.

## Model registry / drift

`ModelRegistry` Protocol + `InMemoryModelRegistry`. Drift is a single
`drift_status` string on `ForecastArtifact`; plug PSI / KS / JS / calibration
monitors behind whatever writes that field. No drift math lives in the Skeptic.

## MCP

`VerificationDataAccess` is intentionally **not** wired to a live MCP client. The
Skeptic's default posture is "emit a `FollowUpTask` for the Coordinator/domain
agent". A future limited read-only verification MCP capability would implement a
`VerificationDataAccess` Protocol and be injected via `SkepticDeps`; no validator
would change.
