# 19 - Observability

## Every mission should trace

- mission creation,
- query normalization,
- task DAG,
- agent activation,
- tool/MCP calls,
- model calls,
- evidence creation,
- handoffs,
- causal runs,
- skeptic verdicts,
- completion decision,
- final claim references.

## Metrics

- mission latency,
- time to first evidence,
- agent turns per mission,
- handoffs per mission,
- loop rate,
- MCP failure rate,
- model failure/fallback rate,
- unsupported-claim rejection rate,
- skeptic overturn rate,
- cost per mission,
- cache hit rate,
- evidence freshness,
- user correction rate.

## Trace correlation

Use `mission_id`, `task_id`, `agent_id`, `evidence_id` and `trace_id` consistently across services.
