# 15-Question Smoke Test & Trace Audit — 2026-09-29

End-to-end test of the running `Seleric_Agent` stack (conversations API → V3 agent →
Cube/MCP), trace/log analysis, independent number verification, and an ordered
remediation backlog. Every point below carries a checklist so it can be worked
through independently.

- **Run window:** 2026-09-29 07:35–08:02 UTC (plus post-restart health checks)
- **Harness:** `/tmp/opencode/seleric_test/harness.py`
- **Raw data:** `/tmp/opencode/seleric_test/results/{analysis,enriched,all}.json`, `*.log`
- **Surfaces used:** `POST /v1/threads`, `POST /v1/threads/{id}/messages`, `GET /v1/runs/{id}/events/stream`, Postgres (`runs`, `run_events`, `missions`, `evidence_artifacts`), Cube REST `:4001`, MCP `:8765`
- **Baseline config:** `run_max_concurrency=4`, `AZURE_OPENAI_MODELS=["gpt-5-mini","DeepSeek-V4-Pro"]`, `AZURE_OPENAI_FAST_MODEL=gpt-5-mini`, `strong reasoning effort=low`, `LANGSMITH_TRACING=false`

---

## 1. Outcomes

| # | question | tier | status | error_code |
|---|----------|------|--------|-----------|
| q01 | greeting / what can you do | simple | completed | – |
| q02 | net sales yesterday | simple | completed | – |
| q03 | how is ROAS calculated | simple | completed | – |
| q04 | dimensions of net_sales | simple | completed | – |
| q05 | compare yesterday vs day before | moderate | completed | – |
| q06 | net sales by day, last 7 days | moderate | **failed** | `INSUFFICIENT_EVIDENCE` |
| q07 | top 5 products by gross sales | moderate | completed | – |
| q08 | CAC + ad spend, last 7 days | moderate | completed | – |
| q09 | why has CAC increased (3 days) | complex | completed | – |
| q10 | which channel/campaign drove the drop | complex | completed | – *(wrong method)* |
| q11 | does ad spend cause net sales | complex | **partial** | – |
| q12 | forecast net sales next 14 days | complex | completed | – *(refused, after full fetch)* |
| q13 | Meta vs Google ROAS, budget shift | complex | completed | – *(unsupported conclusion)* |
| q14 | weather in Paris yesterday | simple | completed | – |
| q15 | prompt injection / print system prompt | simple | **failed** | **none set** |

DB-wide (316 missions): completed 193 (61%), failed 105 (33%), partial 13, running 16, cancelled 1.
Top failure codes: `EXECUTION_LIMIT_EXCEEDED` 28, `LLM_RATE_LIMITED` 26, `V3_AGENT_FAILED` 25,
`INSUFFICIENT_EVIDENCE` 10.

### 1.1 q06 — valid answer thrown away, placeholder delivered
- [ ] Reproduce with `POST /v1/threads/{id}/messages` → "Show net sales by day for the last 7 days"
- [ ] Confirm in `missions.result_json`: 7 evidence artifacts exist, `answer.reset` fires, revision output = "Preparing… Fetching evidence."
- [ ] Fix time-grain resolution in `src/seleric_swarm/agent/scope.py` (`_BREAKDOWN_RE`, `_resolve_dimension_candidates`): `"by day"` → `report_date` grain, not categorical breakdown
- [ ] Fix `src/seleric_swarm/agent/validation/signals.py::check_scope_coverage` so calendar tokens can't match `hour_of_day` / `session_day_of_week`
- [ ] Add regression guard: a scope rejection must not replace a complete answer with a progress placeholder
- [ ] Note: same false-positive class as `docs/BUG_SHEET.md` #8 (pre-V3 `catalogue_grounding.py::_GENERIC_DIM_TOKENS`), deleted in the V3 cleanup — carry the calendar-token exclusion forward

### 1.2 q11 — causal estimator killed by one bad evidence id
- [ ] Reproduce: "Does increasing ad spend cause higher net sales? Estimate the effect."
- [ ] Read `src/seleric_swarm/toolsets/causal.py::_load_evidence` (all-or-nothing `get_many` check)
- [ ] Change to tolerant load: drop unknown ids with a warning; refuse only below a minimum valid count
- [ ] Stop routing hundreds of raw ids through the LLM — accept a query/evidence-set handle instead
- [ ] Re-run q11 and confirm a real `effect_estimate` (or a policy-based refusal, not `INSUFFICIENT_EVIDENCE`)

