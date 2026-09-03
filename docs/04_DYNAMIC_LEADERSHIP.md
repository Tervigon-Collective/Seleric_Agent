# 04 - Dynamic Leadership and Handoffs

## Why leadership moves

The domain closest to the current unresolved causal frontier should lead.

## Required mission state

```yaml
mission_lead: performance_agent
active_specialist: anomaly_agent
leadership_epoch: 3
handoff_history: []
```

## Leadership transfer triggers

A transfer may be proposed when:

- the current lead lacks the required capability,
- evidence moves the investigation into another domain,
- a causal path crosses a domain boundary,
- the current lead reaches a blocker owned by another domain,
- the Skeptic asks for an independent domain investigation.

## Handoff request requirements

Every transfer request must include:

- source agent,
- target agent or requested capability,
- reason,
- evidence references,
- unresolved question,
- requested output contract,
- current mission context subset.

## Coordinator arbitration required when

- A -> B -> A -> B ping-pong occurs,
- multiple agents claim leadership,
- transfer has no evidence,
- permissions/security boundary changes,
- execution budget is exceeded,
- a human approval step would be needed.

## Example

```text
CAC problem
  -> Performance lead
  -> media stable, ATC collapses
  -> Commerce lead
  -> mobile-only funnel degradation
  -> Funnel lead
  -> latency + JS errors
  -> Technical lead
```

## Important

Leadership transfer is a domain ownership change, not a conversational novelty. Persist it as state and audit it.
