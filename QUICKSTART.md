# Quickstart

## 1. Requirements

- Python 3.11+
- `uv` recommended
- Docker for local PostgreSQL/Redis services
- Existing MCP servers or MCP endpoints for business data

## 2. Create environment

```bash
cp .env.example .env
uv sync
```

## 3. Start local state services (or full stack)

Postgres + Redis only:

```bash
docker compose up -d postgres redis
```

Postgres is published on host port **5433** by default (`POSTGRES_PUBLISH_PORT`)
so it does not collide with other local Postgres instances on 5432. Inside the
Compose network, services still reach it at `postgres:5432`.

Apply schema migrations from the host (or let the `api` service run
`seleric-migrate` on startup):

```bash
# Host-side migrate against the published port
seleric-migrate --database-url postgresql+psycopg://seleric:seleric@127.0.0.1:5433/seleric_swarm
```

Full deployable stack (API + Postgres + Redis + MinIO + ClamAV + recovery
worker; voice behind the `voice` profile):

```bash
docker compose up -d --build
# API: http://127.0.0.1:8090/health
```

Or build the API image alone:

```bash
docker build -t seleric-swarm-api .
docker run --rm -p 8090:8000 \
  -e API_HOST=0.0.0.0 -e API_PORT=8000 \
  -e LLM_PROVIDER=fake -e PERSISTENCE_BACKEND=memory \
  seleric-swarm-api
```

## 4. Configure MCP servers

Edit `config/mcp_servers.yaml`.

Do not place secrets in YAML. Use environment variables or your secret manager.

## 5. Define metrics

Begin with a small canonical set:

- spend
- impressions
- clicks
- sessions
- add_to_cart
- checkout_started
- purchases
- gross_sales
- returns
- net_sales
- cogs
- gross_profit
- net_profit
- cac
- cvr
- atc_rate
- atc_to_purchase_rate
- gross_roas
- net_roas

Each metric must have a definition, grain, source, timezone and owner.

## 6. Run local API shell

The package lives under `src/`. Bare `python -m uvicorn seleric_swarm.main:app` fails unless that interpreter has an editable install (or `PYTHONPATH=src`).

**Windows (this repo's venv):**

```powershell
.\.venv\Scripts\python.exe scripts\run_dev.py
```

Or activate the venv, then:

```powershell
.\.venv\Scripts\Activate.ps1
python -m uvicorn seleric_swarm.main:app --reload
```

**uv:**

```bash
uv run uvicorn seleric_swarm.main:app --reload
```

## 7. Validate repository contracts

```bash
uv run python scripts/validate_repo.py
```

## 8. Where to go next

The system is a single PydanticAI agent loop (there is no multi-agent
pipeline to assemble). To understand or extend it:

1. Read `docs/CURRENT_ARCHITECTURE.md` — mission lifecycle, API surface,
   agent loop, toolsets, evidence/provenance, storage, observability.
2. The target architecture diagram is `diagrams/new.mmd`.
3. New capabilities belong in a toolset (`src/seleric_swarm/toolsets/`)
   or a deterministic service, not in a new "agent".
