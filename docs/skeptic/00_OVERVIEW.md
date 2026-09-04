# 00 - Skeptic Agent Overview

The Skeptic Agent is the **independent verification, falsification,
contradiction-detection, claim-validation and remediation subsystem** of the
Seleric Intelligence Swarm. It is not a contrarian chatbot and it is not a
single LLM prompt.

## The question it answers

> "What evidence, control, contradiction, falsification test, statistical check,
> causal test, model check or business constraint would prove this claim
> unreliable - and can the swarm obtain that evidence?"

## What it produces

A `SkepticVerdict` with a top-level verdict of **PASS / REVISE / REJECT**, a
derived `trust_score` + `trust_label`, a list of typed `Challenge`s, generated
`AlternativeHypothesis` objects, explicit `EvidenceGap`s, machine-actionable
`FollowUpTask`s, and a full `audit` block explaining every decision.

## Where the LLM fits

The reasoning model is **one optional component**. It is used only for semantic
work - phrasing alternative hypotheses, writing the final human-readable
explanation. Every deterministic validator, the risk scorer, the trust scorer
and the verdict engine run with **no model at all**. With no reasoning model
injected the agent is fully deterministic (see `tests/skeptic/`).

## Boundaries

| Concern | Owner |
| --- | --- |
| Orchestration, state, conditional edges | LangGraph (`graph.py`) |
| Agent-to-agent messaging / handoffs | A2A (`a2a.py`, thin adapter) |
| Data / tool access | MCP - the Skeptic has **no** broad MCP access; it emits `FollowUpTask`s |
| Anomaly / forecast computation | ML services behind Protocols |
| Causal estimation / refutation | DoWhy behind `CausalValidationService` |
| Planning, challenge generation, interpretation, explanation | LLM (`ReasoningModel`) |

## Read next

- `01_ARCHITECTURE.md` - modules + LangGraph flow + Mermaid
- `02_CLAIM_MODEL.md` - claim types and contracts
- `03_VALIDATION_PIPELINES.md` - what each validator checks
- `07_COORDINATOR_INTEGRATION.md` - how the Coordinator calls it
- `11_FUTURE_INTEGRATIONS.md` - Diagnostic / Prediction / Strategy plug-in points
- `12_SERVICE_WIRING.md` - production adapters (DoWhy, model registry + drift,
  constraint-store business rules, swarm bridge)
