.PHONY: install dev test lint validate

install:
	uv sync --extra dev

dev:
	uv run uvicorn seleric_swarm.main:app --reload

test:
	uv run pytest -q

lint:
	uv run ruff check .

validate:
	uv run python scripts/validate_repo.py
