# 14. Development guide

## 14.1 Setup and checks

```bash
cd /opt/seleric/Seleric_Agent
uv sync --extra dev
cp .env.example .env              # LLM_PROVIDER=fake runs tests and the UI without a model
make lint                         # ruff check .
make typecheck                    # mypy src
make test                         # pytest -q
make validate                     # scripts/validate_repo.py
make office-ui-test               # front-end tests (vitest)
make eval                         # live regression against the deployed API on :8090 (needs the stack)
make ci                           # lint test validate eval office-ui-test
```

`LLM_PROVIDER=fake` makes `resolve_loop_model` return a stub `TestModel`, so unit and API tests need no endpoint. Integration tests (`tests/integration/`) need Postgres, MinIO and Redis.

## 14.2 Where to change what

| To change | Edit |
|-----------|------|
| How the model behaves or writes | `src/seleric_swarm/agent/prompt_library/{behaviour,playbooks,domains}/*.md`. Check size with `scripts/prompt_budget.py` |
| Which figures are accepted in an answer | `agent/grounding.py` and its tests in `tests/unit/agent_loop` |
| Which visual a result gets | `agent/present.py`, `analytics/chart_vocabulary.py` |
| Budgets, models, efforts | Environment variables (chapter 11); `agent/limits.py`, `agent/model.py` |
| Which Core tools the loop may call | `ALLOW` in `agent/mcp_toolset.py` |
| A new local tool | `build_local_tools` in `agent/local_tools.py`; a toolset under `toolsets/` for a composite; document it in `behaviour/helper_tools.md` so the model knows when to use it |
| An analytical method | The pure engine (`causal/`, `exploration/`, `forecasting/`) plus a test; keep fetching in the toolset |
| Monitoring or domain-health content | YAML in `config/` (`monitoring.yaml`, `domain_health_profiles.yaml`); resolvers are generic |
| Forecast eligibility | `config/forecast_policies.yaml`, `forecast_features.yaml`, `calendar_in.yaml`, `data_incidents.yaml` |
| Email recipients | `config/email_contacts.yaml` |
| Database schema | A new numbered file in `migrations/` (never edit an applied one); `seleric-migrate` applies it |
| Conversation contracts | `conversations/contracts.py`, `conversations/schema.py`, repositories in `repositories.py` and `postgres.py` |
| An HTTP route | A router in `api/`, included in `main.py`; use `_principal(request)` and the owner-check helpers |

## 14.3 Rules to keep

- **No metric, dimension, view or brand literals in code.** `scripts/lint_semantic_literals.py` enforces this with `semantic_literal_allowlist.txt`; everything semantic comes from the live catalogue snapshot.
- **Numbers come from tools.** A model-computed figure must go through `run_python` so it is grounded.
- **Tool failures are results, not exceptions**, inside the loop.
- **Best-effort telemetry never fails a mission.**
- **Durable first.** Long-running work should be a run attempt claimed by the worker, with cancellation checked cooperatively.
- **Email stays approval-bound.** Do not add a path that sends without the content-hashed approval.
- No new language-interpreting patterns: the grounding scanner is a character scan on purpose.
- Python 3.11+ (images use 3.12), line length 100.

## 14.4 Evaluation practice

`eval/regress/run_regression.py <questions.json> <out_dir> <repeat>` replays questions against the deployed API and checks the numbers against ClickHouse. When changing one behaviour, run the relevant questions rather than the full set. `eval/regress/ab`, `agent6_checks.py`, `mcp_direct.py` and `ch.sh` support it.

## 14.5 Office UI

```bash
cd office-ui
npm install
npm run dev        # vite
npm run build      # tsc -p tsconfig.build.json && vite build --base=/ui/  -> dist/, mounted by the API at /ui
npm test           # vitest run
npm run typecheck
```
