# 00 - Diagnostic Agent Overview

The Diagnostic Agent answers **"why did it change?"** as an
explicit-hypothesis, test-driven subsystem - never Evidence -> LLM -> root
cause.

## The rule

```
explicit hypotheses  ->  deterministic tests  ->  reject / retain
                     ->  causal estimation + refutation on the survivor
                     ->  root-cause candidate + confidence tier
```

The reasoning model proposes hypothesis *text* and *mechanism* only, and only for
treatment metrics already in evidence or the causal graph. It never decides
retain/reject, never estimates an effect, never states a root cause. With no
reasoning model injected the agent is fully deterministic.

## What it emits (the Skeptic already validates these)

- `DiagnosticArtifact` - hypotheses + retained/rejected + methodology + limitations + `causal_ref`
- `CausalAnalysisArtifact` - treatment/outcome, graph id, common causes, estimator,
  effect, `refutation_results`, `treatment_started_at` / `outcome_started_at`
- `Claim[]` - `claim_type="causal"`, `causal_refs=[...]`, `diagnostic_refs=[...]`,
  `metadata.diagnosed_mechanism`, `metadata.alternatives_ruled_out`

## Boundaries

| Concern | Owner |
| --- | --- |
| Orchestration / state / edges | LangGraph (`graph.py`) |
| Hypothesis generation, mechanism phrasing | LLM (`ReasoningModel`), constrained |
| Test execution (evidence, temporal, segment, control, dose-response) | deterministic runners |
| Causal estimation + refutation | `CausalEstimationService` (template or DoWhy) |
| Agent-to-agent messaging | A2A (`a2a.py`) |

## Read next

- `01_ARCHITECTURE.md` - modules + LangGraph flow + Mermaid
- `02_HYPOTHESIS_MODEL.md` - ontology, generation, ranking
- `03_TESTING.md` - the six deterministic tests + hard gates
- `04_CAUSAL_ESTIMATION.md` - confidence tiers, DoWhy wiring, the metadata ceiling
- `05_INTEGRATION.md` - Coordinator, swarm bridge, Skeptic handoff
