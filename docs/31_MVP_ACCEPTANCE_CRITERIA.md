# 31 - MVP Acceptance Criteria

The first usable MVP should pass all of the following.

## Grounding

- 100% of displayed numeric facts have evidence references.
- Metric versions are visible in trace/audit records.
- Missing data never becomes fabricated zero unless the metric contract explicitly defines it.

## Routing

- Lookup questions do not activate the whole swarm.
- Domain lead selection is correct on the agreed benchmark set.
- Handoff requires evidence.
- Ping-pong is detected.

## Anomaly

- Detector outputs are reproducible.
- Expected baseline and detector metadata are stored.

## Diagnostic

- Hypothesis vs association vs causally supported statements are distinguishable.
- Failed refutation prevents a strong causal claim.

## Prediction

- Every numeric prediction references a registered model/baseline.
- Drifted/inapplicable models are blocked.

## Skeptic

- Skeptic can trigger re-investigation.
- Coordinator cannot silently ignore REJECT.

## Operations

- Missions are replayable from audit metadata.
- Timeouts/retries do not duplicate durable artifacts.
- Read-only mode is enforced.
