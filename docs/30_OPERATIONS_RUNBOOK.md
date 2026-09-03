# 30 - Operations Runbook

## If MCP source fails

1. Mark source unavailable.
2. Do not invent substitute values.
3. Determine whether another governed source is equivalent.
4. Recalculate claim eligibility.
5. Return partial/insufficient result if necessary.

## If an agent loops

1. Detect repeated task/handoff signature.
2. Freeze automatic handoffs.
3. Coordinator reviews unresolved question and evidence.
4. Re-route once or terminate with explicit limitation.

## If model drift fails threshold

1. Mark model inapplicable.
2. Route to approved fallback baseline if configured.
3. Otherwise return insufficient predictive evidence.
4. Create model-review operational event.

## If causal refutation fails

Downgrade causal claim to association/plausible hypothesis and re-open diagnosis if the mission requires a causal answer.

## If Skeptic rejects

Create explicit remediation tasks. Never allow synthesis to hide a rejection.

## Emergency switches

Maintain runtime switches for:

- disable an agent,
- disable an MCP capability,
- disable a model,
- disable causal service,
- disable cross-domain handoffs,
- disable all write actions.
