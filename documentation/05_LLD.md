# 6. Low-level design

## 6.1 Process structure and start-up

| Entry point (`pyproject.toml`) | Function | Process |
|--------------------------------|----------|---------|
| `seleric-api` | `seleric_swarm.main:serve` | FastAPI app under uvicorn |
| `seleric-migrate` | `persistence.migrate:main` | Applies `migrations/*.sql` in order |
| `seleric-recover` | `recovery:main` | Run worker (`--executor`, `--interval`) |
| `seleric-conversation-schema` | `conversations.schema:main` | Emits the conversation schema |
| `seleric-voice` | `voice.worker:main` | LiveKit worker |

`bootstrap.build_runtime(settings)` creates the `SwarmRuntime` dataclass: `settings`, `llm` (port), `mcp` (`MCPGateway`), `metrics` (`MetricRegistry`), `store` (`MissionStore`), `ontology`, and optional `conversations` (repositories), `activity_events` (event sink), `blobs`, `cancellation`, `run_queue`, `business_state`. `main.py` registers `RequestIdMiddleware`, `ApiSecurityMiddleware`, CORS, OpenTelemetry instrumentation, the routers (`conversations`, `phase7`, `emails`, `business_state`, `monitoring`; the Office router and the voice router conditionally) and mounts `office-ui/dist` at `/ui` when `index.html` exists.

## 6.2 Settings (`config/settings.py`)

`Settings` is a `pydantic-settings` class (environment or `.env`, case-insensitive, unknown keys ignored). Chapter 11 lists every field. Notable derived accessors: `resolved_models()` (ordered model ids from `AZURE_OPENAI_MODELS` as a JSON array or comma list; otherwise the legacy `AZURE_OPENAI_MODEL1/2`, then `AZURE_OPENAI_MODEL`), `configured_chat_model()`, `helper_chat_model()`, `resolved_event_notifier_backend()` (`auto` → Redis when a Redis URL exists), `is_dev_surface()`.

## 6.3 HTTP security middleware (`api/security.py`)

```text
dispatch(request):
  if path is exempt or method is OPTIONS: pass through
  principal candidates: external principal_provider / authenticator (if configured)
  authenticated =
       external principal authenticated
    or (no API_KEY configured and no authenticator configured)          # development behaviour
    or (x-api-key header or Bearer token equals API_KEY, constant-time)
  workspace_id, user_id = defaults ("default", "default")
  if authenticated and TRUST_IDENTITY_HEADERS and trusted proxy:
       read x-workspace-id / x-user-id / x-principal-id / x-roles (and x-seleric-* variants)
  request.state.principal = Principal(workspace, user, authenticated, auth_method SHARED_API_KEY|SERVICE|ANONYMOUS, roles)
  if not authenticated: 401 with WWW-Authenticate: Bearer
  if RATE_LIMIT_ENABLED: sliding window per client key (x-api-key prefix, or forwarded IP when trusted, or client IP); 429 with Retry-After
```

Exempt paths: `/`, `/health`, `/readyz`, `/docs`, `/openapi.json`, `/redoc`, `/v1/voice/dev`, and everything under `/ui`. `/ready` (alias of `/readyz`) is not exempt. `request_principal` (`api/mission_access.py`) and `_principal` / `_owned_thread` (`api/conversations.py`) enforce ownership: a thread, run, attachment or draft is visible only to its owner within the workspace; admin routes require `principal.is_admin` (403 otherwise).

## 6.4 The MCP gateway and tool wrapper

### Gateway (`protocols/mcp/gateway.py`)

`MCPGateway` reads `config/mcp_servers.yaml` (one server, `seleric`, `streamable_http`, URL from `SELERIC_MCP_URL`, bearer from `SELERIC_MCP_TOKEN`, capability prefix `seleric`) and exposes `call(tool, args)` and tool listing (`protocols/mcp/servers/seleric_remote.py`). `/readyz` lists the discovered capabilities.

### Tool wrapper (`agent/mcp_toolset.py`)

The loop is allowed only these 17 Core tools (`ALLOW`): `catalogue_search_metrics`, `catalogue_get_metrics`, `catalogue_get_metric`, `catalogue_list_dimensions`, `catalogue_resolve_dimension`, `catalogue_resolve_term`, `catalogue_resolve_concept`, `catalogue_resolve_values`, `catalogue_resolve_brand`, `catalogue_list_brands`, `catalogue_get_ontology`, `catalogue_related_metrics`, `modules_list`, `metrics_query`, `metrics_drilldown`, `insights_explain`, `build_dataset_query`. It does not get `semantic_sql` or any `actions_*` / ads tool. `tool_defs()` sorts and cleans the schemas so the prompt prefix stays byte-stable.

Per call:

1. `validate()` against the published JSON Schema (Draft 2020-12) and `filter_misuse()` for known misuse (for example `set`/`notSet` filters carry no values: `VALUELESS_OPS`).
2. De-duplication inside the turn: `TurnState.seen` maps a call signature to a future, so an identical call is served from the turn's cache (`duplicates` counter).
3. Execution through the gateway with `TOOL_TIMEOUT_S` (45 s default, `AGENT_TOOL_TIMEOUT_S`); `QUERY_ROW_LIMIT` (100,000) is applied to query tools (`with_full_limit`).
4. Post-processing: `flag_partial` marks windows that include the still-open day; `flag_row_limit`; `flag_composed` for multi-part results; `compact()` drops plumbing keys (`DROP_KEYS`: Cube query, SQL, catalogue sha, serve objects, ...), caps lists at `MAX_ROWS` (60) and the result at `MAX_RESULT_CHARS` (14,000) while keeping `rows` and `compare_rows` even when empty.
5. `write_evidence` stores up to `MAX_EVIDENCE_ROWS` (5,000) evidence points per result; going over is flagged on the payload, never silent. `build_dataset_query` results are paged (`DATASET_PAGE` 5,000) up to `MAX_DATASET_EVIDENCE`.
6. Failures are returned to the model as tool results, never raised.

`TurnState` collects `payloads`, `evidence`, `evidence_ids`, `by_query` (query id → evidence ids), `steps`, `parts` (structured answer parts), `tables` (offered tables), `prior_queries`, `errors`, `seen`, counters `tool_calls`, `duplicates`, `tool_ms`, and `today`.

## 6.5 Local tools (`agent/local_tools.py`, `agent/email_tools.py`)

