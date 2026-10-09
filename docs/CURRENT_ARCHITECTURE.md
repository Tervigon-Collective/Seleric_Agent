# Seleric Agent — Current Architecture (V3)

**As of 2026-09-28.** This is the as-built reference for the system that's
actually running: a single `PydanticAI Agent[SelericDeps, MissionResult]`
loop over Cube, mediated by `seleric-mcp`. It replaces the old LangGraph
"swarm_v2" multi-agent design (Coordinator, domain agents, five intelligence
specialists, Blackboard, LeadershipManager, A2A protocol), which has been
deleted from `src/` — see the migration pointer in §1.

For the migration's own planning history (sprint-by-sprint execution log,
profile briefs, open decisions), see `docs/refactor/` — that folder remains
the source of truth for *how the migration was carried out*; this doc
describes *what exists today*.

## 1. Overview & migration pointer

- **Target design:** `diagrams/new.mmd` — `Agent → Toolset → Seleric MCP
  Gateway → CubeClient → Cube`. The LLM never calls Cube/ClickHouse directly.
  (The diagram was corrected 2026-09-28: a `Temporal` durable-execution node
  was removed — Temporal was never adopted; durable missions run on the
  lease/heartbeat run queue in `api/async_missions.py` + `recovery.py`.)
- **Migration source of truth:** `docs/refactor/00_OVERVIEW.md`,
  `01_PROFILE_RUNTIME.md`, `02_PROFILE_SEMANTIC_MCP.md`,
  `03_PROFILE_CAPABILITIES.md`, `SPRINT_PLAN.md`, `TASK_SHEET.md`.
- **What changed:** Coordinator classify/decompose/intake pipeline, domain
  agents, intelligence specialists (Observer/Anomaly/Diagnostic/Prediction/
  Strategy/Skeptic), the Blackboard, LeadershipManager, and the A2A protocol
  are all deleted. A single agent now decides every tool call; there are no
  agent-to-agent handoffs, no `mission_lead`/`active_specialist` state.
- **Residue in the tree (cleanup candidates, not live code):** no swarm_v2
  module is *tracked* in git anymore, but the working tree still contains
  dead package husks — `src/seleric_swarm/coordinator/` and
  `src/seleric_swarm/prompts/` hold only stale `__pycache__/` (no `.py`
  files), `src/seleric_swarm/ml/` is an empty package, and `.gitignore`
  still carries a `!src/seleric_swarm/coordinator/artifacts/` negation for a
  directory that no longer exists. One swarm-era module *is* live:
  `swarm/providers/base.py`, imported by
  `services/business_state/detectors.py`.
- **Historical decision records:** `docs/ADR/ADR-001*`/`ADR-002*` document
  the now-reversed specialist/domain-axis and LangGraph/A2A decisions
  (superseded, see their headers). `docs/ADR/ADR-003*` (evidence-first
  claims) and `ADR-004*` (read-only-first) are unchanged.
- **Archived incident history:** `docs/BUG_SHEET.md` (swarm_v2-era, frozen).

## 2. Mission lifecycle & API surface

Entry point: `src/seleric_swarm/main.py` (FastAPI app).

| Route | Purpose |
|---|---|
| `POST /v1/missions` | Submit a query. `wait: true` runs synchronously via `run_v3_mission` and returns the full `MissionResult` (including `trace.events`, the full event timeline, added inline). `wait: false` seeds a running placeholder and completes on the durable run queue (`enqueue_durable_mission`/`publish_durable_mission`). |
| `GET /v1/missions/{mission_id}` | Poll a mission's current state. |
| `POST /v1/missions/{mission_id}/cancel` | Cooperative best-effort cancellation. |
| `GET /v1/missions/{mission_id}/events` | Paginated structured control-plane events (`family`, `after_seq`, `limit`). |
| `GET /v1/missions/{mission_id}/trace` | Trace metadata (`request_id`, `session_id`, `langsmith_run_id`, `elapsed_seconds`) plus the full event timeline in one call. |

The conversational surface (`src/seleric_swarm/api/conversations.py`, mounted
router) is the primary production entry point: threads/messages/runs with
durable queueing, memory, and attachments. `_dispatch_route()` is hardcoded
to `"v3"` — there is no live classification gate routing to anything else.

