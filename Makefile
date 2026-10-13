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

# Live eval against the running API (:8090): the 6 test_agent questions, ClickHouse-checked.
eval:
	python3 eval/regress/run_regression.py eval/regress/test_agent6.json eval/regress/results/latest 1

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