| Tool | Behaviour |
|------|-----------|
| `diagnose_metric_change` | `toolsets/diagnosis.py` → engine `causal/diagnosis.py` (chapter 7) |
| `break_down_metric` | `toolsets/composition.py` |
| `forecast_metrics` | `toolsets/forecasting.py` → `forecasting/pipeline.py` |
| `explore_data` | `toolsets/exploration.py` → `exploration/engine.py` |
| `run_python(code, query_ids...)` | `toolsets/sandbox.py`: runs the script in a child process (`python -I sandbox_child.py`) with an empty environment, working directory `.data/sandbox/<mission_id>/`, limits of 2 GiB address space, CPU seconds = timeout + 3, 64 MiB file size and open-file limits, a guarded `__import__` allowing only `math, statistics, json, datetime, itertools, functools, collections, decimal, fractions, re, time` and `numpy`/`pandas` if installed, and an audit hook that refuses process creation, network, native libraries and file access outside the working directory; hard kill at `SELERIC_SANDBOX_TIMEOUT_S` |
| `show_chart(query_id, ...)` | `toolsets/analytics.py` chart helpers; plots evidence already stored |
| `show_table(query_id, caption)` | Same table builder as `present.py` |
| `audit_entities(query_ids)` | `toolsets/entity_audit.py`: return on spend, spend with no orders, best/worst with enough orders, in code |
| `business_snapshot()` | Latest domain-health snapshot when fresh |
| `draft_mom_email`, `draft_ad_hoc_email`, `send_email` | `agent/email_tools.py` + `mail/` |
| `escalate(reason)` | Raises the reasoning effort for the rest of the turn (`agent/model.py`: `AGENT_REASONING_EFFORT` low → `AGENT_ESCALATED_EFFORT` medium; steps after a causal helper use `AGENT_WRITEUP_EFFORT`) |

Every local tool is wrapped by `_tracked` for step recording and optional figure registration (`quotable`), and `figures()` supplies the same figure pool that the final answer is checked against, so an email can quote only verified figures.

## 6.6 Prompt assembly (`agent/prompt.py`, `prompt_modules.py`, `prompt_slim.py`, `prompt_library/`)

`system_prompt(catalogue, now, server_guide)` concatenates, in this order:

1. `PREAMBLE`: identity (`behaviour/identity.md`), the generic working rules (`behaviour/*` and numbered `playbooks/*` except identity, answer rules and analysis report), and the helper-tool rules.
2. The Core server's own instructions, trimmed by `prompt_slim.slim_guide` and headed "Server guide (Core MCP instructions: data rules)". Where it differs from the answer rules on answer style or arithmetic, the answer rules win (figures computed with `run_python` are allowed).
3. `ANSWER_RULES` and `ANALYSIS_RULES`.
4. Domain notes (`domains/*.md`) only for the views present in the catalogue snapshot (`pm.domains_for`).
5. A catalogue snapshot: metric ids by view (`metric_index`).
6. The calendar: today and weekday with the time zone; yesterday; week to date; last week; same weekdays last week; last seven complete days and the seven before; month to date; last month.

Playbooks (files under `playbooks/`): today and intraday, comparison, cross-source table, cross-domain, row-level and pairwise, topic analysis, basis and date axis, why-moved, performance analysis, email. Adding a rule means adding or editing a markdown module; no Python change. The instructions given to the PydanticAI agent also carry the thread's memories, prior queries, the thread summary and workspace notes (`_instructions`) and the budget notice when budgets are spent.

## 6.7 Grounding (`agent/grounding.py`)

