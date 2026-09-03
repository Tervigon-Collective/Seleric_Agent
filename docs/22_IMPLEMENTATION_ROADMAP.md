# 22 - Phased Implementation Roadmap

## Phase 0 - Contracts and foundations

Deliver:

- mission schema,
- agent registry,
- metric registry,
- evidence schema,
- A2A message schema,
- MCP capability catalog,
- observability IDs.

Exit: schemas validate and three canonical metrics can be retrieved reproducibly.

## Phase 1 - Grounded read-only intelligence

Build:

- Coordinator,
- Observer,
- Evidence Ledger,
- Performance/Commerce/Funnel domain agents,
- MCP gateway,
- response Claim Gate.

Exit: lookup/comparison questions answer correctly with provenance.

## Phase 2 - Anomaly intelligence

Build:

- anomaly model router,
- statistical baseline library,
- Anomaly Agent,
- anomaly artifact registry.

Exit: historical known anomalies are detected at acceptable precision/recall.

## Phase 3 - Diagnostic intelligence

Build:

- Diagnostic Agent,
- hypothesis registry,
- causal graph registry,
- DoWhy causal service,
- refutation workflow.

Exit: diagnostic reports distinguish association from causal support.

## Phase 4 - Prediction

Build:

- feature store/registry,
- model registry/router,
- Prediction Agent,
- drift/backtest metadata.

Exit: forecast outputs always include uncertainty and model lineage.

## Phase 5 - Strategy + Skeptic

Build:

- Strategy Agent,
- intervention schema,
- constraint/rules service,
- Skeptic Agent,
- claim re-open workflow.

Exit: skeptic can force re-investigation and strategy is tied to validated causes.

## Phase 6 - Dynamic leadership and A2A

Build:

- A2A endpoints,
- Agent Cards,
- leadership transfer state machine,
- handoff arbitration,
- context-minimized artifact exchange.

Exit: replayed cross-domain incidents transfer leadership correctly without loops.

## Phase 7 - Production hardening

Build:

- authorization,
- secret management,
- tracing,
- rate/cost budgets,
- resilience/retry/idempotency,
- red-team suite,
- SLOs,
- incident replay dashboard.

## Phase 8 - Controlled actions (future)

Separate project milestone. Add human approval, policy checks, dry-run, rollback and action audit before any autonomous writes.
