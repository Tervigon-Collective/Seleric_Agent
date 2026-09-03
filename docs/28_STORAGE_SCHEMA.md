# 28 - Persistence and Storage Schema

## Durable records

Persist the following independently from transient LangGraph state:

- missions,
- tasks,
- mission events,
- leadership transfers,
- evidence artifacts,
- anomalies,
- hypotheses,
- causal analyses,
- forecasts,
- strategies,
- skeptic findings,
- claims,
- model executions.

## Recommended split

### PostgreSQL

Durable mission/control/audit metadata and structured artifact indexes.

### Object storage

Large raw extracts, model artifacts, plots, serialized datasets and investigation attachments.

### Redis

Locks, ephemeral coordination, rate limits and short-lived cache only.

## Immutability

Evidence and model-execution records should be append-oriented. Corrections create a new version/superseding record rather than mutating history silently.

## SQL starter

See `migrations/001_init.sql`.
