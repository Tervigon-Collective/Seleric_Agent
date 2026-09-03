# 08 - Evidence, Provenance and Claim Policy

## No Naked Claims

No material user-facing claim should exist without provenance.

## Numeric claim

```text
claim -> evidence_id -> source -> query/calculation -> timestamp
```

## Causal claim

```text
claim
  -> hypothesis
  -> supporting evidence
  -> contradictory evidence
  -> causal assumptions
  -> estimator
  -> refutation tests
```

## Forecast claim

```text
claim
  -> model_id/version
  -> feature snapshot
  -> training/backtest metadata
  -> interval/uncertainty
  -> drift status
```

## Recommendation claim

```text
action
  -> validated cause
  -> expected mechanism
  -> predicted effect or rule basis
  -> business constraints
  -> risks
```

## Confidence

Do not display arbitrary LLM percentages. Derive trust from evidence quality, statistical strength, cross-source agreement, causal validation, model reliability and freshness.

Suggested labels:

- VERIFIED
- STRONG
- PROBABLE
- WEAK
- INSUFFICIENT

## Claim gate

Before synthesis, reject any claim that violates its required provenance class.
