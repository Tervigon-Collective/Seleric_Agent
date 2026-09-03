# 13 - Strategy Agent

## Question

**What interventions are available?**

## Inputs

- validated observations,
- anomaly artifacts,
- retained diagnostic hypotheses,
- causal results,
- forecast artifacts,
- business goals,
- business constraints,
- policy/risk limits.

## Output

A ranked intervention portfolio. Each intervention should include:

- action,
- target mechanism,
- expected direction/effect,
- confidence class,
- expected cost,
- risk,
- reversibility,
- owner/domain,
- prerequisites,
- measurement plan.

## Optimization

For constrained allocation problems, invoke an optimizer rather than having the LLM perform pseudo-optimization in prose.