A secondary surface: the **voice agent** (`src/seleric_swarm/voice/` —
LiveKit-based `token.py`/`worker.py`/`dev_page.py`, router mounted in
`main.py`, compose `voice` service behind the `voice` profile) submits
missions through the same conversation path.

A mission's `route` field is one of `v3` (created today), or a historical
`swarm`/`pending`/`failed`/`lookup` value on an old persisted record — the
old values are read-only compatibility, not a live creation path.

## 3. Agent loop & toolsets

- `agent/runner.py::run_v3_mission` — builds `SelericDeps`, calls
  `build_seleric_agent()` + `run_validated_mission`, converts the result into
  `contracts/lookup.py::MissionResult`, persists it.
- `agent/agent.py::build_seleric_agent` — the `Agent[SelericDeps,
  MissionResult]`, wired with `agent/instructions.py::INSTRUCTIONS` and every
  toolset.
- `agent/dependencies.py` — `SelericDeps`, `ExecutionLimits`, `NullMcpClient`.
- `agent/model.py::resolve_v3_model`, `agent/validation/__init__.py::run_validated_mission`
  (the `EvidenceValidator` gate — bounded by
  `max_validation_revisions = 3`, i.e. up to three revision passes beyond the
  initial run; see `ExecutionLimits` in `agent/dependencies.py`).
- Toolsets (`src/seleric_swarm/toolsets/`): `semantic.py` (metric/dimension
  resolution + `query_metrics`/`drilldown` against the live catalogue —
  Cube is the only authority for business metrics), `analytics.py`
  (statistics/anomaly detection, consumes evidence already fetched — never
  fetches independently), `causal.py` (DoWhy-backed causal estimation +
  refutation), `models.py` (forecasting), `actions.py` (Meta write actions
  only, gated by the propose→confirm→commit flow — Google Ads action
  execution is explicitly out of scope), `knowledge.py`, `experiments.py`,
  `sandbox.py` (registered Python-execution tool, `sandbox.run_python`),
  `exploration.py` (`explore_data`: open-ended exploration — tests window
  changes, trends, step changes, segment shifts, outstanding segments and
  co-movement under false-discovery control; engine in `exploration/`, model
  in `docs/EXPLORATION_MODEL.md`).
  Helper modules that are *not* tool surfaces: `catalogue_index.py`,
  `policy_config.py`, `ads.py` (defined but deliberately unregistered —
  see §11.2).
- Backing service packages (toolsets are thin adapters over these):
  `analytics/`, `knowledge/`, `experiments/`, `models/`, `causal/`,
  `services/` (catalogue bootstrap, evidence, ontology, claim gate, numeric
  audit, business-state, domain-health), `llm/` (provider factory/gateway,
  metering, circuit breaker; one Azure resource — gpt-5-nano since 2026-10-07), `protocols/mcp/gateway.py`
  (`MCPGateway`).
- Every agent loop is bounded (`max_tool_calls`, `max_llm_calls`, etc. in
  `config/settings.py`) and the bound is enforced, not a no-op. Repeat/
  paraphrase loops are additionally walled by `agent/repeat_guard.py` and
  the per-tool call caps noted in §11.1.

## 4. Evidence, provenance & causal validation

Full rules: `docs/08_EVIDENCE_AND_PROVENANCE.md` (unchanged, era-agnostic).
Key points as implemented in V3:

- Every numerical claim maps to an immutable `EvidenceArtifact`
  (`contracts/lookup.py::EvidenceView`) with a traceable evidence id.
- Missing evidence returns `INSUFFICIENT_EVIDENCE`, never a fabricated
  number.
- Causal claims carry an explicit classification (OBSERVATION / ASSOCIATION
  / HYPOTHESIS / CAUSALLY_SUPPORTED / EXPERIMENTALLY_VALIDATED);
  `causal/dowhy_service.py` runs refutation/sensitivity checks before a
  claim can be reported as causally supported.
- `.cursor/rules/01-evidence-policy.mdc` and `03-ml-causal.mdc` carry the
  full non-negotiable list (hallucination guards, temporal-leakage checks
  for predictive features, model id/version stamping on every prediction).

## 5. Storage & persistence

