# 11 - Diagnostic Agent and DoWhy

## Question

**Why might it have changed?**

## Diagnostic loop

```text
Generate candidate hypotheses
  -> rank by plausibility and testability
  -> request missing evidence
  -> statistical association tests
  -> construct causal question
  -> select causal graph
  -> identify estimand
  -> estimate effect
  -> run refutation/sensitivity tests
  -> retain/reject/revise hypothesis
```

## DoWhy is not a root-cause oracle

DoWhy needs an explicit causal question and assumptions. A useful request identifies:

- treatment,
- outcome,
- confounders,
- effect modifiers if relevant,
- causal graph,
- estimator,
- refutation plan.

## Causal knowledge registry

Maintain domain causal graphs separately from prompts. Version them.

Example funnel graph:

```text
traffic_mix -> sessions -> PDP -> ATC -> checkout -> purchase
price -------> ATC/purchase
stock -------> purchase
latency -----> ATC/purchase
payment_fail -> purchase
```

## Required output categories

- association only,
- plausible causal hypothesis,
- causally supported under assumptions,
- rejected,
- insufficient evidence.

Never collapse these categories into one narrative.
