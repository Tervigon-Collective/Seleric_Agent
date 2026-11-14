# 4. Data flow

## 4.1 End-to-end flow of one question

```mermaid
sequenceDiagram
  participant U as User (Office UI, API client)
  participant A as API (FastAPI)
  participant DB as Postgres
  participant R as Recovery worker
  participant L as Loop (PydanticAI)
  participant M as Core MCP
  participant LLM as Model endpoint
  U->>A: POST /v1/threads/{id}/messages {parts, attachment_ids, scope}
  A->>A: validate thread, parts, attachments READY and CLEAN, date axis
  A->>DB: one transaction: run, attempt RETRYABLE, user message, assistant placeholder, attachment links, context bundle, memory usage, thread title, outbox row
  A->>DB: NOTIFY run channel (best effort)
  A-->>U: 202 {run_id, message ids}
  R->>DB: claim attempt (lease 60 s), heartbeat every 15 s
  R->>L: runner.run_v3_mission(run)
  L->>M: tools/list (cached 1 h), catalogue snapshot (cached)
  loop up to AGENT_MAX_STEPS model steps
    L->>LLM: messages + tools
    LLM-->>L: text or tool calls
    L->>M: MCP tool calls (validated, de-duplicated, timed)
    M-->>L: results with provenance
    L->>L: store EvidenceArtifact rows, run local tools
  end
  L->>L: grounding check, optional correction pass, present visuals
  R->>DB: assistant message parts, mission result, v3 artifacts, events, mark run COMPLETED
  U->>A: GET /v1/runs/{id}/events/stream (SSE)
  A-->>U: run.queued, agent.stage, tool progress, answer tokens, run.completed
```

## 4.2 Inputs assembled for the loop (`agent/runner.py`)

| Input | Source | Notes |
|-------|--------|-------|
| `question` | Concatenated non-empty `TEXT` parts of the user message | |
| `history` | The thread's last turns as (role, text), excluding the question being asked | `_history` |
| `memories` | Active memories selected for the run by `ContextBuilder` (`phase6-v1`) | Recorded in `run_memory_usage` |
| `thread_summary` | Extractive summary of earlier turns | `thread_summaries` table |
| `workspace_notes` | `scope.workspace_config` from the request | |
| `prior_queries`, `prior_payloads` | Queries and compact payloads of earlier turns of the thread | `_thread_memory`; lets figures quoted earlier be grounded now |
| `system_prompt` | `prompt.system_prompt(catalogue_snapshot, now, server_guide)` | Section 6.6 |
| `tool_defs` | `tools/list` of the MCP server, cached for an hour per gateway | `_tool_definitions` |
| `catalogue_snapshot` | Live catalogue cache warmed and snapshotted by `CatalogueBootstrap`; fail-open (an empty snapshot leaves the model to the catalogue tools) | `_catalogue_snapshot` |
| Time zone and `as_of` | `scope.timezone` (default `Asia/Kolkata`), `scope.as_of`; the anchor is the mission's calendar day, not UTC now | `_as_of_datetime` |
| Date axis | `scope_date_axis(scope)` | passed to tools as the requested axis |

## 4.3 Evidence flow

```mermaid
flowchart LR
  T[MCP tool result] --> V[validate, compact, flag partial window / row limit / composed]
  V --> E[EvidenceArtifact rows: kind, values, grain, window, provenance]
  E --> S[TurnState.evidence and payloads]
  S --> G[grounding: pool of figures for the answer check]
  S --> P[present: visual from result shape]
  S --> LT[local tools by query_id: run_python, show_chart, show_table, audit_entities, email tables]
  S --> PM[persisted: v3_artifacts, thread memory for later turns]
```

- `write_evidence` limits stored points (`max_points`) and flags when evidence was cut (`_flag_evidence_cut`); the model sees a `query_id` in every MCP result, while the rows stay server-side.
- Datasets from `build_dataset_query` are paged into evidence with `load_dataset` / `load_dataset_rows` and re-shaped as a query payload (`dataset_as_query_payload`) so charts and tables treat them like any other result.

## 4.4 Answer assembly

1. The loop returns text. Metric ids that slipped into prose are replaced by catalogue labels; long decimals are rounded; Indian digit grouping is applied (`answer_text.py`).
2. `grounding()` yields per-figure findings; `problems()` turns them into sentences; at most one correction run follows; `lost_substance()` keeps the original if the rewrite drops verified content; remaining issues append `unverified_note` and set the limitation `UNVERIFIED_FIGURES`.
3. If evidence exists and no limitation applies, `present.attach()` adds parts: `KPI_GRID`, `TABLE`, `CHART` according to result shape.
4. The runner writes the assistant message with typed parts (`TEXT`, `TABLE`, `CHART`, `KPI_GRID`, `SOURCE`, `WARNING`, `APPROVAL`, `AGENT_STATUS`, ...), a mission result, V3 artifacts, and activity events; the placeholder message created at submission is replaced.
5. `LoopResult.status` is `failed` when the answer is empty, `partial` when a limitation exists (`ANSWER_TRUNCATED`, `DEGENERATE_OUTPUT`, `EXECUTION_LIMIT_EXCEEDED`, `UNVERIFIED_FIGURES`), otherwise `completed`.

