.PHONY: install test lint typecheck eval eval-llm validate ci dev

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

ci: lint test validate eval
