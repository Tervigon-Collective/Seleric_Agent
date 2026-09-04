# 04 - Integration

## Coordinator boundary

```python
from seleric_swarm.agents.prediction import (
    PredictionAgent, PredictionRequest, PredictionDeps,
)
from seleric_swarm.agents.prediction.services import (
    model_registry_from_yaml, feature_store_from_yaml,
)

agent = PredictionAgent(
    deps=PredictionDeps(
        evidence_repo=..., artifact_repo=...,
        model_registry=model_registry_from_yaml(),   # config/model_registry.yaml
        feature_store=feature_store_from_yaml(),
        model_service=MyFittingModelService(),        # or TemplateForecastModelService(truth)
        # baseline / drift_monitor / reasoning are optional
    ),
)

result = await agent.predict(PredictionRequest(
    mission_id="M-100",
    target_metric="metric.cac",       # optional; intake resolves from anomalies
    horizon="7d",                     # optional; policy default
    evidence_refs=["EV-1", "EV-2"],
    causal_refs=["CAUS-3"],           # drives scenario spread (cause persistence)
    history=[...],                    # optional; enables the statistical baseline
    context={"applicability_status": "in_domain", "drift_status": "green"},
))
# result.forecast_artifact / result.scenarios / result.claims / result.confidence
```

`result.has_forecast()` is `False` for `INSUFFICIENT_PREDICTIVE_EVIDENCE`.

## From a live swarm Blackboard

```python
from seleric_swarm.agents.prediction.agent import prediction_deps_from_blackboard
deps = prediction_deps_from_blackboard(blackboard, base=PredictionDeps(model_service=...))
```

## Swarm bridge (opt-in)

`agents/prediction/swarm_bridge.py::SwarmPredictionSpecialist` matches the swarm
specialist interface and writes a `Prediction` Blackboard artifact.

```python
await run_swarm_mission(runtime, query=..., full_prediction=True)
```

Default `False` (the lightweight in-loop `PredictionAgent` stays default). With
`True` the reference mission stays green: `status=completed`, a
`registered_model` forecast for `metric.cac` at `MODERATE` confidence
(`test_reference_mission_full_prediction`). In fixture/replay mode the bridge
registers the scenario's declared model as `approved` and routes through the
template model service.

Combine `full_diagnostic=True, full_prediction=True, full_skeptic=True` to run
all three subsystems end to end (`test_reference_mission_all_three_subsystems`).

## Skeptic handoff

The forecast `Claim` is exactly what the Skeptic's model + forecast validators
consume: `forecast_refs=[forecast_id]`, `model_refs=[model_id]`. A registered,
approved, backtested, in-domain, non-drifted forecast with a 2-element interval
and `llm_generated=False` clears both validators
(`test_forecast_claim_passes_skeptic`). A synthetic-input run is capped at
`REVISE` by the Skeptic's synthetic-provenance rule, not by a model/forecast
failure.

## A2A

`agents/prediction/a2a.py::PredictionA2AAdapter` - intents `predict`, `forecast`,
`risk_prediction`, `model_request`, `task_request`. `as_handler` matches
`InProcessTransport`; `produced` is `forecast_artifact` or
`insufficient_predictive_evidence`.

## Failure modes

| situation | behaviour |
| --- | --- |
| No registered model for the target | ladder falls to the baseline; reason recorded |
| Model exists but `candidate` / stale / no backtest / no feature set | skipped with a reason; ladder continues |
| No model and < `min_history_points` of history | `INSUFFICIENT_PREDICTIVE_EVIDENCE`, no claim |
| Model service returns `None` | reason recorded; ladder continues |
| `drift_status` red / `applicability` out_of_domain / regime_shift | tier forced to `INSUFFICIENT`, no claim (artifact still built for the Skeptic to see if a caller wants it) |
| No prediction interval | `WEAK` + limitation |
| LLM narrative fails | numbers unaffected; a warning log only |