Input: the answer text and the list of tool payloads of the turn (plus earlier turns' compact payloads and the figures quoted in the conversation).

1. `answer_numbers(text)`: character scan (no patterns) returning each stated number with `value`, `pct`, `tol`. `tol` is one unit of the last digit shown, scaled by suffixes (`k`, `m`, `l`/`lakh`, `cr`/`crore`), and never below 0.01. Dates, times, years and bare integers under 32 are skipped.
2. Pools: all numbers in all payloads (`payload_numbers`), split by sign; per-entity pools from labelled rows (`payload_rows`: row labels such as campaign or day names mapped to the numbers of that row); numbers outside any labelled row ("loose": totals, deltas, percentages); and, for tiny payloads (`SMALL_POOL`), per-payload pools.
3. Entity scoping: for each answer line, entities named on the line (or on a preceding header line with no numbers, until a blank line) bind that line's numbers to those entities' rows.
4. Classification per number: **measured** when found in the pool within `tol × 1.01` (also as a percentage ÷ 100); **derived** when one arithmetic step (sum, difference, ratio, % change, share) of two numbers that can meaningfully combine reproduces it — arithmetic is restricted to a row's own entity plus totals, because across hundreds of numbers almost any figure is a sum or ratio of some pair; **misbound** when the value exists in the data but belongs to another entity than the one its line is about; **sign mismatch** when only the opposite sign exists; **unmatched** otherwise. `placeholders()` finds a `?` standing in for a value.
5. `problems(g)` renders one sentence per finding; `correction_message(issues)` is the text of the corrective run; `unverified_note(issues)` is appended when issues remain; `lost_substance(before, after)` rejects a rewrite that kept under 60 % of the verified figures when the draft had at most a quarter of its figures flagged.

## 6.8 Visuals (`agent/present.py`)

Runs after the answer, no model call. Decision by result shape:

| Shape | Output |
|-------|--------|
| Snapshot: no entity dimension, at most two periods, two or more measures | KPI cards (with change when two periods); a single measure is a sentence |
| Trend: three or more periods | Chart; plus a table when two or more measures |
| Ranking: entity dimension | Chart for one measure; table for several measures or a long list |
| Multi-view result (several parts) | One table, parts joined on shared entity columns; a cell a part lacks stays empty (unknown, never zero); on a shared time axis every day from every view stays; on an entity ranking the leading view defines the rows and the others' extras are noted as omitted |

`analytics/chart_vocabulary.py` defines the chart types served by `GET /v1/office/chart-types`.

## 6.9 Model chain (`agent/model.py`, `agent/model_health.py`, `llm/`)

`resolve_loop_model(settings)` returns `TestModel` when `LLM_PROVIDER=fake` or no model or key is configured. Otherwise it builds, for each id of `resolved_models()`, a `HealthGatedChatModel` on the Azure OpenAI-compatible client: models whose names start with `gpt-5`, `o1`, `o3`, `o4` are reasoning models and get `reasoning_effort` and a completion cap of `AGENT_MAX_TOKENS` (2,000) + `AGENT_REASONING_BUDGET` (6,000); other models get temperature 0 and the plain cap and never `reasoning_effort`. Models are chained with PydanticAI `FallbackModel`; `PatientModel` wraps the chain with the shared health gate. `MODEL_HEALTH` keeps a per-model cooldown after a failure (429 or timeout) so a failing model is skipped instantly; if every model is cooling down none is skipped. `AGENT_LLM_WAIT_BUDGET_S` bounds waiting. The HTTP read timeout is `AGENT_LLM_TIMEOUT_S`.

`llm/` additionally provides a provider-neutral `LLMPort`, adapters, `factory`, a `circuit_breaker`, `structured` output helper, `metering` and `tracing`; the helper model (`AZURE_OPENAI_HELPER_MODEL`) serves `POST /v1/query/enhance` and small tasks.

## 6.10 Persistence layer

- `persistence/migrate.py` applies SQL files in order; `postgres.py`, `file_store.py`, `memory.py` implement the mission store; `v3_artifacts.py` stores typed artifacts.
- `conversations/repositories.py` defines the repository interfaces; `conversations/postgres.py` implements them with SQLAlchemy and raw SQL. Writes that must be atomic use `repositories.transaction()` (used by message submission).
- **Run claiming** (`conversations/postgres.py`): `claim(run_id, worker_id, lease_seconds)` selects a runnable attempt `FOR UPDATE SKIP LOCKED` where the worker is null or the lease expired, sets `worker_id`, `lease_expires_at`, increments `version`; `heartbeat` extends the lease only while `lease_expires_at > now` and the version matches; `transition_failed_attempt(..., retry_delay_seconds)` moves to the next attempt or to failed; `list_recoverable` feeds `recover_expired`.
- **Outbox**: `submission_outbox` rows are written in the submission transaction (`add_outbox`); the queue marks them as delivered.
- **Events**: `conversations/events.py` appends `ActivityEvent` rows and notifies readers; `event_mapping_v1.py` maps mission events to conversation events.
- **Blobs**: `conversations/blobs.py` provides `MinioBlobStore`, local store, and `ClamAVMalwareScanner` / `UnavailableMalwareScanner`.
- **Privacy**: `conversations/privacy.py` holds visibility primitives. Payloads are delivered as stored (no value redaction); access is enforced by principal and workspace checks and a capability-limited public-field allowlist.

## 6.11 Recovery worker (`recovery.py`)

`DurablePollingRunQueue` is the durable queue (the QUEUED run / RETRYABLE attempt rows are the queue); `enqueue` also sends `NOTIFY` on `RUN_CHANNEL`, and `listen_for_runs` listens in a background thread and wakes the poll loop; `InProcessRunQueue` is the development equivalent. `RunRecoveryWorker.run_forever` polls, dispatches into free slots (`_dispatch`), and heartbeats each running attempt (`_heartbeat`, `RUN_HEARTBEAT_S`). `RunRecoveryService.fail_attempt` and `recover_expired` implement retry and lease recovery with delay `RUN_RETRY_DELAY_S × 2^(attempt−1)` plus jitter. The executor given on the command line (`api.conversations:build_submission_executor`) rehydrates query, scope, request id, execution mode and placeholder id from `Run.metadata.submission` and calls `agent.runner.run_v3_mission`.

## 6.12 Mail (`mail/`)

`draft.py` renders the two email formats (HTML, plain text, preview markdown) with `[[table:QUERY_ID]]` and `[[chart:QUERY_ID]]` markers filled from stored results; `excel.py` and `chart.py` produce attachments; `recipients.py` resolves explicit addresses or contact names from `config/email_contacts.yaml`; `session.py` wires the process-wide mail runtime (None until tenant, client, secret and mailbox are all configured); `graph.py` is the Microsoft Graph transport (it sends as the configured mailbox, the account Report Automation sends its reports from), using MIME `sendMail` with a base64 text body; `send.py` implements approve-then-send with the guarantees in 5.7.

## 6.13 Observability (`observability/`)

`tracing.py` configures OpenTelemetry and Langfuse (best-effort; `ruff` per-file ignores document that telemetry failures must never fail a mission); `traces.py` records mission traces served by `GET /v1/missions/{id}/trace` and the admin diagnostics route; `flow.py` shapes flow events; `llm/metering.py` counts tokens.

## 6.14 Module index (generated)

Every module under `src/seleric_swarm`, with the first line of its docstring and its public functions and classes. Generated from the code on 2026-11-11.


### `src/seleric_swarm/`

| Module | Responsibility (first docstring line) | Public functions / classes |
|---|---|---|
| `src/seleric_swarm/__init__.py` | — | — |
| `src/seleric_swarm/agent/__init__.py` | Seleric V3 agent runtime (Profile A). | — |
| `src/seleric_swarm/agent/answer_text.py` | Plain-text clean-ups applied to the final answer (moved from ``agent/validation/answer_audit.py``). | `replace_metric_ids`, `round_long_decimals`, `is_table_delimiter`, `numeric_tokens`, `strip_markdown_tables`, `indian_digit_grouping`, `drop_enumerations`, `with_roundings` |
| `src/seleric_swarm/agent/artifacts.py` | Artifact payload schemas — frozen contract, ``docs/refactor/CONTRACTS.md`` §3. | `EvidenceArtifact`, `Finding`, `CausalArtifact`, `PredictionArtifact`, `ForecastArtifact` |
| `src/seleric_swarm/agent/auto_chart.py` | A chart for the answer's main result, when the model asked for none. | `has_transcript_chart`, `main_result`, `auto_chart` |
| `src/seleric_swarm/agent/dependencies.py` | ``SelericDeps`` — frozen, ``CONTRACTS.md`` §1. | `SelericMcpClient`, `NullMcpClient`, `ExecutionLimits`, `SelericDeps` |
| `src/seleric_swarm/agent/email_tools.py` | Email tools: draft a MoM or an ad hoc email from the session, show it for approval, send only what was approved. | `MomAction`, `clean_subject`, `unfilled`, `build_email_tools` |
| `src/seleric_swarm/agent/forecast_plan.py` | Compile a typed ForecastPlan from the tool call's metric ids and horizon — code only, no LLM. | `compile_forecast_plan`, `render_forecast_plan` |
| `src/seleric_swarm/agent/grounding.py` | Deterministic grounding of an answer's figures against the tool results of the turn (no model call). | `answer_numbers`, `payload_numbers`, `payload_rows`, `placeholders`, `is_query_payload`, `grounding`, `quoted_figures`, `measured_in`, +5 more |
| `src/seleric_swarm/agent/limits.py` | Real execution-limit enforcement (Sprint 2 Profile A). | `BudgetVerdict`, `ExecutionBudgetTracker` |
| `src/seleric_swarm/agent/local_tools.py` | The loop's local tools: analysis helpers the model calls when a question needs them (never a forced stage). | `query_id_of`, `ToolView`, `build_local_tools` |
| `src/seleric_swarm/agent/loop.py` | The single reasoning loop: one PydanticAI agent run over the Core MCP tools and the local helpers. | `LoopResult`, `history_messages`, `degenerate`, `run_turn` |
| `src/seleric_swarm/agent/mcp_toolset.py` | The Core MCP read tools as PydanticAI tools for the single reasoning loop. | `compact_value`, `compact`, `filter_misuse`, `ist_today`, `flag_partial`, `flag_row_limit`, `with_full_limit`, `flag_composed`, +11 more |
| `src/seleric_swarm/agent/model.py` | Resolve the pydantic-ai model the V3 agent should run with. | `begin_loop_effort`, `set_loop_effort`, `loop_effort`, `is_reasoning_model`, `EffortModel`, `resolve_loop_model` |
| `src/seleric_swarm/agent/model_health.py` | Per-model cooldowns for the fallback chain. | `StreamStalled`, `ModelHealth`, `cooldown_for`, `retry_after_hint`, `HealthGatedChatModel`, `is_transient`, `PatientModel`, `ReplayedStreamedResponse` |
| `src/seleric_swarm/agent/output.py` | ``ToolResult`` (frozen, ``CONTRACTS.md`` §2) and a draft V3 ``MissionResult``. | `ToolResult`, `MissionResult` |
| `src/seleric_swarm/agent/present.py` | The visuals that go with an answer: KPI cards, a table or a chart of the main result, chosen from its shape. | `number`, `query_id_of`, `Source`, `source_of`, `sources_of`, `humanize`, `indian_grouping`, `display_value`, +16 more |
| `src/seleric_swarm/agent/progress.py` | Live progress from a running mission, so the UI can show steps as they happen. | `set_progress_sink`, `clear_progress_sink`, `has_progress_sink`, `emit_progress`, `tool_label`, `AnswerStream`, `progress_handler` |
| `src/seleric_swarm/agent/prompt.py` | The single reasoning loop's system prompt. | `metric_index`, `calendar`, `system_prompt` |
| `src/seleric_swarm/agent/prompt_modules.py` | Loads the agent's prompt modules (markdown under ``prompt_library/``) so behaviour, query-type playbooks and per-domain knowledge live apart. | `Module`, `folder`, `one`, `domains_for` |
| `src/seleric_swarm/agent/prompt_slim.py` | What the loop trims from the text it sends on every step (the prefix was ~13.9k tokens; this is ~11k). | `slim_guide`, `slim_defs` |
| `src/seleric_swarm/agent/runner.py` | Run one mission through the single reasoning loop and persist it where the UI already reads. | `run_v3_mission` |
| `src/seleric_swarm/analytics/__init__.py` | Pure analytics functions over already-fetched evidence (Profile C). | — |
| `src/seleric_swarm/analytics/chart_vocabulary.py` | Canonical chart vocabulary — the single source of truth for chart forms. | `normalize_chart_type`, `chart_type_catalog`, `chart_type_summary` |
| `src/seleric_swarm/analytics/comparison.py` | Period-over-period comparison — the arithmetic behind ``compare_periods``. | `MetricPoint`, `PeriodDelta`, `pair_key`, `period_deltas` |
| `src/seleric_swarm/analytics/grain.py` | Grain preconditions for the Analytics toolset — ``CONTRACTS.md`` A1.2 (ACCEPTED). | `period_span_days`, `validate_grain_set` |
| `src/seleric_swarm/analytics/visualization.py` | Chart spec construction. | `infer_unit_group`, `unit_groups_for_evidence`, `stacking_allowed`, `stacking_warning`, `DataShape`, `describe_evidence_shape`, `infer_chart_type_from_shape`, `reconcile_chart_type`, +3 more |
| `src/seleric_swarm/api/__init__.py` | API package — HTTP-facing helpers (async missions, etc.). | — |
| `src/seleric_swarm/api/async_missions.py` | Async mission acceptance — seed running placeholder, finish in background. | `request_cancel`, `is_cancel_requested`, `clear_cancel`, `new_mission_id`, `seed_running_mission`, `enqueue_durable_mission`, `publish_durable_mission`, `run_mission_job`, +1 more |
| `src/seleric_swarm/api/business_state.py` | Business-state endpoint. | `BusinessStateRequest`, `business_state` |
| `src/seleric_swarm/api/conversations.py` | Ownership-aware conversation HTTP API. | `CreateThreadRequest`, `UpdateThreadRequest`, `SubmitMessageRequest`, `InitiateAttachmentRequest`, `CompleteAttachmentRequest`, `CreateMemoryRequest`, `UpdateMemoryRequest`, `MoveMemoryRequest`, +48 more |
| `src/seleric_swarm/api/emails.py` | The email preview card's endpoints: read a draft's status, approve the version shown and send it. | `ApproveRequest`, `email_status`, `approve_and_send` |
| `src/seleric_swarm/api/mission_access.py` | Ownership policy for legacy mission-backed HTTP surfaces. | `request_principal`, `can_access_mission`, `require_mission_access` |
| `src/seleric_swarm/api/monitoring.py` | Monitoring panel API: what is watched per domain, how, and what the latest snapshot says. | `state`, `targets`, `config`, `catalogue`, `save_config`, `reset_config`, `refresh` |
| `src/seleric_swarm/api/office/__init__.py` | Seleric AI Office — read-only spatial UI gateway. | — |
| `src/seleric_swarm/api/office/gateway.py` | Read-only HTTP surface for the Seleric AI Office UI. | `office_chart_types`, `list_office_missions`, `office_snapshot`, `office_stream` |
| `src/seleric_swarm/api/office/normalize.py` | Pure normalization: persisted mission payload -> office snapshot + UI events. | `normalize_events`, `build_office_snapshot` |
| `src/seleric_swarm/api/office/registry.py` | In-process registry of mission ids the office gateway can list. | `register_mission`, `known_mission_ids`, `clear` |
| `src/seleric_swarm/api/office/v3_adapter.py` | V3 mission -> office raw dict, for the Office UI's read path. | `v3_raw_snapshot` |
| `src/seleric_swarm/api/phase7.py` | Scoped search, run diagnostics and replay metadata APIs. | `search`, `run_diagnostics`, `backend_capabilities`, `replay_metadata` |
| `src/seleric_swarm/api/ready.py` | Readiness checks for /readyz — dependency surface without secrets. | `check_readiness`, `effective_capabilities` |
| `src/seleric_swarm/api/request_id.py` | Request ID correlation middleware. | `RequestIdMiddleware` |
| `src/seleric_swarm/api/security.py` | API security — optional API key + per-client rate limiting. | `SlidingWindowRateLimiter`, `ApiSecurityMiddleware` |
| `src/seleric_swarm/api/status.py` | Shared mission status helpers — one terminal set for API / runner / tests. | `is_terminal_status`, `coerce_typed_status` |
| `src/seleric_swarm/api/v3_state.py` | Process-level V3 mission/artifact stores for ``api/missions.py``. | `configure_v3_persistence`, `configure_v3_postgres`, `get_v3_mission_store`, `get_v3_artifact_store`, `reset_v3_stores` |
| `src/seleric_swarm/bootstrap.py` | — | `build_runtime` |
| `src/seleric_swarm/cancellation.py` | Cooperative cancellation backends. | `CancellationBackend`, `InMemoryCancellationBackend`, `RedisCancellationBackend`, `build_cancellation_backend`, `MissionCancelledError` |
| `src/seleric_swarm/causal/__init__.py` | — | — |
| `src/seleric_swarm/causal/diagnosis.py` | Diagnosis engine — why did a metric change, by how much, and how sure are we? | `MetricMeta`, `lineage_from_definitions`, `unit_count_metric`, `descendants`, `lineage_in_degree`, `CalendarFit`, `fit_calendar`, `Identity`, +19 more |
| `src/seleric_swarm/causal/dowhy_service.py` | DoWhy boundary. | `DoWhyUnavailable`, `CausalRequest`, `RefuterOutcome`, `DoWhyEstimate`, `DoWhyService`, `TimeSeriesEffect`, `conservative_interval`, `build_time_series_frame`, +1 more |
| `src/seleric_swarm/causal/estimate_pool.py` | Run the per-driver DoWhy estimates of one diagnosis in parallel worker processes. | `workers`, `warm`, `estimate_many` |
| `src/seleric_swarm/causal/graph_chart.py` | Build a ``causal_graph`` chart_spec from a diagnosis report's DoWhy DAGs. | `build_causal_graph_spec` |
| `src/seleric_swarm/config/__init__.py` | — | — |
| `src/seleric_swarm/config/secrets.py` | Secret resolution interface. | `resolve_secret` |
| `src/seleric_swarm/config/settings.py` | — | `Settings`, `helper_chat_model`, `configured_chat_model`, `get_settings` |
| `src/seleric_swarm/contracts/__init__.py` | — | — |
| `src/seleric_swarm/contracts/lookup.py` | — | `TimeRangeV1`, `CoordinatorClassificationV1`, `MetricMappingV1`, `DimensionMappingV1`, `TraceInfo`, `MissionError`, `ClaimView`, `EvidenceView`, +2 more |
| `src/seleric_swarm/conversations/__init__.py` | Conversation platform contracts and policy primitives. | — |
| `src/seleric_swarm/conversations/blobs.py` | Secure streaming blob storage used by conversation attachments. | `BlobValidationError`, `MalwareScanStatus`, `MalwareScanResult`, `MalwareScanner`, `UnavailableMalwareScanner`, `ClamAVMalwareScanner`, `BlobWriteResult`, `BlobStore`, +7 more |
| `src/seleric_swarm/conversations/context.py` | Deterministic Phase 6 memory, summary, extraction, and context services. | `normalize_memory_content`, `approximate_token_count`, `memory_identity`, `validated_memory`, `ThreadSummaryService`, `MemoryCandidateExtractor`, `memories_contradict`, `MemoryService`, +1 more |
| `src/seleric_swarm/conversations/contracts.py` | Canonical contracts for the Seleric conversation platform. | `ContractModel`, `PrincipalAuthMethod`, `Principal`, `ThreadStatus`, `MessageRole`, `MessagePartType`, `RunStatus`, `RunAttemptStatus`, +31 more |
| `src/seleric_swarm/conversations/event_mapping_v1.py` | Version 1 mapping from legacy mission events to public activity events. | `canonical_kind`, `event_type_for_mission_kind`, `map_mission_event` |
| `src/seleric_swarm/conversations/events.py` | Append-only activity event sink and optional delivery notification ports. | `EventNotifier`, `PollingEventNotifier`, `InMemoryEventNotifier`, `RedisEventNotifier`, `build_event_notifier`, `ActivityEventFields`, `ActivityEventSink` |
| `src/seleric_swarm/conversations/file_store.py` | Snapshot in-memory conversation repositories onto local disk. | `snapshot_conversations`, `restore_conversations`, `build_file_repositories` |
| `src/seleric_swarm/conversations/memory.py` | Thread-safe in-memory conversation repositories. | `InMemoryThreadRepository`, `InMemoryMessageRepository`, `InMemoryRunRepository`, `InMemoryArtifactRepository`, `InMemoryAttachmentRepository`, `InMemoryMemoryRepository`, `InMemoryFeedbackRepository`, `InMemoryThreadSummaryRepository`, +1 more |
| `src/seleric_swarm/conversations/phase7.py` | Scoped search and reciprocal-rank fusion. | `build_query_embedder`, `reciprocal_rank_fusion`, `InMemorySearchRepository`, `PostgresSearchRepository` |
| `src/seleric_swarm/conversations/postgres.py` | Postgres implementations of the conversation persistence ports. | `PostgresThreadRepository`, `PostgresMessageRepository`, `PostgresRunRepository`, `PostgresArtifactRepository`, `PostgresAttachmentRepository`, `PostgresMemoryRepository`, `PostgresThreadSummaryRepository`, `PostgresFeedbackRepository`, +2 more |
| `src/seleric_swarm/conversations/privacy.py` | Visibility policy primitives for conversation data. | `redact_text`, `redact_data`, `can_view_event`, `event_for_principal` |
| `src/seleric_swarm/conversations/repositories.py` | Persistence ports for durable conversation state. | `ThreadRepository`, `MessageRepository`, `RunRepository`, `ArtifactRepository`, `AttachmentRepository`, `MemoryRepository`, `ThreadSummaryRepository`, `SearchRepository`, +4 more |
| `src/seleric_swarm/conversations/schema.py` | Generate the public JSON Schemas from canonical Pydantic contracts. | `conversation_json_schema`, `main` |
| `src/seleric_swarm/domain/__init__.py` | — | — |
| `src/seleric_swarm/domain/models.py` | — | `SeriesPoint`, `FeatureValue`, `StateRequest`, `MetricState`, `EvidenceArtifact`, `LeadershipTransfer`, `Claim` |
| `src/seleric_swarm/exploration/__init__.py` | Open-ended data exploration: plan a subspace from the catalogue, test it for | — |
| `src/seleric_swarm/exploration/engine.py` | Exploration engine — what is worth knowing in this data right now? | `FollowUp`, `Insight`, `ExplorationInput`, `ExplorationReport`, `windows`, `window_value`, `explore` |
| `src/seleric_swarm/exploration/insights.py` | Statistical primitives for insight extraction — pure functions, no I/O. | `normal_sf`, `two_sided_p`, `prediction_t`, `t_tail`, `Outstanding`, `outstanding_top`, `Trend`, `trend`, +9 more |
| `src/seleric_swarm/exploration/space.py` | The explorable data space, read from the live catalogue. | `configured_scope_ids`, `default_brand_id`, `scope_dimensions`, `scope_keys`, `plan_dimensions`, `headline_metrics` |
| `src/seleric_swarm/forecasting/__init__.py` | Governed multi-target forecasting (Chronos-2 + feature framework). | — |
| `src/seleric_swarm/forecasting/__main__.py` | CLI: ``python -m seleric_swarm.forecasting run/explain ...``. | `main`, `explain_forecast` |
| `src/seleric_swarm/forecasting/assembler.py` | Point-in-time feature frame via ``semantic.raw_query_metric``. | `assemble_feature_frame`, `content_hash`, `compute_cutoff` |
| `src/seleric_swarm/forecasting/backtest.py` | Offline rolling-origin backtest and bundle promotion (P1). | `rolling_cutoffs`, `score_fold`, `propose_bundle_yaml`, `write_report`, `seasonal_naive_bands`, `fetch_daily_series`, `evaluate_target`, `main` |
| `src/seleric_swarm/forecasting/bakeoff.py` | Feature-set bake-off for the multivariate Chronos-2 forecast. | `FeatureChoice`, `BakeoffResult`, `run_bakeoff` |
| `src/seleric_swarm/forecasting/calendar.py` | Deterministic India calendar covariates — the only known-future features. | `FestivalEntry`, `PaydayConfig`, `CalendarConfig`, `load_calendar_config`, `clear_calendar_cache`, `festival_date_set`, `pre_festival_date_set`, `is_payday`, +4 more |
| `src/seleric_swarm/forecasting/calibration.py` | Run-time calibration of horizon-total bands from the series' own rolling-origin errors. | `empirical_total_error` |
| `src/seleric_swarm/forecasting/chart.py` | Forecast fan chart as a standard ``chart_spec`` (same widget as every other chart). | `build_forecast_chart_spec` |
| `src/seleric_swarm/forecasting/dataset.py` | Explicit dataset-preparation and feature-engineering report. | `describe_dataset` |
| `src/seleric_swarm/forecasting/derive.py` | Horizon totals and derived-ratio recomposition (code only, never LLM). | `horizon_totals`, `ratio_from_components`, `aggregate_days` |
| `src/seleric_swarm/forecasting/driver.py` | Driver-conditioned level. | `apply_driver_level`, `fetch_candidates` |
| `src/seleric_swarm/forecasting/engines.py` | Forecast engines and router ladder (Chronos /predict, ETS path, seasonal-naive). | `EngineForecast`, `RouterDecision`, `ChronosEngine`, `EtsEngine`, `SeasonalNaiveEngine`, `route_and_forecast`, `impute_series` |
| `src/seleric_swarm/forecasting/entities.py` | Entity selection for platform/channel/campaign/product/SKU forecasts. | `EntitySelection`, `select_entities`, `entity_filter`, `sum_mismatch_warning` |
| `src/seleric_swarm/forecasting/features.py` | Deterministic candidate feature generation for a forecast target. | `FeaturePriors`, `load_feature_priors`, `clear_feature_priors_cache`, `candidate_features`, `module_of` |
| `src/seleric_swarm/forecasting/gates.py` | Eligibility and temporal gates — every decision carries a reason code. | `cutoff_for`, `apply_temporal_cutoff`, `check_future_role`, `check_identity`, `check_date_basis`, `check_grain`, `check_unit`, `check_certified`, +4 more |
| `src/seleric_swarm/forecasting/horizon.py` | Turn forecast horizon slots into a concrete [start, end] window from as_of. | `resolve_horizon` |
| `src/seleric_swarm/forecasting/insights.py` | Generic, metric-agnostic facts about a forecast, computed in code. | `build_insight_facts`, `render_insight_facts` |
| `src/seleric_swarm/forecasting/pipeline.py` | Deterministic forecast pipeline — policies → frame → engines → artifact. | `ForecastOutcome`, `run_forecast` |
| `src/seleric_swarm/forecasting/policies.py` | Forecast policy registry — load and validate ``config/forecast_policies.yaml``. | `ProvisionalDefaults`, `PolicyDefaults`, `PromotionRules`, `Eligibility`, `EntityPolicy`, `BundleBacktest`, `FeatureBundle`, `TargetPolicy`, +8 more |
| `src/seleric_swarm/forecasting/quality.py` | Data-quality gates per series — verdicts: block, mask, drop, warn. | `Incident`, `IncidentsFile`, `load_incidents`, `clear_incidents_cache`, `assess_series`, `is_blocking`, `dropped_features` |
| `src/seleric_swarm/forecasting/scope.py` | Workspace brand taken from the principal's context, applied to forecast fetches. | `principal_brand_id`, `brand_filters` |
| `src/seleric_swarm/forecasting/score.py` | Score stored forecasts against days that have since matured. | `score_payload`, `is_due`, `mark_ran`, `fetch_actuals`, `load_recent_forecasts`, `write_score`, `main` |
| `src/seleric_swarm/forecasting/selection.py` | Data-driven choice of the driver that conditions a forecast's level. | `CandidateScore`, `Selection`, `select_driver`, `selection_facts` |
| `src/seleric_swarm/forecasting/types.py` | Shared typed contracts for the forecasting package. | `HorizonWindow`, `GateDecision`, `QualityVerdict`, `FeatureRole`, `DailyPoint`, `TargetForecast`, `FeatureFrame`, `ForecastPlanTarget`, +1 more |
| `src/seleric_swarm/llm/__init__.py` | — | — |
| `src/seleric_swarm/llm/adapters/__init__.py` | — | — |
| `src/seleric_swarm/llm/adapters/azure_openai_compatible.py` | — | `normalize_openai_error`, `AzureOpenAICompatibleAdapter` |
| `src/seleric_swarm/llm/adapters/fake.py` | — | `hints_from_registry`, `resolve_as_of`, `classify_lookup_query`, `map_metric`, `map_dimension`, `synthesize_response`, `FakeLLMAdapter` |
| `src/seleric_swarm/llm/circuit_breaker.py` | Per-model health gate for the LLM gateway. | `CircuitBreaker` |
| `src/seleric_swarm/llm/errors.py` | — | `LLMErrorCode`, `LLMError`, `LLMStructuredOutputError` |
| `src/seleric_swarm/llm/factory.py` | — | `build_llm` |
| `src/seleric_swarm/llm/gateway.py` | LLM Gateway: model-agnostic routing/fallback middleware over one provider adapter. | `LLMGateway` |
| `src/seleric_swarm/llm/metering.py` | Per-mission LLM token/latency metering (docs/44 ticket PRD-002). | `MissionUsage`, `MeteredLLMPort` |
| `src/seleric_swarm/llm/model_spec.py` | Routing metadata for one candidate model behind the LLM gateway. | `ModelSpec` |
| `src/seleric_swarm/llm/port.py` | — | `ChatMessage`, `LLMRequestMetadata`, `LLMRequest`, `TokenUsage`, `LLMResponse`, `StructuredLLMResponse`, `LLMPort` |
| `src/seleric_swarm/llm/structured.py` | — | `extract_json_object`, `schema_instruction`, `with_schema_instruction`, `parse_structured` |
| `src/seleric_swarm/llm/tracing.py` | Translate an :class:`LLMRequest` into LangSmith run metadata + a span name. | `llm_run_metadata`, `llm_run_name`, `enforce_llm_run_metadata` |
| `src/seleric_swarm/mail/__init__.py` | Session email: MoM and ad hoc drafts, recipients, Excel attachment, and the approve-then-send flow. | — |
| `src/seleric_swarm/mail/chart.py` | Email charts: a PNG line chart per time-series dataset, drawn from the stored rows (never from numbers the model typed). | `chartable`, `chart_png` |
| `src/seleric_swarm/mail/draft.py` | The two email formats, rendered as styled HTML, a plain-text fallback and the markdown the preview card shows. | `EmailTable`, `EmailChart`, `RenderedEmail`, `ActionItem`, `MoM`, `AdHoc`, `render_mom`, `render_ad_hoc` |
| `src/seleric_swarm/mail/evidence.py` | Excel datasets from the evidence a thread's queries stored: the original values, never display strings or numbers the | `datasets_for`, `tables_for`, `charts_for` |
| `src/seleric_swarm/mail/excel.py` | The email's Excel attachment, built from the datasets the user picked (never every query result). | `Column`, `Dataset`, `build_workbook` |
| `src/seleric_swarm/mail/graph.py` | Send through Microsoft Graph as the configured mailbox (the account ReportAutomation sends its reports from). | `to_mime`, `GraphTransport` |
| `src/seleric_swarm/mail/recipients.py` | Recipient resolution: an explicit address or a trusted contact, otherwise a question. Never a guess. | `Contact`, `Resolved`, `Clarify`, `ContactBook`, `resolve` |
| `src/seleric_swarm/mail/send.py` | Approve-then-send, with the guarantees that make sending safe. | `SendStatus`, `NotApproved`, `StaleApproval`, `Rejected`, `Aborted`, `Attachment`, `Draft`, `SendReceipt`, +6 more |
| `src/seleric_swarm/mail/session.py` | The process's mail wiring: the shared record store, the Graph sender and the trusted contacts. | `MailRuntime`, `set_mail_runtime`, `disable_mail`, `get_mail_runtime` |
| `src/seleric_swarm/main.py` | — | `get_runtime`, `lifespan`, `MissionRequest`, `PingRequest`, `root`, `health`, `readyz`, `llm_ping`, +6 more |
| `src/seleric_swarm/models/evaluation.py` | Prediction → actual feedback loop (spec §20, Profile C Sprint 3). | `PredictionError`, `evaluate_prediction`, `pair_predictions_with_actuals`, `summarize`, `wql`, `mase`, `coverage`, `bias`, +2 more |
| `src/seleric_swarm/models/service.py` | Forecasting over already-fetched evidence (Profile C, Sprint 3). | `ForecastResult`, `ForecastUnavailable`, `ForecastPathResult`, `forecast_series`, `forecast_path`, `ModelRecord`, `InMemoryModelRegistry`, `YamlModelRegistry`, +1 more |
| `src/seleric_swarm/observability/__init__.py` | Tracing and flow helpers (``tracing``, ``traces``, ``flow``). | — |
| `src/seleric_swarm/observability/flow.py` | Terminal-visible mission flow logging. | `log_mission_event`, `log_mission_step` |
| `src/seleric_swarm/observability/traces.py` | One OpenTelemetry span per V3 mission (Sprint 3 Profile A). | `MissionTraceHandle`, `mission_trace` |
| `src/seleric_swarm/observability/tracing.py` | — | `redact_value`, `redact_mapping`, `redact_processor`, `configure_logging`, `configure_langfuse_env`, `ensure_openai_tracing`, `configure_langsmith_env`, `configure_opentelemetry`, +10 more |
| `src/seleric_swarm/paths.py` | — | `repo_root`, `reports_dir` |
| `src/seleric_swarm/persistence/__init__.py` | — | — |
| `src/seleric_swarm/persistence/file_store.py` | Local-disk persistence for chat, missions, and V3 stores. | `dump_v3_mission`, `load_v3_mission`, `FileMissionStore`, `FileV3MissionStore`, `FileV3ArtifactStore`, `FilePaths`, `file_paths` |
| `src/seleric_swarm/persistence/memory.py` | — | `MissionStore`, `extract_events`, `filter_events`, `InMemoryMissionStore` |
| `src/seleric_swarm/persistence/migrate.py` | Small ordered SQL migration runner for Seleric's Postgres schema. | `run_migrations`, `main` |
| `src/seleric_swarm/persistence/postgres.py` | Optional durable Postgres mission store. | `PostgresMissionStore`, `build_store` |
| `src/seleric_swarm/persistence/v3_artifacts.py` | Postgres-backed V3 artifact store: evidence, findings, plans and turn records. | `PostgresV3ArtifactStore` |
| `src/seleric_swarm/protocols/__init__.py` | — | — |
| `src/seleric_swarm/protocols/mcp/__init__.py` | — | — |
| `src/seleric_swarm/protocols/mcp/gateway.py` | — | `MCPGateway` |
| `src/seleric_swarm/protocols/mcp/servers/__init__.py` | — | — |
| `src/seleric_swarm/protocols/mcp/servers/seleric_remote.py` | Streamable-HTTP adapter for the hosted Seleric catalogue/metrics MCP server. | `MCPUnavailableError`, `SelericMCPTransport`, `RemoteToolServer`, `build_seleric_servers` |
| `src/seleric_swarm/recovery.py` | Lease-fenced recovery and periodic execution for durable run attempts. | `RecoveryResult`, `WorkerResult`, `RunExecutionResult`, `ResumableRunExecutor`, `RetryableExecutionError`, `RunWorkQueue`, `RunRecoveryService`, `RunRecoveryWorker`, +5 more |
| `src/seleric_swarm/runtime.py` | — | `SwarmRuntime` |
| `src/seleric_swarm/semantic/__init__.py` | Catalogue knowledge through one port. | — |
| `src/seleric_swarm/semantic/_fake.py` | In-memory SemanticPort for tests. No MCP. | `FakeSemanticPort` |
| `src/seleric_swarm/semantic/_mcp_port.py` | MCP adapter for SemanticPort. Capability names live only in this module. | `CatalogueCaller`, `McpSemanticPort` |
| `src/seleric_swarm/semantic/_port.py` | SemanticPort and the values that cross it. | `CatalogueError`, `ConceptResolution`, `ValueCandidate`, `ValueCandidates`, `MetricDefinition`, `MetricsQueryArgs`, `QueryResponse`, `concept_from_payload`, +4 more |
| `src/seleric_swarm/semantic/_read_model.py` | Immutable catalogue read model. Unknown payload fields land in ``extra``. | `MetricRecord`, `DimensionRecord`, `HierarchyRecord`, `ViewRecord`, `BrandRecord`, `SemanticReadModel` |
| `src/seleric_swarm/services/__init__.py` | — | — |
| `src/seleric_swarm/services/business_state/__init__.py` | — | — |
| `src/seleric_swarm/services/business_state/detectors.py` | — | `RobustZScoreResult`, `robust_zscore`, `RobustZScoreDetector` |
| `src/seleric_swarm/services/business_state/facade.py` | — | `BusinessStateService` |
| `src/seleric_swarm/services/business_state/features.py` | — | `compute_features`, `classify_freshness` |
| `src/seleric_swarm/services/business_state/formatter.py` | The latest business-state snapshot, as the data the agent loop's ``business_snapshot`` tool returns. | `snapshot_age_hours`, `is_stale` |
| `src/seleric_swarm/services/business_state/profiles.py` | — | `ProfileLoader` |
| `src/seleric_swarm/services/business_state/series.py` | — | `fetch_series` |
| `src/seleric_swarm/services/catalogue_bootstrap.py` | Live Cube catalogue cache — warmed once at startup, refreshed on TTL. | `CatalogueMetricMeta`, `CatalogueSnapshot`, `CatalogueBootstrap` |
| `src/seleric_swarm/services/domain_health/__init__.py` | — | — |
| `src/seleric_swarm/services/domain_health/judge.py` | Decide whether a metric's latest daily value needs attention. | `Judgement`, `infer_shape`, `judge` |
| `src/seleric_swarm/services/domain_health/models.py` | — | `ResolvedMetric`, `DomainStateSnapshot` |
| `src/seleric_swarm/services/domain_health/monitor_config.py` | User-editable monitoring configuration: shipped defaults (config/domain_health_profiles.yaml) plus stored overrides. | `ConfigError`, `VersionConflict`, `OverrideStore`, `validate_domain`, `merge_domain`, `candidates` |
| `src/seleric_swarm/services/domain_health/resolver.py` | — | `DomainHealthProfiles`, `display_kind`, `DomainStateResolver` |
| `src/seleric_swarm/services/domain_health/scheduler.py` | — | `build_headline_snapshot`, `domains_due`, `run_once`, `main` |
| `src/seleric_swarm/services/domain_health/snapshot_store.py` | — | `SnapshotStore` |
| `src/seleric_swarm/services/elapsed.py` | Today-in-progress facts shared by ``query_metrics`` and the answer validator. | `now_in`, `in_progress_day`, `state_cutoff_hour`, `completed_hours`, `covers_in_progress_day`, `same_span` |
| `src/seleric_swarm/services/mcp_query.py` | One metrics_query argument builder + call. Used by BSS series, Hybrid fetch, and fetch_series. | `scope_date_axis`, `with_date_axis`, `relabel_undated`, `applied_axes`, `axis_statement`, `row_date`, `dimension_value`, `split_dimension_dict`, +2 more |
| `src/seleric_swarm/services/metrics.py` | — | `cadence_stem`, `is_intraday_id`, `MetricDefinition`, `MetricRegistry`, `lead_agent_for_hints`, `is_percent_share_metric`, `percent_points`, `format_metric_value` |
| `src/seleric_swarm/services/monitoring/__init__.py` | Unified business monitoring: targets (floor / target / ceiling) judged on the semantic layer's own series. | — |
| `src/seleric_swarm/services/monitoring/evaluate.py` | V1 target evaluation: one metric, one day, against a floor / target / ceiling. Pure: no I/O, no metric names. | `MetricDef`, `Band`, `GateConfig`, `DataEvent`, `Evaluation`, `data_events`, `untrusted_days`, `classify`, +4 more |
| `src/seleric_swarm/services/monitoring/read.py` | Read side of the monitor schema: what the app shows. No metric names; labels, bands and states come from Postgres. | `shape_state`, `read_state` |
| `src/seleric_swarm/services/monitoring/runner.py` | Evaluate brand targets against the semantic layer's series and record the result in Postgres (schema ``monitor``). | `Loaded`, `Desired`, `load_metrics`, `load_known_days`, `fetch_series`, `summary_of`, `desired_findings`, `reconcile`, +3 more |
| `src/seleric_swarm/services/ontology.py` | Live OpenMetadata ontology snapshot, fetched from seleric-mcp. | `OntologyPort`, `OntologyService` |
| `src/seleric_swarm/services/query_enhance.py` | Fast query enhancer — rewrite ambiguous drafts into system-ready questions. | `QueryEnhanceError`, `enhance_query` |
| `src/seleric_swarm/services/time_range.py` | — | `as_of_date`, `window_from_query`, `resolve_time_range` |
| `src/seleric_swarm/state/__init__.py` | V3 runtime state stores (Profile A — Sprint 1 scaffolding). | — |
| `src/seleric_swarm/state/artifacts.py` | ``ArtifactStore`` — Profile A owns this (``CONTRACTS.md`` §3). | `ArtifactStore`, `InMemoryArtifactStore` |
| `src/seleric_swarm/state/cache.py` | ``MissionQueryCache`` (Sprint 3 Profile A). | `MissionQueryCache` |
| `src/seleric_swarm/state/missions.py` | V3 Mission model + store (Profile A — Sprint 1 scaffolding). | `Mission`, `MissionStore`, `InMemoryMissionStore` |
| `src/seleric_swarm/state/scratchpad.py` | ``Scratchpad`` — per-run working memory (agent tracking). | `Scratchpad` |
| `src/seleric_swarm/swarm/__init__.py` | Seleric two-axis dynamic swarm (domain x intelligence). | — |
| `src/seleric_swarm/swarm/providers/__init__.py` | Swarm provider ports (anomaly detectors / metric reading types). | — |
| `src/seleric_swarm/swarm/providers/base.py` | Provider ports - the seams you plug real data / models into (architecture sec. 3, 5, 10-13). | `MetricReading`, `DomainEvent`, `DataResult`, `AnomalyFinding`, `CausalQuery`, `CausalResult`, `ForecastResult`, `RankedInterventions`, +8 more |
| `src/seleric_swarm/toolsets/__init__.py` | — | — |
| `src/seleric_swarm/toolsets/_data.py` | Shared data-access helpers for the diagnosis, composition and exploration tools. | `cache_key`, `cached_metrics_query`, `fetch_failure`, `raw_query_metric`, `evidence_index`, `put_evidence`, `resolve_concept`, `reject_unknown_metric` |
| `src/seleric_swarm/toolsets/analytics.py` | Evidence plumbing for the chart tool. | `generate_visualization` |
| `src/seleric_swarm/toolsets/catalogue_index.py` | Catalogue LTM — local Qdrant-backed semantic search over the live catalogue. | `point_id`, `compose_embedding_text`, `build_embedder`, `build_client`, `ensure_collection`, `upsert_entry`, `search_catalogue`, `list_catalogue`, +2 more |
| `src/seleric_swarm/toolsets/composition.py` | Break an additive metric into the lines it is built from (catalogue ``formula.composition``). | `break_down_metric` |
| `src/seleric_swarm/toolsets/diagnosis.py` | DiagnosisToolset — "why did <metric> change?" in one call. | `diagnose_metric_change` |
| `src/seleric_swarm/toolsets/entity_audit.py` | Entity audit — the figures behind "which campaigns / products / channels work and which leak money", computed in code. | `Entity`, `Roles`, `pick_roles`, `entities_of`, `Audit`, `audit` |
| `src/seleric_swarm/toolsets/exploration.py` | ExplorationToolset — "what is worth knowing in this data?" in one call. | `explore_data` |
| `src/seleric_swarm/toolsets/forecasting.py` | ForecastToolset — governed multi-target forecasts for follow-ups. | `forecast_metrics` |
| `src/seleric_swarm/toolsets/policy_config.py` | Tool precondition thresholds ported from ``config/*_policies.yaml`` (A1.3). | — |
| `src/seleric_swarm/toolsets/sandbox.py` | SandboxToolset — run agent-authored python over already-fetched evidence. | `run_python` |
| `src/seleric_swarm/toolsets/sandbox_child.py` | The sandbox's child process: runs one agent-authored script and prints its outcome as JSON. | `main` |
| `src/seleric_swarm/voice/__init__.py` | Voice agent (LiveKit) — Phase 0. | `room_name_for_thread` |
| `src/seleric_swarm/voice/dev_page.py` | A minimal same-origin join page for the Phase 0 transport proof. | `voice_dev_page` |
| `src/seleric_swarm/voice/projection.py` | LiveKit-facing context projection for a voice session. | `VoiceProjection`, `project_voice_context`, `fetch_voice_projection` |
| `src/seleric_swarm/voice/speech.py` | One voice, one utterance at a time. | `SpeechQueue` |
| `src/seleric_swarm/voice/token.py` | ``POST /v1/voice/token`` — mint a LiveKit room JWT for the calling principal. | `VoiceTokenRequest`, `VoiceTokenResponse`, `mint_voice_token`, `mint_room_jwt` |
| `src/seleric_swarm/voice/worker.py` | ``seleric-voice`` — the LiveKit agent worker process. | `VoicePrincipal`, `principal_from_metadata`, `LatencyLog`, `build_voice_agent`, `format_spoken_summary`, `split_spoken_chunks`, `ask_reply`, `narration_line`, +4 more |