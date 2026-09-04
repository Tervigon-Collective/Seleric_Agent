# 05 - Integration

## Coordinator boundary

```python
from seleric_swarm.agents.diagnostic import (
    DiagnosticAgent, DiagnosticRequest, DiagnosticDeps,
)
from seleric_swarm.agents.diagnostic.registries import (
    causal_graphs_from_yaml, TemplateCausalEstimationService,
)

agent = DiagnosticAgent(
    deps=DiagnosticDeps(
        evidence_repo=..., artifact_repo=..., anomaly_repo=...,
        causal_graphs=causal_graphs_from_yaml(),
        causal_service=TemplateCausalEstimationService(scenario["causal_truth"]),  # or DoWhyCausalEstimationService()
        reasoning=...,   # optional; NullReasoningModel default
    ),
)

result = await agent.diagnose(DiagnosticRequest(
    mission_id="M-100",
    question="Why did purchase CVR drop?",
    outcome_metric="metric.purchase_cvr",        # optional; intake resolves from anomalies
    anomaly_refs=["AN-11", "AN-12"],
    evidence_refs=["EV-1", "EV-2", "EV-3"],
    lead_domain="technical_agent",
    degradation_started_at="2026-09-01T12:05:00+05:30",
    observations=df,                              # optional pandas frame -> real DoWhy
))
# result.diagnostic_artifact / result.causal_artifact / result.claims
```

## From a live swarm Blackboard

```python
from seleric_swarm.agents.diagnostic.agent import diagnostic_deps_from_blackboard
deps = diagnostic_deps_from_blackboard(blackboard, base=DiagnosticDeps(causal_service=...))
```

## Swarm bridge (opt-in)

`agents/diagnostic/swarm_bridge.py::SwarmDiagnosticSpecialist` matches the swarm
specialist interface and writes `Hypothesis` + `Causal` Blackboard artifacts.

```python
await run_swarm_mission(runtime, query=..., full_diagnostic=True)
```

`full_diagnostic` defaults to `False` (the lightweight in-loop `DiagnosticAgent`
stays default). With `True` the reference mission stays green: Performance ->
Funnel -> Technical leadership, retained frontend-regression hypothesis,
`STRONGLY_SUPPORTED`, Skeptic PASS (`test_reference_mission_full_diagnostic`).
Combine with `full_skeptic=True` to run both subsystems end to end.

## Skeptic handoff

The `Claim` the Diagnostic emits is exactly what the Skeptic validates:

```python
verdict = await SkepticAgent(...).validate_claim(
    SkepticValidationRequest(mission_id=..., claim=result.claims[0],
                             evidence_refs=result.claims[0].support_refs)
)
```

The claim carries `causal_refs=[causal_artifact.causal_id]` and
`diagnostic_refs=[diagnostic_run_id]`, so the Skeptic's `CausalValidator` audits
the real `CausalAnalysisArtifact` (`test_diagnostic_claim_passes_skeptic`).

## A2A

`agents/diagnostic/a2a.py::DiagnosticA2AAdapter` - intents `diagnose`,
`hypothesis_generation`, `causal_diagnosis`, `model_request`, `task_request`.
`as_handler` matches `InProcessTransport`; returns an `artifact_response`
carrying `diagnostic_artifact`, `causal_artifact`, `claims`, `finding`.

## Failure modes

| situation | behaviour |
| --- | --- |
| No anomalies / evidence | outcome falls back to `metric.purchase_cvr`; template hypotheses still generated; likely `inconclusive` |
| No causal graph registered | graph gate fails -> tier capped at `PLAUSIBLE_CAUSAL` |
| Causal truth mismatch (template) | `passed=False` -> `ASSOCIATION_ONLY` / `PLAUSIBLE_CAUSAL` -> `inconclusive` |
| DoWhy unavailable | `DoWhyCausalEstimationService` -> template fallback + limitation; never a fake pass |
| LLM failure | template hypotheses + deterministic pipeline unaffected; a debug log line only |
| Impossible temporal order | `temporal_precedence` hard-gate fail -> hypothesis `rejected`; if it still reached causal, tier `REJECTED` |