## 4.5 Conversation data

```mermaid
erDiagram
  threads ||--o{ messages : has
  threads ||--o{ runs : has
  runs ||--o{ run_attempts : has
  runs ||--o{ run_events : emits
  messages ||--o{ message_parts : has
  messages ||--o{ attachments : links
  runs }o--|| missions : executes
  missions ||--o{ evidence_artifacts : has
  runs ||--o{ run_memory_usage : records
  memories ||--o{ run_memory_usage : used_in
  threads ||--o{ thread_summaries : summarised_by
  messages ||--o{ message_feedback : rated_by
  runs ||--o{ v3_artifacts : produces
```

Full column lists are in chapter 10.

## 4.6 Memory flow

On every submitted user message, `MemoryCandidateExtractor.extract(message, thread)` produces candidates and `MemoryService.ingest_candidates` stores them. The `memories.status` column defaults to `PENDING_CONSENT`, and a database constraint allows `ACTIVE` only when `consented_at` is set; the other statuses are `CANDIDATE`, `SUPERSEDED`, `ARCHIVED`, `DELETED`. Users manage them through `/v1/memories*` (pin, confirm, move scope, archive, delete). `ContextBuilder` selects memories for a run and records the selection (`run_memory_usage`). Scopes are `THREAD`, `USER`, `PROJECT`, `EPISODIC`; episodic events are stored in `episodic_events`. Retrieval is lexical (generated `search_vector` column, `ts_rank` with `websearch_to_tsquery`); the optional `embedding vector(1536)` column exists only when pgvector is installed, which it is not.

## 4.7 Attachment flow

```mermaid
flowchart LR
  I[POST /threads/id/attachments: initiate] --> UP[PUT /attachments/id/content]
  UP --> CP[POST /attachments/id/complete]
  CP --> SC[ClamAV scan]
  SC -->|clean| RD[status READY, scan CLEAN]
  SC -->|infected or error| BAD[not usable]
  RD --> LINK[linked to a message at submission]
```

A message may reference only attachments of the same thread that are `READY` and `CLEAN` and not yet linked. Limits: `ATTACHMENT_MAX_SIZE_BYTES` 25 MiB; `ATTACHMENT_ALLOWED_MIME_TYPES` (default: JSON, PDF, CSV, plain text, JPEG, PNG, WebP). Blobs live in MinIO bucket `seleric-attachments` (or local disk).

## 4.8 Email flow

```mermaid
sequenceDiagram
  participant L as Loop
  participant S as Mail service (shared record store)
  participant U as User
  participant API as POST /v1/emails/{id}/approve
  participant G as Microsoft Graph
  L->>S: draft_mom_email or draft_ad_hoc_email (tables and charts by query id)
  S-->>L: preview record, version, content hash
  L-->>U: APPROVAL message part (recipients, subject, body, attachments)
  U->>API: Approve version N
  API->>S: approve bound to hash of sender, recipients, subject, body, attachments
  S->>S: mark SENDING before awaiting
  S->>G: send once
  G-->>S: accepted or refused
  S-->>API: status SUBMITTED or FAILED
```

## 4.9 Scheduled data flows

### Domain-health snapshots (`business-state-cron`)

`services.domain_health.scheduler` resolves, per domain, the metrics listed in `config/domain_health_profiles.yaml` through the semantic port (MCP), applies `business_state_profiles.yaml` features and the judge (`judge.py`), and writes JSON snapshots under `SELERIC_DOMAIN_HEALTH_DIR` (volume `domain_health`). The loop calls the scheduler every 60 seconds; the scheduler decides what is due. The agent reads the latest snapshot through `business_snapshot`; `GET /v1/monitoring/state` and `POST /v1/business-state` serve it.

### Target monitoring (`monitoring-cron`)

`services.monitoring.runner --brand 20` reads `monitor.metrics`, `monitor.targets_current`, `monitor.known_periods`, fetches series through the semantic layer, evaluates each metric against its floor/target/ceiling (`evaluate.py`), and writes `monitor.evaluations`, `monitor.findings` and `monitor.finding_events`. It never touches ClickHouse.

### Forecast scoring

`python -m seleric_swarm.forecasting.score --if-due` (once a day, driven by the business-state loop) compares stored forecast artifacts (`v3_artifacts`) with days that have since matured and writes reports into the `forecast_reports` volume (`SELERIC_REPORTS_DIR`).
