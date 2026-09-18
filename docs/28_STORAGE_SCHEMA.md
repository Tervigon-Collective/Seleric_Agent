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

Local Compose publishes Postgres on host port **5433** by default
(`POSTGRES_PUBLISH_PORT`) so it does not collide with other databases on 5432.
In-compose services still connect to `postgres:5432` on the Docker network.
Host-side migrate / local API use
`postgresql+psycopg://seleric:seleric@127.0.0.1:5433/seleric_swarm`.

### Object storage

Large raw extracts, model artifacts, plots, serialized datasets and investigation attachments.

### Redis

Locks, ephemeral coordination, rate limits and short-lived cache only.

## Immutability

Evidence and model-execution records should be append-oriented. Corrections create a new version/superseding record rather than mutating history silently.

## SQL starter

See `migrations/001_init.sql`.