### 1.3 q13 — "compare Meta vs Google" answered from blended evidence
- [ ] Confirm evidence rows carry `channel='meta,google'` (7 rows, one per day) in `evidence_artifacts`
- [ ] Root cause: `query_metrics(dimensions={"channel":["meta","google"]})` is *filter* semantics; breakdown requires `{"channel": ""}`
- [ ] Add tool/prompt guard: a "compare X vs Y" request must produce per-group rows before an answer can claim a winner
- [ ] Add validation signal: reject a comparison whose evidence has exactly one aggregated group
- [ ] Re-run q13 and confirm per-channel ROAS (probe shows `google` 0.13 vs `meta` 1.28 on 2026-09-25)

### 1.4 q10 — change question answered with level ranking
- [ ] Reproduce: "Net sales fell last week — which channel and campaign drove the drop, and by how much?"
- [ ] Current answer ranks one period (2026-09-21..27) and calls the top level the "driver"
- [ ] Independent MCP check: unattributed **+66,407** WoW, Meta **+218,570**, Google **−31,506** → Google is the decliner
- [ ] Force period-over-period evidence for change/driver intents (`compare_period` or two queries)
- [ ] Add validation signal: a change-attribution answer must cite ≥2 periods
- [ ] Re-run q10 and confirm the named driver is computed from deltas

### 1.5 q15 — correct refusal, wrong status, wasted budget
- [ ] Confirm final text is a proper refusal while `runs.status=FAILED`, `error_code=None`
- [ ] Decide contract: refusal ⇒ `completed` (or set an explicit `REFUSED`/`UNSUPPORTED` code)
- [ ] Add an `unsupported` / `policy` intent so refusals exit before tool exploration
- [ ] Cap refusal paths at ≤3 tool calls (today: 21 calls, 6 tool types, 90 s, repeat-guard hits)
- [ ] Re-run q15 and assert status, error code, tool count, and duration

### 1.6 q12 — policy refusal paid for with 364 rows
- [ ] Confirm `no_approved_model` is only raised after the daily series fetch (exec 101 s)
- [ ] Pre-flight the model registry before any `query_metrics` call for forecast-class intents
- [ ] Re-run q12 and assert the refusal arrives without fetching history

### 1.7 Classification & claims quality
- [ ] Surface `complexity` — it is always `None`, so `_prefer_fast_model` / `_should_plan` act on intent only
- [ ] Fix Jev label accuracy (q03 definition → `diagnostic`; q04/q05/q07/q08/q09/q13/q14 → `trend`; q15 → `lookup`)
- [ ] Investigate why `claims` is 0 across every run while the claim-gate design is live — populate it or delete it
- [ ] Fix stale `mission_lead="coordinator"` on results

---

## 2. Latency

Server-side, from `runs` + `run_events` (client-receipt times lag by the terminal-flush delay):

| q | queue s | exec s | **first token s** | stream s | terminal SSE lag |
|---|--------:|-------:|------------------:|---------:|-----------------:|
| q01 | 0.5 | 13.8 | 10.0 | 3.8 | +5.0 |
| q02 | 0.5 | 26.1 | 26.0 | 0.1 | +5.0 |
| q03 | 0.5 | 40.4 | 36.5 | 3.9 | +5.0 |
| q04 | 0.5 | 37.9 | 37.2 | 0.7 | +5.0 |
| q05 | 26.5 | 20.1 | 15.9 | 4.3 | +5.0 |
| q06 | 14.2 | 36.6 | 27.9 | – | – |
| q07 | 2.4 | 27.3 | 23.2 | 4.1 | +5.0 |
| q08 | 41.9 | 19.7 | 17.7 | 2.0 | +5.0 |
| q09 | 2.2 | 43.4 | 36.2 | 7.2 | +5.0 |
| q10 | 2.2 | 31.9 | 23.1 | 8.8 | +5.1 |
| q11 | 11.4 | 74.9 | 66.2 | 8.6 | +5.2 |
| q12 | 80.0 | 101.5 | 47.8 | 53.6 | +5.0 |
| q13 | 106.3 | 26.9 | 21.3 | 5.5 | +5.1 |
| q14 | 31.7 | 23.8 | 22.9 | 0.9 | +5.0 |
| q15 | 28.8 | 90.4 | 90.3 | – | – |

**Medians:** queue 11.4 s (max 106.3) · exec 31.9 s (max 101.5) · **first token 26.0 s (max 90.3)** ·
stream 4.1 s · post-completion 0.01 s server-side / **+5.0 s to the client**.

### 2.1 First token is ~80% of execution
- [ ] Instrument per-phase timing inside `agent/runner.py` (classify → values → snapshot → LLM round → tool → final)
- [ ] Confirm split: pre-agent work (`catalogue_resolve_values` median 2.6 s / p90 5.5 s / max 10.8 s, plus Jev classify and `_catalogue_snapshot.warm()`) runs serially before the first LLM turn
- [ ] Confirm tool-round count ≈ `tool_call` count + 1, each LLM round-trip ~4–8 s (q01: 1 call/10 s, q04: 5 calls/37 s, q15: 21 calls/90 s)
- [ ] Add a fast path for `conversation`/`greeting`/`definition`/`out_of_scope` intents: single LLM call, fast model, no planning, no values resolve — target q01/q03/q04 < 8 s
- [ ] Re-measure first-token after each change with the same harness

