# 03 - Coordinator Design

## Coordinator is a control plane

The Coordinator should not compete with domain agents for analytical leadership.

## Mission planning flow

```text
User query
  -> normalize dates/entities/metrics
  -> classify query type
  -> decompose into atomic questions
  -> determine evidence requirements
  -> create task DAG
  -> select minimum agent set
  -> select initial mission lead
  -> execute
```

## Query classes

1. **Lookup** - deterministic factual retrieval.
2. **Comparison** - compare periods/entities.
3. **Anomaly** - determine what changed abnormally.
4. **Diagnostic** - investigate why.
5. **Predictive** - estimate future outcome.
6. **Prescriptive** - recommend intervention.
7. **Compound** - requires multiple stages.

## Minimum-agent principle

Do not invoke the whole swarm for simple retrieval.

Example:

```text
"Sales yesterday?"
Coordinator -> Commerce -> Observer -> Claim Gate -> Response
```

Example:

```text
"Why did CAC increase and what should we do?"
Coordinator -> Performance lead -> Observer -> Anomaly -> Diagnostic
            -> possible handoff -> Prediction -> Strategy -> Skeptic
```

## Termination conditions

Mission can terminate when:

- requested questions are answered,
- required evidence is present,
- skeptic requirements are satisfied,
- unresolved uncertainty is explicitly represented,
- no blocking task remains,
- budget/latency thresholds have not forced a partial answer.
