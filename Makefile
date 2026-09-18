.PHONY: install test lint typecheck eval eval-llm validate ci dev docker-build docker-up docker-down migrate office-ui-test

install:
	uv sync --extra dev

dev:
	python scripts/run_dev.py

test:
	uv run pytest -q

lint:
	uv run ruff check .

typecheck:
	uv run mypy src

validate:
	uv run python scripts/validate_repo.py

# Deterministic eval: FakeLLM, no network, runs in CI.
eval:
	uv run python -m seleric_swarm.eval lookup_v1

# Opt-in live eval against Azure Llama + LLM-as-judge faithfulness. Never in CI.
eval-llm:
	uv run python -m seleric_swarm.eval lookup_v1 --live-llm --judge

office-ui-test:
	npm --prefix office-ui test

docker-build:
	docker compose build api

docker-up:
	docker compose up -d --build

docker-down:
	docker compose down

# Apply pending SQL migrations to the Compose Postgres (host port 5433).
DATABASE_URL ?= postgresql+psycopg://seleric:seleric@127.0.0.1:5433/seleric_swarm
migrate:
	uv run seleric-migrate --database-url $(DATABASE_URL)

ci: lint test validate eval office-ui-test
