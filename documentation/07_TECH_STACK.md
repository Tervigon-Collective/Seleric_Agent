# 8. Technology stack and tools

Versions were read from the running container `seleric_agent-api-1` and `pyproject.toml` on 2026-11-11.

## 8.1 Runtime

| Layer | Technology | Version | Role |
|-------|-----------|---------|------|
| Language | Python | 3.12.12 (`requires-python >= 3.11`, ruff target 3.12) | Service code |
| Web | FastAPI / Uvicorn | 0.141.1 / 0.52.4 | HTTP API, SSE, static UI |
| Agent framework | `pydantic-ai-slim` | 2.45.0 | Agent, tools, `FallbackModel`, usage limits |
| Validation and settings | Pydantic / pydantic-settings | 2.13.5 / 2.15.0 | Contracts, configuration |
| Database | PostgreSQL 16; `psycopg` 3.3.5, SQLAlchemy 2.0.52 | | Conversations, runs, artifacts, monitoring |
| Cache and events | Redis 7 (`redis` 8.1.0) | | Cancellation flags, event notification |
| Object store | MinIO (`minio` 7.2.20) | server RELEASE.2025-04-22T22-12-26Z | Attachments |
| Malware scan | ClamAV 1.4 | | Upload scanning |
| Model client | `openai` SDK 3.7.0 via an Azure OpenAI-compatible adapter | | Chat completions |
| Causal | `dowhy` 0.14, `statsmodels` 0.15.0 | | Driver estimation, robust fits |
| Numerics | `numpy` 2.4.6, `pandas` 3.0.5 | | Engines and sandbox |
| Forecast service | Chronos-2 on CPU (`chronos/`), own container | | Forecast engine |
| Vector DB (optional) | `qdrant-client` 1.19.1 | | Catalogue embedding search |
| Tracing | `langfuse` 4.16.0, OpenTelemetry SDK 1.45.0, FastAPI instrumentation | | Traces |
| Spreadsheets | `xlsxwriter` 3.3.0 | | Email attachments |
| Retry | `tenacity` 9.1.4 | | Retries |
| Logging | `structlog` 26.1.0 | | Structured logs |
| Voice | LiveKit (`livekit-api` 1.2.1 for tokens; `livekit-agents` installed only in the `voice-runtime` image), Azure speech | | Voice channel |
| UI | React 18, Vite, TypeScript, Tailwind 4, TanStack Table; Node 22 build stage | | Seleric Office |
| Packaging | `uv` and `uv.lock`; multi-stage `Dockerfile` (`ui-builder`, `runtime`, `voice-runtime`) | | Images |

`livekit-agents` is deliberately not declared in `pyproject.toml`: it requires `openai<3`, and the single universal lock would downgrade `openai` for every install; the voice worker installs it out of band.

## 8.2 Models

| Use | Model (deployed) |
|-----|------------------|
| Loop chain (`AZURE_OPENAI_MODELS`) | `gpt-5-mini` (reasoning model; effort low, escalated to medium), then `gpt-4o-code` (fallback) |
| Helper (`AZURE_OPENAI_HELPER_MODEL`) | `Llama-4-Maverick-17B-128E-Instruct-FP8` |
| Voice | Azure speech for speech-to-text and text-to-speech; the voice conversational model is set by the `VOICE_LLM_*` variables |

## 8.3 Tools and scripts

| Tool | Purpose |
|------|---------|
| `Makefile` | `install`, `dev`, `test`, `lint`, `typecheck`, `validate`, `eval`, `office-ui-test`, `docker-build`, `docker-up`, `docker-down`, `migrate`, `ci` |
| `scripts/run_dev.py` | Local development server |
| `scripts/validate_repo.py` | Checks required files and that JSON schemas parse |
| `scripts/lint_semantic_literals.py` | Fails when code contains metric or dimension literals outside `semantic_literal_allowlist.txt` |
| `scripts/prompt_budget.py` | Reports prompt size |
| `scripts/sync_catalogue_to_qdrant.py` | Pushes the catalogue to Qdrant |
| `scripts/backfill_business_state.py` | Backfills business-state snapshots |
| `scripts/record_catalogue_fixture.py` | Refreshes the catalogue test fixture from a live MCP |
| `scripts/check_rate_limit_scope.py` | Probes whether a 429 on one model deployment also hits a sibling deployment on the same endpoint (per-endpoint or per-model throttle) |
| `eval/regress/` | Live regression runner, checks and question sets |
| `ruff`, `mypy`, `pytest`, `pytest-asyncio` (dev extras) | Static analysis and tests |

## 8.4 Test stack

`tests/` contains `unit/` (agent loop, causal, contracts, forecasting, semantic), `contract/` (conversation contracts, MCP gateway, remote server, semantic port, feedback), `api/` (metric definitions, Office gateway and normalisation, query enhance, voice token), `integration/` (durable backends, MinIO, Postgres artifacts) and `fixtures/` (business state, catalogue, query specs).
