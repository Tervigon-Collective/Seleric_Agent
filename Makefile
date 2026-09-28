.PHONY: install test lint typecheck eval validate ci dev docker-build docker-up docker-down migrate office-ui-test

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

# Deterministic eval: golden-dataset gate, no network, runs in CI.
# (The old `seleric_swarm.eval` CLI package was deleted in the V3 refactor;
# eval/suites/lookup_v1.yaml is orphaned swarm-era config.)
eval:
	uv run pytest tests/unit/test_v3_golden_dataset.py -q

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