- `src/seleric_swarm/persistence/postgres.py::build_store` — the mission
  store `runtime.store` (Postgres-backed in production, with an in-memory
  fallback via `persistence/memory.py`). This is the one live mission store
  used by both `main.py`'s synchronous path and the durable run queue —
  it is **not** a swarm_v2-only duplicate despite an older module docstring
  implying a since-resolved "two pipelines share nothing" caution.
- `src/seleric_swarm/state/{missions,artifacts}.py` — typed V3 mission/
  artifact scaffolding (`Mission`, `ArtifactStore`), used by the still-
  unmounted `api/missions.py` stub and `api/v3_state.py`'s Office UI
  snapshot fallback.
- `src/seleric_swarm/conversations/` (~7,300 lines) — the conversation/
  memory/artifact platform: threads, messages, runs with lease-based durable
  execution, consent-based memory (`MemoryService`, plus episodic tracking in
  `conversations/memory_manager.py`), blob storage
  (`conversations/blobs.py` — backends are `local` or `minio` only
  (`config/settings.py::blob_backend`); **production requires MinIO**
  (settings validation rejects any other value), local-fs is the dev
  default. There is no Postgres blob backend.)
- `src/seleric_swarm/conversations/phase7.py` — the propose → validate →
  preview → confirm → commit → audit flow for write actions. The
  `ApprovalRequest`/`RollbackRecord` types themselves live in
  `conversations/contracts.py`; `phase7.py` hosts the in-memory/Postgres
  approval repositories and the flow logic.

## 6. Observability & tracing

- The live V3 event stream is `conversations/events.py::ActivityEventSink`
  (thread/run `ActivityEvent`s, persisted via `append_event`). The only
  surviving piece of the old swarm control-plane observability is
  `conversations/event_mapping_v1.py::canonical_kind` — a backward-compat
  alias map (moved out of the deleted `coordinator/observability/events.py`)
  used to normalize kinds on old persisted records. The
  `observe_mission_events`/`notify_mission_event` observer plane and the
  swarm event vocabulary (leadership/skeptic/specialist/remediation
  constants) were deleted as dead no-op plumbing — nothing emitted through
  them in V3. (Note: `src/seleric_swarm/coordinator/` may still exist in a
  working tree as `__pycache__`-only residue; it contains no source.)
- `src/seleric_swarm/observability/traces.py::mission_trace` — wraps each
  agent run; `observability/tracing.py` configures OpenTelemetry/LangSmith
  export (`configure_opentelemetry`, `instrument_fastapi`).
- Every relevant log/trace event carries `mission_id`, `task_id`/`run_id`,
  `agent_id`/`tool_name`, `evidence_id`, `trace_id` where applicable.
- `GET /v1/missions/{id}/events` and `/trace` (§2) are the two read paths
  for after-the-fact investigation.

## 7. Security & governance

Full policy: `docs/18_SECURITY_GOVERNANCE.md` (unchanged, era-agnostic
principles — least privilege, audit, secrets handling). As implemented:

- `src/seleric_swarm/api/security.py::ApiSecurityMiddleware` — API key /
  rate limiting, applied to all non-probe routes (exempts `/health`-style
  probes, the whole `/ui/*` static bundle, and `/v1/voice/dev`).
- `src/seleric_swarm/api/mission_access.py::require_mission_access` —
  workspace/owner scoping on every mission read.
- Write actions always go through `conversations/phase7.py`'s approval flow
  (§5) — never direct autonomous execution.

## 8. Office UI gateway

`src/seleric_swarm/api/office/gateway.py` (+ `normalize.py`, `registry.py`,
`v3_adapter.py`) — a read-only SSE snapshot/event-stream gateway over the
same mission stores. Reusable docs: `docs/office-ui/06_REALTIME_ARCHITECTURE.md`
(snapshot/stream/reconnect/dedupe mechanics), `13_PERFORMANCE.md`,
`14_ACCESSIBILITY.md`, `16_PRODUCTION_READINESS.md`. The front-end renders
the V3 vocabulary natively (one `seleric_agent` over capability stations;
`route=swarm` records keep the retired 14-character view) — the swarm_v2
event docs that assumed per-specialist zones were removed.

## 9. Deployment & operations

