# 29 - Prompting and Reasoning Policy

## System prompts define role boundaries

Prompts should specify:

- what the agent owns,
- what it must not decide,
- which artifacts it accepts,
- which tools/services it may invoke,
- what evidence class its outputs require,
- when to hand off,
- failure behavior.

## Do not put business truth in prompts

Metric definitions, model metadata, causal graphs and permissions live in registries/services, not copied into large prompts as the source of truth.

## Hypothesis generation

Diagnostic prompts may generate multiple candidate explanations. They must explicitly mark them as hypotheses until evidence/tests promote or reject them.

## Context policy

Pass:

- mission question,
- current domain scope,
- relevant artifact references,
- unresolved question,
- constraints.

Avoid passing unrelated historical messages.

## Response policy

Agents produce structured intermediate artifacts. Natural-language synthesis happens at the final boundary or for concise human-readable explanations attached to artifacts.