### 2.2 Pre-agent pipeline is serial and uncached
- [ ] Run Jev classify, `catalogue_resolve_values`, and snapshot warm concurrently
- [ ] TTL-cache `catalogue_resolve_values` (identical resolves recur across missions)
- [ ] Add a lock/single-flight around `_catalogue_snapshot.warm()` (currently herd-prone under concurrency 4)
- [ ] Verify no regression in metric-id canonicalization (see `docs/BUG_SHEET.md` #2)

### 2.3 Nothing streams while tools run
- [ ] Confirm `run_stream` deltas only cover the final node → 0 bytes shown during tool rounds
- [ ] Emit a status/progress token before the first tool round (and on each round) so UI/voice have a visible signal
- [ ] Consider streaming intermediate deltas rather than only the final answer

### 2.4 Queueing and admission control
- [ ] Record that queue median is 11.4 s and max 106.3 s (q13 waited 106 s behind q12's 101 s run)
- [ ] Add a priority lane so simple/conversation runs never wait behind complex missions
- [ ] Make admission token/cost-aware instead of a flat `run_max_concurrency=4`
- [ ] Validate the chosen concurrency against per-model TPM (concurrency 4 triggered the 429 storm in §4)

### 2.5 Terminal SSE flush adds a constant +5 s
- [ ] Reproduce: deltas arrive on time, `run.completed` reaches the client ~5 s after `answer.completed`
- [ ] Flush the stream immediately on terminal events (`answer.completed`, `run.completed`, `run.failed`)
- [ ] Add `Cache-Control: no-cache` / disable proxy buffering on the SSE route
- [ ] Re-measure client-side wall time and confirm it converges to queue + exec

---

## 3. Correctness verification (independent)

- [x] CAC + spend reproduced through the MCP (`metrics_query`): agent 1,836.88 / 222,262.38 → now 1,838.12 / 222,412.65 (hourly data drift, brand 20)
- [x] `net_sales` 2026-09-27 = 84,835.08 — exact match
- [~] `net_sales` 2026-09-28: agent −1,021,977.24 vs current −1,020,876.39 (**0.11%**, day is `is_final=0`, still moving)
- [x] Documented that raw Cube queries **without** the MCP's default `brand_id=20` filter differ by ~28% — external verification must apply that scope
- [ ] Build a permanent verifier: replay the 15 questions and assert each numeric claim against Cube with brand scope
- [ ] Add the brand-scope caveat to any dashboard/docs that query Cube directly

---

## 4. Reliability

### 4.1 Rate-limit storm (22 + 22)
- [ ] Evidence: 22 × `RateLimitError` + 22 × "All models from FallbackModel failed" in `recovery.log` — DeepSeek-V4-Pro (southindia), DeepSeek-V3.2 (swedencentral), gpt-5-mini
- [ ] Mission-visible symptom: "The language model is rate-limited right now. Please retry in a moment." (3 complex missions failed)
- [ ] Add request-level retry with backoff *before* falling back to the next model
- [ ] Add per-model TPM budgets and shed load to the queue instead of failing the mission
- [ ] Re-tune cooldowns (`model_health.py` 45/90/30/300 s) — they cannot help when every tier is limited
- [ ] Confirm no 429s post-restart and watch `LLM_RATE_LIMITED` counts over the next 24 h

### 4.2 Artifact store is per-process and in-memory
- [ ] Confirm `InMemoryArtifactStore` ("no durability guarantee across process restarts") and that api/recovery containers do not share it
- [ ] Decide: persist artifacts (DB/minio) or explicitly forbid cross-process evidence handoff
- [ ] Add a test that fails if evidence written in one container is read in another

### 4.3 Stuck missions
- [ ] 16 missions sit in `running` with no observed reaper
- [ ] Add a reaper for `running` older than ~15 min (lease/heartbeat based)
- [ ] Backfill-mark the 16 stale rows and confirm they stop inflating dashboards

### 4.4 Failure taxonomy drift
- [ ] `V3_AGENT_FAILED` = 25 — sample and confirm they are all 429/fallback exhaustion, not new exceptions
- [ ] `EXECUTION_LIMIT_EXCEEDED` = 28 — confirm the budget caps that trigger it are intentional
- [ ] Track the completed/failed ratio weekly as a release health metric

---

## 5. Observability gaps

### 5.1 No progress events on the streamed path
- [ ] Confirm `_run_agent_streamed` (`agent/validation/__init__.py`) never passes `event_stream_handler`
- [ ] Confirmed symptom: every SSE stream contained only `run.queued` / `run.started` / `answer.*` / `run.completed` — 0 `agent.tool_*` events, `steps=0` in the client view
- [ ] Wire the handler so `agent.tool_started` / `agent.tool_completed` reach the stream
- [ ] Verify UI progress and voice narration (`voice/worker.py:537`) receive events again

### 5.2 Validation outcome never persisted
- [ ] `runner.py:751-755` and the trace rebuild (~line 1015) drop `trace.validation`
- [ ] Persist verdict, trust_score, and revision count into `result_json.trace`
- [ ] Expose them via `GET /v1/missions/{id}`

### 5.3 Trace metadata missing
- [ ] Persist `intent` and `complexity` (currently absent from `trace` keys)
- [ ] Add timestamps to trace steps (today only durations are derivable, and only server-side)
- [ ] Fix `trace.elapsed_seconds`, which excludes pre-agent work (classify/values/catalogue)

### 5.4 API surface returns only `raw_json`
- [ ] `GET /v1/missions/{id}` returns raw output; `result_json` (steps, claims, query_class, limitations) is readable only via psql
- [ ] Return a projection of `result_json` (or add a `?include=trace` variant)
- [ ] Document the contract change

### 5.5 Tracing disabled
- [ ] `LANGSMITH_TRACING=false` — enable in dev, or replace with local structured step timing
- [ ] Decide whether `trace.langsmith_run_id/url` should be dropped when tracing is off

---

## 6. Recommendations (ordered backlog)

### Correctness
- [ ] **R1** Time-grain vs categorical-breakdown fix in `scope.py` + `signals.py`, and never emit a progress placeholder as `final_response` (see §1.1)
- [ ] **R2** Guard compare/change intents: require per-group rows and ≥2 periods; reject single-aggregate evidence (see §1.3, §1.4)
- [ ] **R3** Tolerant evidence loading + evidence-set handle for the causal estimator (see §1.2)
- [ ] **R4** `unsupported`/`policy` intent with immediate early exit; correct refusal status/error code; ≤3 tool calls on refusals (see §1.5)
- [ ] **R5** Model-registry policy check before data fetch (see §1.6)
- [ ] **R6** Populate claims or remove the claim gate (see §1.7)

### Latency
- [ ] **R7** Fast path for simple intents — target q01/q03/q04 < 8 s first token (see §2.1)
- [ ] **R8** Parallelize + cache pre-agent work; lock the catalogue snapshot warm (see §2.2)
- [ ] **R9** Cut tool rounds: batch multi-metric `resolve_concept` (19 single calls vs 5 uses of batch `get_metric_definitions`), emit early status tokens, stream intermediate deltas (see §2.3)
- [ ] **R10** Token-aware admission control with a simple-task priority lane (see §2.4)
- [ ] **R11** Flush SSE on terminal events (see §2.5)

### Reliability & observability
- [ ] **R12** 429 handling: per-model TPM budgets, backoff-before-fallback, load shedding into the queue (see §4.1)
- [ ] **R13** Persist `intent`/`complexity`/`validation`/step timestamps; surface them on the API (see §5.2, §5.3, §5.4)
- [ ] **R14** Fix Jev label accuracy and surface `complexity` so routing decisions are real (see §1.7)
- [ ] **R15** Reap stuck `running` missions; persist artifacts across processes (see §4.2, §4.3)

---

## 7. Reproduction

- [ ] `cd /mnt/c/SpacePeppers/SpacePeppers/Seleric_Agent && docker compose up -d && curl -s localhost:8091/readyz`
- [ ] `python3 /tmp/opencode/seleric_test/harness.py` (set `CONCURRENCY=1` or `2`; concurrency 4 triggers the 429 storm)
- [ ] Rebuild analysis: `python3` over `results/*.json` (schema documented in the harness)
- [ ] Inspect authoritative timings:
  `select mission_id, status, extract(epoch from started_at-created_at), extract(epoch from completed_at-started_at) from runs order by created_at desc limit 15;`
- [ ] Inspect evidence: `select evidence_id, metric_or_fact, value_json, dimensions from evidence_artifacts where mission_id='MS3-...';`
- [ ] Inspect validation (not in API response): `select result_json->'trace' from missions where mission_id='MS3-...';`
- [ ] Direct MCP probe (token `SELERIC_MCP_TOKEN` in `.env`): `tools/call` → `metrics_query`
- [ ] Cube sanity: `curl 'http://127.0.0.1:4001/cubejs-api/v1/load?query=<json>'`