- `docker-compose.yml` / `Makefile` — services: `api`, `postgres` (host
  port 5433 by default), `redis`, `minio`, `clamav`, `recovery` (the
  durable-queue worker — its *command* is the `seleric-recover` console
  script), and `voice` (profile-gated). `seleric-migrate` / `make migrate`
  for schema; the recovery worker runs
  `seleric_swarm.api.conversations:build_submission_executor`.
- `config/settings.py` (`Settings`) — all runtime configuration; see the
  file directly for the current field list (several old-pipeline-only
  fields were removed in this cleanup — see the audit doc,
  `docs/audits/2026-09-21_v3_cleanup_and_anomalies.md`).

## 10. Testing approach

- `tests/unit/` — fast, mostly-mocked coverage of individual modules
  (toolsets, contracts, settings, conversations).
- `tests/contract/` — interface-shape tests against the live MCP gateway
  (`tests/contract/test_mcp_gateway.py`).
- `tests/integration/` — real-backend tests (e.g. blob storage).
- `tests/api/`, `tests/fixtures/` — route-level tests and shared fixtures.
- The old `tests/replay/` and `tests/adversarial/` suites were deleted in
  the V3 refactor (only stale `__pycache__/` may remain locally). The
  eval harness lives in `src/seleric_swarm/evals/`: the golden-dataset
  loader is covered by `tests/unit/test_v3_golden_dataset.py`;
  `evals/parity.py` currently has no live test consumer.
- Every new agent/tool/model contract needs both a unit test and a contract
  test; missing/stale/conflicting-data paths and prompt injection carried
  in tool-returned text are explicitly tested, not just the happy path.
  Full bar: `.cursor/rules/04-testing-observability.mdc`.

## 11. Open items carried forward

Nothing substantive survived from the deleted stale-planning trackers
(`docs/33_PROJECT_CHECKLIST.md`, `44_ROBUSTNESS_SCALABILITY_ADAPTABILITY_BACKLOG.md`,
`45_TICKET_TRACKER.md`, `46_ARCHITECTURE_CONSOLIDATION_PLAN.md`) — their open
items were all scoped to code that's now deleted (the five intelligence
specialists' load/cost validation, the three-independent-MCP-path
consolidation that only existed because swarm_v2's `HybridMcpDataProvider`
existed). Two small items worth tracking separately, found during this
cleanup pass (see the audit doc for detail):

- The Office UI's event vocabulary (§8) is V3-native (single agent +
  tool beats; legacy swarm records keep their old view).
