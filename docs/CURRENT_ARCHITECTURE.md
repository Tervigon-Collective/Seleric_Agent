# Seleric Agent — Current Architecture (V3)

**As of 2026-09-21.** This is the as-built reference for the system that's
actually running: a single `PydanticAI Agent[SelericDeps, MissionResult]`
loop over Cube, mediated by `seleric-mcp`. It replaces the old LangGraph
"swarm_v2" multi-agent design (Coordinator, domain agents, five intelligence
specialists, Blackboard, LeadershipManager, A2A protocol), which has been
fully deleted from `src/` — see the migration pointer in §1.

For the migration's own planning history (sprint-by-sprint execution log,
profile briefs, open decisions), see `docs/refactor/` — that folder remains
the source of truth for *how the migration was carried out*; this doc
describes *what exists today*.

## 1. Overview & migration pointer

- **Target design:** `diagrams/new.mmd` — `Agent → Toolset → Seleric MCP
  Gateway → CubeClient → Cube`. The LLM never calls Cube/ClickHouse directly.
- **Migration source of truth:** `docs/refactor/00_OVERVIEW.md`,
  `01_PROFILE_RUNTIME.md`, `02_PROFILE_SEMANTIC_MCP.md`,
  `03_PROFILE_CAPABILITIES.md`, `SPRINT_PLAN.md`, `TASK_SHEET.md`.
- **What changed:** Coordinator classify/decompose/intake pipeline, domain
  agents, intelligence specialists (Observer/Anomaly/Diagnostic/Prediction/
  Strategy/Skeptic), the Blackboard, LeadershipManager, and the A2A protocol
  are all deleted. A single agent now decides every tool call; there are no
  agent-to-agent handoffs, no `mission_lead`/`active_specialist` state.
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
  (the `EvidenceValidator` gate — bounded to one revision,
  `max_validation_revisions = 1`).
- Toolsets (`src/seleric_swarm/toolsets/`): `semantic.py` (metric/dimension
  resolution + `query_metrics`/`drilldown` against the live catalogue —
  Cube is the only authority for business metrics), `analytics.py`
  (statistics/anomaly detection, consumes evidence already fetched — never
  fetches independently), `causal.py` (DoWhy-backed causal estimation +
  refutation), `models.py` (forecasting), `actions.py` (Meta write actions
  only, gated by the propose→confirm→commit flow — Google Ads action
  execution is explicitly out of scope), `knowledge.py`, `experiments.py`.
- Every agent loop is bounded (`max_tool_calls`, `max_llm_calls`, etc. in
  `config/settings.py`) and the bound is enforced, not a no-op.

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
- `src/seleric_swarm/conversations/` (~5,000 lines) — the conversation/
  memory/artifact platform: threads, messages, runs with lease-based durable
  execution, consent-based memory (`MemoryService`), blob storage
  (`conversations/blobs.py` — Postgres in production despite `MinioBlobStore`
  also existing in code; confirm with a deployment owner before treating
  MinIO as load-bearing).
- `src/seleric_swarm/conversations/phase7.py` — `ApprovalRequest`/
  `RollbackRecord`: the propose → validate → preview → confirm → commit →
  audit flow for write actions.

## 6. Observability & tracing

- `src/seleric_swarm/coordinator/observability/events.py` —
  `observe_mission_events`/`canonical_kind`, the structured mission-event
  vocabulary that replaced the old Blackboard. Used live by
  `api/conversations.py` and `conversations/event_mapping_v1.py`.
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
  rate limiting, applied to all non-probe routes.
- `src/seleric_swarm/api/mission_access.py::require_mission_access` —
  workspace/owner scoping on every mission read.
- Write actions always go through `conversations/phase7.py`'s approval flow
  (§5) — never direct autonomous execution.

## 8. Office UI gateway

`src/seleric_swarm/api/office/gateway.py` (+ `normalize.py`, `registry.py`,
`v3_adapter.py`) — a read-only SSE snapshot/event-stream gateway over the
same mission stores. Reusable docs: `docs/office-ui/06_REALTIME_ARCHITECTURE.md`
(snapshot/stream/reconnect/dedupe mechanics), `13_PERFORMANCE.md`,
`14_ACCESSIBILITY.md`, `16_PRODUCTION_READINESS.md`. The event *vocabulary*
those docs assume (leadership transfer, per-specialist office zones) was
swarm_v2-specific and its docs were removed — the gateway mechanics are
unchanged, but the front-end's event mapping will need updating for
whatever vocabulary V3 actually emits (see `docs/office-ui/00_OVERVIEW.md`).

## 9. Deployment & operations

- `docker-compose.yml` / `Makefile` — local Postgres on host port 5433 by
  default; `seleric-migrate` / `make migrate` for schema; `seleric-recover`
  runs the durable-queue recovery worker
  (`seleric_swarm.api.conversations:build_submission_executor`).
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
- Every new agent/tool/model contract needs both a unit test and a contract
  test; replay tests cover cross-capability scenarios (one capability's
  output feeding another); missing/stale/conflicting-data paths and prompt
  injection carried in tool-returned text are explicitly tested, not just
  the happy path. Full bar: `.cursor/rules/04-testing-observability.mdc`.

## 11. Open items carried forward

Nothing substantive survived from the deleted stale-planning trackers
(`docs/33_PROJECT_CHECKLIST.md`, `44_ROBUSTNESS_SCALABILITY_ADAPTABILITY_BACKLOG.md`,
`45_TICKET_TRACKER.md`, `46_ARCHITECTURE_CONSOLIDATION_PLAN.md`) — their open
items were all scoped to code that's now deleted (the five intelligence
specialists' load/cost validation, the three-independent-MCP-path
consolidation that only existed because swarm_v2's `HybridMcpDataProvider`
existed). Two small items worth tracking separately, found during this
cleanup pass (see the audit doc for detail):

- The Office UI's event vocabulary (§8) needs updating for V3's actual
  event kinds — currently undocumented for the new architecture.
- Follow-up query time/metric inheritance (e.g. "gross sales today" → "gross
  sale" inheriting the time range) had an implementation
  (`coordinator/intake/conversation_context.py`) that was never wired into
  the live V3 conversation submission path (`api/conversations.py`) — it
  was deleted as dead code in this pass, not as a live regression, but if
  follow-up context inheritance is a wanted V3 feature it needs a real
  implementation, not a resurrection of the deleted one.
