# Seleric Agent

One PydanticAI agent loop answers business questions over the Seleric Core MCP (Cube is the only authority for
metrics). Read `docs/CURRENT_ARCHITECTURE.md` first; `README.md` has the golden rule and non-goals. swarm_v2 /
LangGraph is deleted; anything still describing coordinators, handoffs, understand/plan stages is stale history.

## Commands

```bash
uv sync --extra dev                 # uv is not preinstalled on every machine: `pip install uv` into .venv works
uv run ruff check .                 # lint
uv run mypy src                     # typecheck (one Windows-only error: sandbox_child.py setrlimit)
uv run pytest -q                    # runs tests/ only (testpaths); chronos/ has its own image and needs torch
uv run python scripts/validate_repo.py
uv run python scripts/lint_semantic_literals.py
npm --prefix office-ui test         # frontend (vitest); also `npm run typecheck` / `npm run build`
docker compose up -d --build        # full stack; API on $API_PUBLISH_PORT (default 8090)
make eval                           # live regression against the running API; needs real LLM + MCP, costs money
```

On Windows without `uv` on PATH, call `.venv/Scripts/python.exe -m pytest|ruff|mypy` directly.

## Layout

- `src/seleric_swarm/agent/` loop, grounding, presentation; `toolsets/` local tools; `protocols/mcp/` Core client.
- `api/`, `conversations/`, `recovery.py`: HTTP surface and durable runs. **Missions execute in the `recovery`
  container, not `api`** - after an agent change rebuild and restart `api recovery business-state-cron` together.
- `services/monitoring/`, `services/domain_health/`, `services/business_state/`: scheduled evaluation (cron containers).
- `config/*.yaml` policies and registries; `config/model_registry.yaml` targets must be catalogue ids (`metric.*`).
- `office-ui/` React frontend; `chronos/` separate forecasting service; `eval/regress/` live eval harness.
- `docs/OPEN_ISSUES.md` is the one open-issues tracker; `docs/archive/` and `plans/` are history.

## Rules that bite

- Numbers in answers must trace to evidence ids; never let the LLM write analytical SQL or invent figures.
- Tools never call other tools; analytics/causal/forecast tools consume already-fetched evidence.
- Every loop is bounded and the bound is enforced. Writes need propose -> validate -> preview -> confirm -> commit -> audit.
- `docker-compose.yml` passes env to containers explicitly: a new setting read from `.env` must also be added
  to the relevant service there (a missing `STT_PROVIDER` once crash-looped the API under `APP_ENV=production`).
- `validate_for_startup()` rejects fake providers, empty `API_KEY` etc. outside local/dev/test `APP_ENV`.
- The `run_python` sandbox is POSIX-only; its tests skip on Windows. Run them on Linux/CI or in Docker.
- `tests/unit/test_business_state_live.py` needs a live MCP with a loaded catalogue.
- Never commit `.env`; add new keys to `.env.example` (and delete keys when code stops reading them).