- Follow-up query time/metric inheritance (e.g. "gross sales today" → "gross
  sale" inheriting the time range) had an implementation
  (`coordinator/intake/conversation_context.py`) that was never wired into
  the live V3 conversation submission path (`api/conversations.py`) — it
  was deleted as dead code in this pass, not as a live regression, but if
  follow-up context inheritance is a wanted V3 feature it needs a real
  implementation, not a resurrection of the deleted one.

### 11.1 Known constraints at the seleric-mcp seam (intentional)

These are accepted design points, not defects — documented so they aren't
rediscovered as bugs. They live on the gateway (`Base_Agent`) side; the agent
inherits them through the shared service token.

- **Single shared service token — no per-user RBAC.** The gateway authenticates
  as one `service-token` actor with one `caller_scopes` set. A metric's
  `access_policy.roles_allowed` (e.g. `net_profit` → exec/finance) is
  informational only; just the scope check is enforced. There is no per-user
  identity to authorize sensitive metrics against, and brand/tenant isolation is
  by filter argument, not identity.
- **Module scoping is unpinned in V3.** Sprint 5 removed per-agent module
  pinning (`MCPGateway._build_allowlist` returns an empty module map). Calls are
  unscoped unless the model passes `module=` explicitly; the gateway's
  module-scoping machinery still works but nothing pins it.
- **Freshness gate fails open on uncertainty.** The gateway blocks only
  *positively-known-stale* views; a Cube probe error, unparseable cadence, or a
  view with no date dimension is not blocked (so one transient hiccup can't take
  every metric down). Enforcement is also behind a settings flag.
- **Result store is in-process (~1h TTL) → single gateway instance.** `drilldown`
  and `insights_explain` depend on a stored parent query. `toolsets/semantic.py`
  re-runs the parent to sidestep the TTL, but a horizontally-scaled gateway would
  not find a `parent_query_id` created on another instance.
- **Loop-breaker caps.** `search_semantics` is hard-disabled after 3 searches per
  mission and `query_metrics` walls after 2 identical repeat calls — deliberate
  guardrails against small-model paraphrase/duplicate loops.
- **Write path is Pipeboard-only and unverified.** The action broker registers a
  single Pipeboard executor; direct Meta/Google Graph-API writes are not wired.
  The Pipeboard `POST /actions/{type}` endpoint is an inherited convention never
  confirmed against Pipeboard's real API.

### 11.2 Read-only ad surfaces (CONTRACTS.md A2)

`toolsets/ads.py` defines `query_meta_insights` (Cube-backed, certified —
`meta_ad_performance` via the same planner as `metrics_query`, so it writes
`EvidenceArtifact`s), plus `list_meta_accounts` / `list_google_accounts` /
`query_google_ads` (live Graph/GAQL reads, uncertified reference data —
outside the semantic layer, freshness gate, and catalogue). **As of
2026-09-28 these tools are deliberately NOT registered on the agent**
(`agent/agent.py::TOOLS` carries an explicit comment): third-party ad API
surfaces were pulled back from the model, and Meta ad delivery numbers stay
reachable through `query_metrics` (`meta_ad_performance`). No ad write/CRUD
tools are wired either. `semantic.resolve_brand` is registered so
multi-brand questions resolve a `brand_id` instead of the model inventing
one.

---
### Phase 6 — Semantic SQL (Cube Core / 2026-10-06)

**What:** Cube Core's Postgres-protocol SQL API (`CUBEJS_PG_SQL_PORT=15432`) is enabled on `cube-v2`; the agent surface adds `semantic_sql` (new MCP tool `semantic_sql` + agent wrapper `semantic.semantic_sql`).

**Safety rules (hard):** read-only SELECT only; single statement; no DDL/DML (`INSERT/UPDATE/DELETE/DROP/ALTER/CREATE/TRUNCATE/GRANT/REVOKE`); no blocking/dangerous functions (`pg_sleep`, `pg_terminate_backend`, `pg_cancel_backend`, `set_config`, `lo_import`, `copy`); must reference Cube view members or `MEASURE()`; max rows 5000 (hard cap 50,000); statement timeout 30s; rate limit 6 calls/min per caller; provenance carries `query_sha`, `catalogue_version`, `freshness`.

**Access control:** Postgres wire auth uses dev-mode `user:password` (env `CUBE_SQL_DSN`); production DSN must replace it. Cube-side policies: none added this phase (brand scoping stays at ClickHouse `cube_serve` login + agent module filters).

**Caching / pre-aggregations:** Cube Store (`cubestore` service, `ws://cubestore:3030`) is added and running; pre-aggregation YAML defined for 3 hot grains (`pnl_daily` daily rollup by finance_channel, `orders` daily by sales_channel, `ad_delivery` daily by ad_platform) — deferred from active build due to Cube 1.6.48 + ClickHouse index parsing limitation (see `doc/semantic_v2/PHASE6.md`).

**Agent loop impact:** `semantic_sql` is registered in `agent/agent.py` (`TOOLS`), `semantic_layer/semantic_sql.py` validates and runs queries, returns `SemanticSqlResult` with `data`, `columns`, `row_count`, `limited`, `query_sha`, `elapsed_ms`. The agent wrapper builds a `ToolResult` with `EvidenceArtifact` provenance (query text, time range, result set). The `semantic_sql` capability wire uses the same `MCPGateway` and `CubeClient` auth as `metrics_query`.

**Verification status:**
- `psql "postgresql://user:password@127.0.0.1:15432/cube" -c "SELECT 1"` passes (Postgres wire)
- `POST /cubejs-api/v1/cubesql` (REST SQL) passes via `CubeClient`
- `semantic_sql` MCP tool returns governed rows with provenance (verified: `finance_channel` rollup from `serve.pnl_daily`)
- Validation blocks `DROP`, `SELECT pg_sleep(5)`, and multi-statement SQL
- Rate limit enforced (`6/min`)

**Reference docs:**
- Plan / gap: `Seleric_Agent_Core/doc/semantic_v2/PHASE6.md`
- Cube docs (inspiration): Cube Core introduction (`docs.cube.dev/docs/introduction`)
