# 20 - Testing and Evaluation

## Test layers

### Unit

Schemas, metric formulas, routers, handoff rules, claim policy.

### Contract

A2A envelopes, Agent Cards, MCP tool contracts, model input/output schemas.

### Integration

Coordinator -> agent -> MCP -> evidence -> claim gate.

### Replay

Replay historical incidents and compare system conclusions with known outcomes.

### Adversarial

- missing data,
- stale data,
- contradictory sources,
- prompt injection in tool data,
- handoff loops,
- malformed agent response,
- causal confounding,
- model drift,
- metric-definition version mismatch.

## Core evaluation dimensions

- factual correctness,
- evidence coverage,
- metric correctness,
- diagnostic precision,
- causal claim discipline,
- forecast accuracy,
- strategy utility,
- calibration,
- unnecessary-agent rate,
- time/cost.

## Golden incident suite

Build 30-50 historical business incidents with expected evidence, acceptable hypotheses and prohibited claims before broad rollout.
