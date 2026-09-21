# Seleric Intelligence Swarm

Business intelligence agent: a single `PydanticAI Agent[SelericDeps, MissionResult]`
loop that answers a business question by calling capability toolsets (semantic
metrics, analytics, causal, models, actions, knowledge, experiments), all
mediated through `seleric-mcp` — Cube is the only authority for business
metrics, and every numeric claim in a final answer maps to a traceable
evidence id.

See `docs/CURRENT_ARCHITECTURE.md` for the full architecture (mission
lifecycle, API surface, agent loop, evidence/provenance, storage,
observability, security). This repo previously ran an older LangGraph
multi-agent "swarm_v2" design (coordinator + domain agents + intelligence
specialists); that architecture and its docs were retired — see
`docs/CURRENT_ARCHITECTURE.md`'s overview for the pointer to what changed.

## Golden rule

> The LLM decides what to investigate and how to interpret evidence. Cube,
> statistical engines, ML models and causal tools produce or validate the
> evidence — the LLM never generates production analytical SQL.

## Repository map

```text
Seleric_Agent/
├── README.md
├── pyproject.toml
├── .env.example
├── docker-compose.yml
├── Makefile
├── config/
├── diagrams/
├── docs/
├── src/seleric_swarm/
├── tests/
└── .cursor/rules/
```

## Start here

1. Read `docs/00_PROJECT_CHARTER.md`.
2. Read `docs/CURRENT_ARCHITECTURE.md`.
3. Configure your MCP endpoint in `.env`.
4. Define canonical metrics in `config/metric_registry.yaml`.

## Non-goals

- Autonomous write actions without the propose → validate → preview → confirm → commit → audit flow.
- Treating LLM self-reported confidence as calibrated confidence.
- Treating DoWhy as an automatic root-cause oracle without a causal graph and assumptions.

## Current reference baseline

Python 3.11+, PydanticAI, FastAPI, Cube via `seleric-mcp`, DoWhy for causal estimation. Dependency versions should be locked after a compatibility test in your environment.

## Durable conversation recovery

Production conversation submissions are persisted as queued run attempts; API
processes do not execute them with FastAPI background tasks. Run the recovery
worker with the persisted submission executor:

```console
seleric-recover --executor seleric_swarm.api.conversations:build_submission_executor
```

The executor factory receives the worker runtime and rehydrates the query,
scope, request ID, execution mode, and assistant placeholder from `Run.metadata`.
Workers claim attempts using a lease version, heartbeat with that fencing token,
and emit a terminal conversation event only after the terminal compare-and-set
succeeds. `docker compose up` starts this worker automatically. Local in-memory
runtimes use the same worker path through an in-process queue.

## Local Postgres

`docker compose` publishes Postgres on host port **5433** by default
(`POSTGRES_PUBLISH_PORT`) to avoid clashing with other local databases on 5432.
In-compose `api`/`recovery` always use `@postgres:5432` (override with
`COMPOSE_DATABASE_URL`). Host-side tools use `DATABASE_URL` pointing at
`127.0.0.1:5433`:

```console
seleric-migrate --database-url postgresql+psycopg://seleric:seleric@127.0.0.1:5433/seleric_swarm
# or: make migrate
```
