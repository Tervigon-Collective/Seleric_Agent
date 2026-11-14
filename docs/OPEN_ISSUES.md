# Seleric Agent — Open Issues

> Single tracker for known issues. Last reviewed: 2026-11-14.
> Older trackers are history only: `docs/archive/OPEN_ISSUES_2025-10-12.md` (swarm_v2 era),
> `docs/BUG_SHEET.md` (archived), `docs/STRESS_TEST_BUG_BACKLOG.md` (2026-09-25 snapshot).

| ID | Issue | Where | Notes |
|----|-------|-------|-------|
| OI-001 | `test_business_state_live` fails with `CATALOGUE_MISS` when the live MCP is reachable but its catalogue is empty or unavailable | `tests/unit/test_business_state_live.py` | The `runtime` fixture skips only when MCP credentials are absent, not when the catalogue is empty. Needs an `integration` marker or a catalogue-presence skip. |
| OI-002 | The `run_python` sandbox is POSIX-only (`resource` module); its tests are skipped on Windows | `toolsets/sandbox_child.py`, `tests/unit/test_sandbox_toolset.py` | Run those tests on Linux/CI or in Docker. mypy reports `setrlimit` on Windows only. |
| OI-003 | Dev dependencies are declared twice with different pins | `pyproject.toml` (`optional-dependencies.dev` vs `[dependency-groups].dev`) | Consolidate and re-run `uv lock`. |
| OI-004 | Ruff targets `py312` while `requires-python` is `>=3.11` | `pyproject.toml` | Align the two. |
| OI-005 | Several modules are very large (`causal/diagnosis.py` ~3000 lines, `api/conversations.py` ~2200, `conversations/postgres.py` ~1900) | `src/seleric_swarm/` | Split by responsibility; costly to review and to load as context. |
| OI-006 | Port 8090 collides with other local services (seen with another project's container) | `docker-compose.yml`, `Makefile` `eval` | Set `API_PUBLISH_PORT` in `.env`; the `eval` target is hardwired to :8090. |
| OI-007 | Keys in a developer's `.env` that nothing reads: `AZURE_OPENAI_STRONG_REASONING_EFFORT`, `AZURE_EMBEDDING_MODEL`, `JEV_*` | `.env` (local, untracked) | Remove locally if unused. |
