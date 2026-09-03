# 01 - System Architecture

## Layers

### 1. Interface layer

User, API, dashboard, scheduled mission or alert.

### 2. Swarm control plane

- Intent normalization
- Query decomposition
- Task DAG construction
- Initial lead selection
- Mission state
- Handoff arbitration
- Completion gate
- Response synthesis

### 3. Domain agent mesh

Domain agents understand business semantics and own domain-specific tool permissions.

### 4. Intelligence specialist mesh

Observer, Anomaly, Diagnostic, Prediction, Strategy and Skeptic supply reusable analytical capabilities across domains.

### 5. Deterministic intelligence plane

Metrics, features, statistics, anomaly models, forecast models, causal inference, optimizers and rule engines.

### 6. MCP access plane

MCP provides governed access to data/tools. Agents should not bypass this layer for production business data.

### 7. Shared state and trust plane

Mission blackboard, evidence ledger, registries, audit trail, provenance and claim validation.

## Separation of responsibilities

```text
LangGraph  -> internal workflow + state + checkpointing
A2A        -> agent interoperability + handoffs
MCP        -> tools/data access
ML         -> anomaly/prediction computation
DoWhy      -> causal estimation/refutation
LLM        -> planning, semantic reasoning, hypothesis generation, interpretation
```

## Final architecture diagram

See `../diagrams/final_architecture.mmd`.

## Fundamental rule

A domain agent may become mission lead. An intelligence specialist normally becomes the active analytical specialist. These are two independent pieces of state:

```text
mission_lead = performance_agent
active_specialist = diagnostic_agent
```
