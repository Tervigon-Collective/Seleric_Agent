# Slim-loop rebuild of Seleric_Agent: multi-session plan

## Context

Seleric_Agent (`/opt/seleric/Seleric_Agent`, branch `gaurav`, live on :8090) has grown to about 76k lines in 509 files. Before the agent even starts, every turn goes through these stages:

- understand, restyle and deepen-diagnosis
- the v2 shadow
- QuerySpec
- plan_from_slots
- prefetch and insights

After that, a validated-revision wrapper can re-run the agent up to 3 times. On the golden 30 the median is about 55–73s and p90 is about 134s. Most `runner.py` code is a patch for one specific incident.

`/opt/seleric/test_agent` is a reference. It is a single LLM↔MCP loop of about 850 lines (`app.py`) that runs against the same core MCP server (`Seleric_Agent_Core`, `seleric-mcp-mcp-1:8765`). It computes the calendar in code and checks numbers deterministically. On its 6 questions with gpt-5-mini low: 0 failures, 99% grounded, p50 23s.

**Goal:** rebuild Seleric_Agent in test_agent's shape: one PydanticAI pass, low latency, grounded numbers, correct reasoning. Keep the product surfaces and the harness lessons Seleric has already paid for, and delete everything else. test_agent is a reference, not a replacement, and it is still changing (it was edited during planning on 2026-10-13), so re-sync from it at the start of each session.

## Decisions (interview 2026-10-13, also saved in memory `slim-loop-rebuild-2026-10-13.md`)


| Topic             | Decision                                                                                                                                                                                                                                                        |
| ----------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Codebase          | Seleric_Agent stays. Work on `gaurav` directly. Deploy freely from the working tree, with a rollback tag before every deploy. **Commit only when the user says so** (`plans/` convention).                                                                      |
| Plan home         | `/opt/seleric/Seleric_Agent/plans/SLIM_LOOP_PLAN.md` is the source of truth, with a status board and progress log. Coordinate in `plans/AGENT_CHAT.md` (append-only, sign lines `SL`). `REFACTOR_PLAN_2026-10-10.md` and `REFACTOR_2.md` are marked superseded. |
| Delete            | understand/restyle/deepen, v2 shadow, QuerySpec, plan_from_slots, prefetch/insights, `run_validated_mission`, business-state fast path, Meta write actions and approvals. Delete as I go: no flag, no parallel engine. Delete the tests for deleted code too.   |
| Keep surfaces     | threads/runs + office-ui, memories + attachments, voice, business-state cron, mission events timeline, Langfuse/LangSmith, metering, Prometheus                                                                                                                 |
| Tools in the loop | Core MCP read tools plus local tools: DoWhy diagnosis (a helper during reasoning), forecasting (Chronos), explore_data, run_python, business snapshot, chart/table, `escalate`                                                                                  |
| Engine            | PydanticAI 2.45: `Tool.from_schema`, `AbstractToolset`, `WrapperModel`, `Agent.iter`, `FallbackModel`, `UsageLimits` and `parallel_tool_calls` are all verified present in the live container                                                                   |
| Model             | gpt-5-mini, low effort. 429 fallback to gpt-4o-code through the existing FallbackModel chain. The `escalate` tool switches the remaining steps to medium effort. No classifier and no keyword rules.                                                            |
| Grounding         | Deterministic check, then one corrective pass. Anything still unmatched ships, flagged as unverified.                                                                                                                                                           |
| Answer            | Markdown plus charts/tables. The model can request a chart; otherwise one is built from the main result.                                                                                                                                                        |
| Domain rules      | These move into Core MCP instructions and tool descriptions. Show the user the Core diff before pushing, because Core main redeploys for every client.                                                                                                          |
| Data              | Clean slate: delete old threads and data. Confirm the exact tables and buckets first and take backups.                                                                                                                                                          |
| Done bar          | Golden 30 (live, ClickHouse truth) and test_agent's 6 both pass, at p50 ≤ 25s and p90 ≤ 45s                                                                                                                                                                     |


Standing rules (from memory): no hardcoded metric names or keywords, and no regex in new code. Reuse the existing `scan_numbers` for number parsing. Verify live: re-run the real questions and cross-check them in ClickHouse.

## Target architecture

```
office-ui / voice / cron ─► api (conversations, office, business_state, async_missions, recovery)  [unchanged surfaces]
                              │
                              ▼
                 agent/runner.py::run_v3_mission   (~250 lines: context → loop → persist; same signature/return)
                              │
                              ▼
                 agent/loop.py::run_turn           (one PydanticAI Agent[SelericDeps,str], Agent.iter, single pass
                              │                     + one grounding correction; cancellation checked between nodes)
            ┌─────────────────┼───────────────────────────┐
            ▼                 ▼                           ▼
  agent/mcp_toolset.py   agent/local_tools.py       agent/grounding.py
  (tools/list at boot,   diagnose_metric_change,    (port of test_agent grounding: measured /
   allowlist, schemas →  break_down_metric,          derived / misbound / placeholders, row-bound,
   Tool defs, jsonschema forecast_metrics,           over this turn's + recent turns' payloads)
   validate, misuse      explore_data, run_python,
   checks, compact,      business_snapshot,
   flag_partial, per-    show_chart / show_table,
   turn dedup, evidence  escalate
   artifacts)
            │
            ▼
  protocols/mcp (MCPGateway + RemoteToolServer, plus list_tools)  ─►  Seleric_Agent_Core :8765  ─►  Cube v2
```

- **Prompt (**`agent/prompt.py`**):** test_agent's generic "How to work" and "Answer" blocks, the catalogue index from `CatalogueSnapshot.render_compact`, and the MCP server instructions (once Core is fixed). The calendar goes last so the prefix stays byte-stable for prompt caching. It includes the cut-off hour and the partial-today facts.
- **Effort:** `agent/model.py` gains an `EffortModel(WrapperModel)` that reads a per-mission ContextVar set by `escalate` and merges in `openai_reasoning_effort`. It wraps the existing `resolve_v3_model` chain: HealthGated → Fallback → Patient.
- **Budget:** a `PreparedToolset` hook withdraws all tools when steps, tool calls or wall time are exhausted, and the model is told to answer from the evidence it has. This replaces test_agent's `tool_choice=none` forced final.
- **Follow-ups:** history comes from `context_bundle.recent_messages` (last N pairs) plus the TurnRecord, which now also carries a `queries` digest (query_id → tool + args) so `metrics_drilldown` works across turns. Prior payloads come from the prior turn's evidence artifacts, for grounding.
- **Evidence:** every successful `metrics_query` / `metrics_drilldown` / `insights_explain` writes `EvidenceArtifact`s (lifted from `toolsets/semantic.py::_put_evidence`) and returns `evidence_ids` in the tool result. SOURCE parts, charts, run_python and diagnosis keep working unchanged.



## Session protocol (every session)

1. Read `plans/SLIM_LOOP_PLAN.md` (status board and progress log), the tail of `plans/AGENT_CHAT.md`, and memory `slim-loop-rebuild-2026-10-13.md`.
2. Run `git -C /opt/seleric/Seleric_Agent status && git log --oneline -10 && git stash list`.
  - Commits only happen when the user says so, so the working tree holds this rebuild's uncommitted work. Before a big deletion, save `git diff` and `git status` to `~/work/backups/slim-<date>/` so the tree can be restored.
  - If someone else's work is in flight (a teammate or another Claude session; there is a stash `other-track-wip`), post in AGENT_CHAT and coordinate before building or deleting.
3. **Reference sync:** compare `sha256sum /opt/seleric/test_agent/app.py` with the last value in the progress log. If it changed, diff the relevant functions (grounding, flag_partial, filter_misuse, compact, prompt) and port what is new.
4. Pick the next unchecked task and mark it `in progress` on the status board. Before a deploy, tag the images `rollback-YYYYMMDD<letter>` (the existing convention, e.g. `seleric_agent-api:rollback-20261012e`). When the user asks for a commit, use prefix `slim:` on `gaurav`.
5. Deploy: `docker compose build api recovery business-state-cron voice && docker compose up -d api recovery business-state-cron` (voice only when changed). Smoke test, then re-run the relevant questions live.
6. End of session: update the status board and progress log in `plans/SLIM_LOOP_PLAN.md` (done, numbers, test_agent sha, next step). Post one line in AGENT_CHAT and update the memory file.



## test_agent reference snapshot (last synced 2026-10-13 21:52)

The reference `app.py` has sha256 `6b3c195e9e09…`, is 865 lines, and was edited at 21:32 and 21:52 during planning. The table shows what each feature means for the port. Items marked ✚ were added after the first draft of this plan.


| test_agent feature                                                                                                                                                                                      | Port decision                                                                                                                                                                                                                                  |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Allowlist (16 read tools); schemas from `list_tools`; jsonschema `validate`; nulls stripped                                                                                                             | Port (Phase 1, `mcp_toolset`)                                                                                                                                                                                                                  |
| ✚ `filter_misuse`: set/notSet carrying values silently returns the unfiltered total                                                                                                                     | Port. Returned as an invalid-arguments tool result                                                                                                                                                                                             |
| ✚ `flag_partial`: generic warning when `provenance.time_range.end ≥ today` (IST)                                                                                                                        | Port. Combine with Seleric's cut-off-hour facts                                                                                                                                                                                                |
| `compact`/`_shrink`: drop plumbing keys, cap lists with an explicit marker, never cut a number. ✚ explicit NULLs are kept                                                                               | Port as written                                                                                                                                                                                                                                |
| Per-turn dedup of identical calls (shared future)                                                                                                                                                       | Port                                                                                                                                                                                                                                           |
| Metric index by view (date axis + dims union)                                                                                                                                                           | Use `CatalogueSnapshot.render_compact` if it carries view, date axis and dims. Otherwise port `build_metric_index`, replacing its regex with `str.partition`                                                                                   |
| ✚ Grounding v3, one module with these parts:                                                                                                                                                            | Port with Phase 1 grounding                                                                                                                                                                                                                    |
| · row-bound entity scoping, with numbers carried under a header line                                                                                                                                    |                                                                                                                                                                                                                                                |
| · `misbound` (a figure taken from another row)                                                                                                                                                          |                                                                                                                                                                                                                                                |
| · `placeholders` ("?", "7?")                                                                                                                                                                            |                                                                                                                                                                                                                                                |
| · arithmetic only within a row's entity plus loose totals                                                                                                                                               |                                                                                                                                                                                                                                                |
| · `SMALL_POOL=40` free-combine                                                                                                                                                                          |                                                                                                                                                                                                                                                |
| · bisect derived scan (a±b, a/b, % change, share)                                                                                                                                                       |                                                                                                                                                                                                                                                |
| · only payloads with a `query_id` feed arithmetic and labels; catalogue results can only be quoted                                                                                                      |                                                                                                                                                                                                                                                |
| · `_day` turns timestamp labels into day labels                                                                                                                                                         |                                                                                                                                                                                                                                                |
| · metric names are never entities                                                                                                                                                                       |                                                                                                                                                                                                                                                |
| ✚ `problems()` → one corrective pass with the exact corrective text: no "?" or guesses, fetch or say unavailable, query the total instead of summing rows                                               | Port verbatim                                                                                                                                                                                                                                  |
| ✚ Still failing after the pass: append "⚠ Not verified against the data: …" to the answer, also streamed                                                                                                | Port. Also write it to `limitations` and `trace.grounding`                                                                                                                                                                                     |
| ✚ Prior payloads: the last `KEEP_PAYLOADS=8` tool payloads of the thread join the grounding pool, so follow-ups are checked too                                                                         | Persist compacted payloads per turn (a `turn_payloads` artifact keyed by thread) and reload the last 8. Evidence artifacts alone lose the row shape                                                                                            |
| History: last `HISTORY_TURNS=6` pairs, with the "[evidence query ids]" digest appended to each assistant turn                                                                                           | Port. Build from `context_bundle.recent_messages` plus the digest stored in the TurnRecord                                                                                                                                                     |
| Forced final at the step, tool or time budget, with the message "Tool budget reached. Write the final answer now…"                                                                                      | Port through the `PreparedToolset` withdraw hook plus that message                                                                                                                                                                             |
| ✚ `degenerate()`: content_filter, a repetition loop, or `length` without a tool call → retry the step once at temperature ≥0.4. Twice → fixed "couldn't produce a reliable answer" text, never the junk | Port. Use word split, not regex                                                                                                                                                                                                                |
| Reasoning models: `max_completion_tokens = MAX_TOKENS + REASONING_BUDGET (6000)`, effort low, default temperature. Others: `max_tokens` and temperature 0                                               | Port into the per-model settings. Verify that PydanticAI sends `max_completion_tokens` for gpt-5*                                                                                                                                              |
| Text tool protocol for Llama-4 (Azure's native tool parser garbles arguments)                                                                                                                           | **Do not port. Remove Llama-4 from the loop's fallback chain**, because PydanticAI uses native tools. `.env` `AZURE_OPENAI_MODELS` currently ends in Llama, and `.env` is user-owned: tell the user and propose `["gpt-5-mini","gpt-4o-code"]` |
| discover/execute meta-tools when there are more than 30 tools                                                                                                                                           | Not needed (16 MCP + ~8 local). Not ported                                                                                                                                                                                                     |
| Server guide off (stale ids)                                                                                                                                                                            | Phase 4 fixes Core, then the guide is switched on                                                                                                                                                                                              |
| Debug events (llm_start/end with ttft, tokens, cached, finish; tool_start/end with ms, chars, dup, query_id; revise; done stats)                                                                        | Mirror them into `trace.steps` and `trace.stats` for the mission `/trace` endpoint                                                                                                                                                             |
| ✚ `replay.py`: an 11-turn real UI conversation in one session, recorded per turn for warehouse checks                                                                                                   | Add to the harness (Phase 0)                                                                                                                                                                                                                   |
| ✚ `test_grounding.py`: 14 offline cases from real failures. It is still changing; it changed again after 21:52                                                                                          | Port every case present at Phase 1 time as `tests/unit/test_grounding.py`. Re-sync new cases each session                                                                                                                                      |
| `eval.py`: 6 questions, `CHECKS`, Q1 reference from a direct MCP `net_sales` query (yesterday vs previous), rescore mode                                                                                | Port, including the Q1 reference check                                                                                                                                                                                                         |




## Phases

Each phase ends deployable and verified, with the progress log updated.

### Phase 0: Baseline and harness (1 session)

- [x] Save this plan as `/opt/seleric/Seleric_Agent/plans/SLIM_LOOP_PLAN.md`, adding a status board (phase/task, owner, state, date) and a progress log. Record the test_agent `app.py` sha256. — **done 2026-10-13; test_agent app.py sha recorded**
- [x] Add a one-line header "Superseded by SLIM_LOOP_PLAN.md (2026-10-13)" to `plans/REFACTOR_PLAN_2026-10-10.md` and `plans/REFACTOR_2.md`. Post in `plans/AGENT_CHAT.md` that v2 modules (understand/spec/plan/derive/investigate/answer/pipeline) are frozen pending deletion. — **done 2026-10-13**
- [x] Move the golden harness into the repo, because the scratchpad is ephemeral. Copy `/tmp/claude-1000/-opt-seleric/06f1f257-…/scratchpad/regress/{regression.json,run_regression.py}` and `../ch.sh` to `eval/regress/`. Keep `contains_sql` live truth. — **done; note only 7 of 30 golden items carry checks**
- [x] Add `eval/regress/test_agent6.json`: test_agent's 6 questions (Q5 and Q6 in one thread). Port its `CHECKS` as predicates over the mission's tool-call args, read from `trace.steps` / run events. — **done; Q1 reference, wall/LLM/tool/grounding recording and p50/p90 included**
  - Add the Q1 reference check: a direct MCP `metrics_query net_sales yesterday` + `previous_period`, then check the answer has both values and the right direction.
  - Extend `run_regression.py` to record wall time, LLM calls, tool calls and grounding.
  - Report p50/p90.
- [x] Add `eval/regress/replay11.json`: test_agent `replay.py`'s 11-turn session, as one API thread in order. Record each turn's answer, grounding, revisions and tool calls. Spot-check the figures in ClickHouse; this is the multi-turn follow-up gate. — **file done; NOT yet run on the new loop**
- [x] Baseline run on the current live system: golden 30 and the 6. Record pass counts and p50/p90 in the progress log. — **PARTIAL: stopped early by user; Q1-Q4 of the 6 FAILED on the old system; golden 30 / replay11 baselines never completed**



### Phase 1: Build the loop and swap it in (1–2 sessions)

New files, reusing existing pieces:

- [x] `protocols/mcp/servers/seleric_remote.py`: add `list_tools()` (JSON-RPC `tools/list`) and keep the existing retries, session re-init and timeouts. Add `MCPGateway.list_tools()` in `protocols/mcp/gateway.py`. — **done (transport + gateway `list_tools`, plus `server_instructions`)**
- [x] `agent/mcp_toolset.py`, an `AbstractToolset[SelericDeps]`: — **done; allowlist sorted, byte-stable prefix. `insights_explain` added to the gateway TOOLS**
  - Fetch the schemas once at boot (cached on runtime) and apply the read-only allowlist: test_agent's list, with no `actions_*` or `semantic_sql`.
  - Tool defs come straight from the MCP schemas.
  - `call_tool`:
    - strip nulls;
    - jsonschema validate, then `filter_misuse`;
    - call through `MCPGateway` with `mcp_call_timeout_s`;
    - `flag_partial`;
    - per-turn dedup cache;
    - `compact`;
    - evidence write.
  - Errors are returned as tool results (no ModelRetry).
  - Port `compact`, `_shrink`, `flag_partial` and `filter_misuse` from test_agent.
- [x] `agent/grounding.py`: port test_agent `grounding` and `problems`. Replace its regex `answer_numbers` with the existing `agent/validation/grounding.py::scan_numbers` and `_matches`, moved here before that package is deleted. — **done, DEVIATION: own character-scan number reader instead of `validation/grounding.py::scan_numbers`; adds entity/row binding, percent-follows-from-figures rule, identifier rules, `lost_substance`**
- [x] `agent/prompt.py`: as described above. Put the domain-rules block in a temporary `prompts/data_rules.md`, deleted in Phase 4. — **done, DEVIATION: domain rules are a `DATA_RULES` constant in `prompt.py`, not `prompts/data_rules.md` (Phase 4 deletes it)**
- [x] `agent/local_tools.py`: register the existing tools unchanged at first: — **done; helper results are returned as a slim `ToolView` (the 82k-char diagnosis result had been re-sent every step); `run_python` contract restored 2026-10-14**
  - `toolsets/diagnosis.py::diagnose_metric_change`
  - `toolsets/composition.py::break_down_metric`
  - `toolsets/forecasting.py::forecast_metrics`
  - `toolsets/exploration.py::explore_data`
  - `toolsets/sandbox.py::run_python`
  - `toolsets/analytics.py::generate_visualization`
  `SelericDeps` defaults make `required_scope` and `resolved_window` empty or None. New tools:
  - `escalate(reason)` sets the effort ContextVar.
  - `business_snapshot()` returns the latest `SnapshotStore().aget_latest("business")` JSON when it isn't stale, else "unavailable". There is no LLM formatter.
- [x] `agent/loop.py::run_turn(deps, question, history, on_stream)`: — **done, DEVIATIONS: `agent.run` + event handler (not `Agent.iter`); answer delivered as one delta at the end (real streaming = Phase 5); `turn_payloads` saved by the runner; effort via `EffortModel`**
  - `Agent.iter` with `parallel_tool_calls=True` and `UsageLimits`.
  - Emit progress events per node through the existing `agent/progress.py` (`agent.stage`, `agent.tool_started` / `tool_completed`, `agent.answering`), so the UI stepper keeps working.
  - Check `is_cancel_requested` between nodes.
  - After the final text, run grounding over this turn's payloads plus the thread's last 8. If `problems()` reports any (unmatched, misbound or placeholders) and budget remains, continue with `message_history` plus the verbatim corrective message (one pass), sending `on_stream("reset")` before re-streaming.
  - Leftover problems are appended to the answer as "⚠ Not verified against the data: …". They also go into `limitations` and `trace.grounding`.
  - Degenerate guard: retry a degenerate step once. A second degenerate step gives the fixed "couldn't produce a reliable answer" message.
  - Save this turn's compacted payloads as a `turn_payloads` artifact.
  - Per-model settings: for reasoning models, set completion cap = MAX_TOKENS + REASONING_BUDGET and use the default temperature. Non-reasoning models get temperature 0.
  - The answer is cleaned with existing `answer_audit` text helpers (`replace_metric_ids`, `realign_markdown_tables`, `unescape_literal_newlines`), moved alongside.
- [x] Rewrite `agent/runner.py::run_v3_mission` with the same signature and return contract: — **done (455 lines, same contract); auto chart/table parts NOT built (Phase 3)**
  - register, create the Mission, load the catalogue snapshot and prior TurnRecord, build deps and history;
  - wrap in `mission_trace`, run `run_turn`, build the auto chart/table parts;
  - persistence stays as it is: `v3_store.finish`, `_write_turn_record` with a stub classification plus the queries digest, `v3_raw_snapshot`, `_to_lookup`, `runtime.store.put`;
  - errors map through `_user_facing_agent_failure`.
- [x] Unit tests: — **PARTIAL: `tests/unit/agent_loop` has 43 passing (loop 16, grounding 23, diagnosis batching 4). Not covered: dedicated runner-contract test, `mcp_toolset` compact NULL case, escalate-only test beyond effort propagation. Confirm all 14 test_agent grounding cases are covered (23 grounding tests exist)**
  - `mcp_toolset`: validation, misuse, compact (including NULL kept), dedup.
  - `grounding`: all 14 `test_agent/test_grounding.py` cases.
  - The loop: budget and forced final, degenerate retry and refusal, escalate.
  - The runner contract.
- [x] Fallback chain: tell the user that `.env` `AZURE_OPENAI_MODELS` ends in Llama-4, which can't do native tools on Azure, and propose dropping it. — **done: Llama dropped, chain is gpt-5-mini then gpt-4o-code**
- [ ] Deploy (rollback tag first). Run golden 30, the 6 and replay11 live. Gate: golden pass count ≥ the Phase 0 baseline, the 6 match test_agent's check rates, and replay11 has no misbound or placeholder figures. If the gate fails, fix forward or redeploy the rollback tag. — **DEPLOYED (rollback-20261013a..f, 20261014a..d); GATE OPEN, see "Phase 1 gate: remaining" below**

#### Phase 1 gate: remaining (as of 2026-10-14)

- [ ] Add `contains_sql` checks for the 23 unchecked golden items, then run golden 30 live (concurrency 1) and record `checked_pass`.
- [ ] Run replay11 live: no misbound or placeholder figures; spot-check figures in ClickHouse.
- [ ] The 6 at test_agent's check rates: currently 5/6 (Q6 scope-reuse check fails; cause not yet inspected). Re-run to separate variance from a real gap (Q4 "ROAS attempted" also flipped once).
- [ ] Latency: p50 61s / p90 84s vs bar 25s / 45s (bar belongs to Phase 5, but the "why" questions at 70-110s are the main gap).
- [ ] `run_python` still wastes about 2 calls per use (model ignores the docstring); expose rows to the model differently.
- [ ] `trace.stats.errors` does not record `ok:false` run_python returns.
- [ ] Diff test_agent `app.py` (sha 422fc64e…) against the ported pieces (port table is stale).
- [ ] Q5 like-for-like experiment needs at least 5 repeats per variant.
- [ ] Quality items still open: model offers CSV/actions; Q1 hedged opening; ids leaking in prose (partly fixed).
- [ ] Needs user: Core fix for broken `net_roas` / `mer` metrics (Phase 4 diff first); Phase 2 wipe list; commit timing (nothing committed, 76 files changed).




### Phase 2: Delete the old pipeline and wipe data (1 session)

**Status: COMPLETE with carry-overs (2026-10-14).** Code deleted, data wiped, deployed (`rollback-20261014e` = build before this phase), unit + api + contract tests 1013 pass. Uncommitted. Backups in `~/work/backups/slim-20261014/`: `tracked.diff`, `untracked.tgz`, `status.txt`, `stash0.diff`, `seleric_swarm.dump`, `stage_traces.tgz`, `minio_seleric_attachments.tgz`.

**Carry-overs (not done in Phase 2, with owner/phase):**
- office-ui `api/phase7.ts::decideApproval` and `ApprovalCard.tsx` still exist and call dead endpoints (office-ui belongs to another editor). Remove when they finish.
- ~~Repo `docker compose build` fails on office-ui `tsc`~~ works again (the other editor removed the unused import); `voice` image still not rebuilt.
- ~~ruff and mypy not green~~ **FIXED 2026-10-14:** `ruff check .` and `mypy src` both pass (0 errors); unit + api + contract 1014 pass. `target-version = "py312"` set; best-effort `except: pass` spots and the wall-clock-date CLIs have per-file ruff ignores in `pyproject.toml`; `networkx`/`jsonschema` added to the mypy missing-stubs list. Mostly variable-name collisions fixed by renaming (`causal/diagnosis.py`, `toolsets/diagnosis.py`, `toolsets/semantic.py` `query_filters`); dead duplicate methods removed from `CatalogueBootstrap`.
- Business-state fast path (`api/business_state.py`, `services/business_state/formatter.py`) is NOT deleted: it still serves `/v1/business-state` from the snapshot. It is replaced in Phase 3 (call `run_v3_mission`, add `business_snapshot`), then deleted.
- `toolsets/{causal,experiments,knowledge,models}.py` are no longer reachable from the loop (not in the local-tool list). They were not on the delete list; decide in Phase 3 whether to delete them with their tests.
- `semantic/_port.py` and `toolsets/semantic.py` stay until Phase 3 (`_data.py` lift).
- ~~`business-state-cron` `PermissionError: /app/reports`~~ FIXED 2026-10-14: forecast score/backtest reports go to `SELERIC_REPORTS_DIR` (`/app/.data/reports`, volume `forecast_reports`; `paths.reports_dir()`).
- The `eval` replay/live Jenkins stages were removed; the live gate is `eval/regress/run_regression.py`, run by hand.

- [x] Untangle first — **done**:
  - `RequiredScope` now comes from `agent/scope_model.py` (docstring trimmed); `agent/scope.py` deleted.
  - `agent/model.py` stub is `TestModel(call_tools=[])`; `toolsets/knowledge.py` lost the Jev relevance call; `toolsets/semantic.py::_scoped_to_named_values` removed (it only read `required_scope`, always empty now).
  - `strip_markdown_tables` + a char-based `is_table_delimiter` moved into `agent/answer_text.py` (no regex); `api/conversations.py` points there.
  - `toolsets/derive.py` and the old `evals/` dropped.
  - **Pulled forward from Phase 3:** `agent/forecast_plan.py::compile_forecast_plan(targets, horizon_days, entity_dimension, entity_values, ...)` no longer takes an `Understanding`; `toolsets/forecasting.py` passes the tool arguments straight in (forecast tests rewritten for it).
- [x] Delete packages — **done**: `pipeline/ plan/ spec/ understand/ answer/ investigate/ context/ graph/ derive/ ml/ utils/ evals/ adapters/`, `contracts/pipeline/` (v2 contracts), `agent/context.py` (old `compact_history`).
- [x] Delete modules — **done**: `agent/{understand,query_spec,plan,executor,intent,scope,repeat_guard,instructions,agent}.py`, `agent/validation/`, `services/{insights,claim_gate,evidence}.py`, `observability/{_stages,langsmith_eval}.py`, `toolsets/{ads,actions,derive}.py`, `config/llm_roles.py`, `forecasting/aggregate.py`, `api/missions.py`, `checkpointing.py` (+ wiring in `bootstrap.py`, `runtime.py`, `main.py`, `recovery.py`), `config/{brand_prefs,context_providers,llm_roles,causal_graph}.yaml`, `config/prompts/`.
- [x] Approvals / write actions — **done**: `/v1/approvals*` and `/v1/admin/approvals/expire` removed from `api/phase7.py` (search, run diagnostics, capabilities, replay metadata stay); `conversations/phase7.py` keeps only search + embedder; `ApprovalRequest`/`ApprovalDecisionEvent`/`RollbackRecord`/`ApprovalStatus` and the `approvals` repository removed from contracts, memory, postgres and file store; `actions_*` tools removed from the MCP gateway and transport; `ALLOW_WRITE_ACTIONS`, Jev (`JevConfig`, `JEV_*`) and the v2/checkpoint/stage-tracing settings removed. `MessagePartType.APPROVAL` is kept (office-ui renders it). **Not done:** office-ui `api/phase7.ts::decideApproval` / `ApprovalCard.tsx` (another editor owns office-ui).
- [x] Tests — **done**: old-package test dirs deleted; 45 mixed/old test files pruned or deleted (a script removed only the tests that touched deleted code); forecast plan/horizon tests rewritten. Unit + api + contract: **1013 pass, 0 fail**.
- [x] `pyproject.toml` — **done**: `langgraph`, `langgraph-checkpoint-postgres`, `import-linter`, the `[tool.importlinter]` block, stale ruff ignores and the langgraph filterwarning removed; `uv lock` run (drops langgraph/langchain/langsmith/orjson/requests/rich/..., adds `jsonschema`). CI: removed the `lint-imports` and golden-dataset steps; `Jenkinsfile.eval` is now static gates + unit tests (the live eval is `eval/regress/run_regression.py`); `make eval` runs the 6.
- [x] Old eval assets deleted: `eval/{golden,baselines,cassettes,datasets,suites,verified,reports}`, `scripts/{eval_run,replay_mission,seed_verified_examples}.py`.
- [x] Root clutter deleted: `fix_*.py`, `add_indexes.py`, `rename_cube.py`, `dump_thread.py`, `test*.py`, `test_db.py`, `*.log.err`, `*_log.txt`, `rerun*.output.txt`, `recent_api_logs.txt`, root `__pycache__`.
- [x] Migration `migrations/015_drop_approvals_and_checkpoints.sql` — written. The approval and checkpoint tables had 0 rows, so applying it on deploy loses nothing.
- [x] `plans/`, `reports/`, `postman/`, root `contracts/`: user said keep all for now.
- [x] **Clean-slate wipe — DONE 2026-10-14 on the user's go-ahead** (wipe now, MinIO + traces too, keep plans/reports/postman/contracts). Backups in `~/work/backups/slim-20261014/`: `seleric_swarm.dump`, `stage_traces.tgz`, `minio_seleric_attachments.tgz` (the bucket was empty). Postgres: all conversation/mission tables truncated (`schema_migrations` kept), migration 015 applied on deploy, `stage_traces` volume removed from compose and deleted. Row counts before: `evidence_artifacts` 126,857; `message_parts` 94,614; `v3_artifacts` 115,034; `run_events` 19,034; `missions` 1,382; `messages` 1,822; `runs` 911 (+`run_attempts` 1,005, `submission_outbox` 911); `mission_events` 4,446; `threads` 667; `thread_summaries` 839; `message_feedback` 6; everything else 0. DB is 497 MB; `pg_dump` taken. MinIO bucket `seleric-attachments` + volume `stage_traces` not yet backed up or wiped.
- [x] Gates: pytest green (1013). ruff 285 -> 127 and mypy 272 -> 235 errors (both were already red at HEAD; mostly old code in `causal/diagnosis.py`, `toolsets/diagnosis.py`, `forecasting/*`; the new loop files have ~15 mypy items, see Phase 5). **Deployed** (`rollback-20261014e` = the build before Phase 2): `api`, `recovery`, `business-state-cron`. The api build was blocked by an office-ui `tsc` error in another editor's WIP (`stores/conversation.ts` unused `useShellStore` import), so the image was built from a scratch copy of the tree with that one line removed; office-ui in the repo is untouched and will fail `docker compose build` until that editor finishes. `voice` NOT rebuilt (still the old image; its code path does not use the deleted modules).

### Phase 3: Untangle and slim the kept tools (1–2 sessions)

- [ ] Create `toolsets/_data.py`. Lift the shared helpers worth keeping out of `toolsets/semantic.py`:
  - `_cached_metrics_query`, `_mcp_error_result`
  - `_series_stats`, additive totals via `CatalogueSnapshot.aggregation_for`
  - `_breakdown_subtotals`
  - the partial-day helpers `_same_hours_applies` / `_in_progress_note`
  - `_rank_large_breakdown`, `_suggest_close_values`
  Then delete `toolsets/semantic.py` (2990 lines), `semantic/_port.py` and the leftover parts of `semantic/` that nothing uses.
- [ ] Point diagnosis, composition and exploration at `_data.py`. Remove the `required_scope.segment_hints` and `resolved_window` reads; the tool arguments carry the scope.
- [ ] Forecasting:
  - `forecast_metrics(targets=[metric ids], horizon_days, entity…)` builds the forecast plan directly, without the fake `Understanding` / `MetricSlot`.
  - Shrink `agent/forecast_plan.py` and drop its imports of `agent.plan`. Keep `forecasting/pipeline.py::run_forecast` and Chronos.
- [ ] Charts:
  - `show_chart(evidence_ids, chart_type?, title?)` and `show_table(evidence_ids)` wrap `analytics/visualization.py::generate_visualization_spec`.
  - Auto-render: when the model requested nothing and the main result has 2 or more rows, build one chart (trend → line, breakdown → bar) and one TABLE part.
  - Delete `generate_visualization`'s older paths.
- [ ] `api/business_state.py`: call `run_v3_mission`. The model uses `business_snapshot` if it wants it. Delete `formatter.format_business_state`'s LLM call if nothing else uses it.
- [ ] Slim `SelericDeps` down to the fields the loop and tools use: drop `query_spec`, `required_scope`, `resolved_window` and `jev`.
- [ ] Gates: unit tests green, then golden diagnosis, forecast and exploration questions re-run live and ClickHouse-checked. Deploy.



### Phase 4: Move domain rules to Core MCP (1 session; needs user approval)

- [ ] Do this in a worktree of `/opt/seleric/Seleric_Agent_Core`, which is jenkins-owned.
  - Rewrite `src/seleric_mcp/gateway/prompts.py::NO_HALLUCINATION_GUARD`. Remove the stale retired ids in §3g–3i (`:206-268`), such as total_sales_all_channels, meta_spend and net_profit_all_channels. Map them through `catalogue_v2/deprecations.yaml`.
  - Add the data rules from test_agent's PREAMBLE:
    - Meta = `ad_platform` filter;
    - default brand;
    - order-date by default, with Finance `pnl_*` only when asked;
    - granularity none for totals;
    - never sum ratios;
    - the supported-dimension caveat.
  - Fix the `metrics_query` docstring (`server.py:903`), which also names retired ids.
- [ ] Run `uv run pytest -m "not live"` in Core and **show the user the diff, then wait for approval** before pushing main. Jenkins then redeploys.
- [ ] In Seleric_Agent, include the server instructions in the prompt and delete `prompts/data_rules.md`. Re-run the 6 and golden 30.



### Phase 5: Latency and quality to the bar (1–2 sessions)

- [ ] **Real token streaming of the final step.** Use `HealthGatedChatModel.request_stream` for the answer step and keep fallback before the first token. Today `PatientModel` replays a non-streamed response.
- [ ] **Prompt-cache hygiene.** Keep the prefix byte-stable: tool defs in sorted order, catalogue index cached per catalogue version, calendar last. Check `cached_tokens` in the trace.
- [ ] **Tune.** `MAX_STEPS`, `MAX_TOOL_CALLS`, `MAX_RESULT_CHARS`, `HISTORY_TURNS`, `LLM_REQUEST_CAP_S`, and the reasoning budget, all from eval data.
- [ ] **Metering and observability.** Record per-mission tokens and cached tokens in `trace` and Prometheus, and keep Langfuse spans per LLM and tool step.
- [ ] **Full gate.** Golden 30 all checks pass, the 6 at or above test_agent's rates, and replay11 clean on grounding with its figures spot-checked in ClickHouse. Latency p50 ≤ 25s and p90 ≤ 45s, live, measured at concurrency 1 and 4.
- [ ] Rewrite `docs/CURRENT_ARCHITECTURE.md` to describe the loop, and archive the `OPEN_GAPS_*` docs that no longer apply. Update memory.



## Critical files

- **Rewrite:**
  - `src/seleric_swarm/agent/runner.py` (2066 lines, target about 250)
  - `agent/model.py` (add `EffortModel`, drop planner and role resolvers)
  - `agent/dependencies.py`
  - `protocols/mcp/servers/seleric_remote.py` and `protocols/mcp/gateway.py` (add `list_tools`)
- **New:** `agent/loop.py`, `agent/mcp_toolset.py`, `agent/local_tools.py`, `agent/grounding.py`, `agent/prompt.py`, `toolsets/_data.py`, `eval/regress/`*
- **Reuse unchanged:**
  - `agent/progress.py`, `agent/model_health.py`, `agent/artifacts.py`, `agent/output.py`
  - `api/office/v3_adapter.py`, `api/v3_state.py`, `contracts/lookup.py`
  - `conversations/*` (`ContextBuilder`, `_answer_parts`, the stream path)
  - `services/catalogue_bootstrap.py`, `services/elapsed.py`, `services/time_range.py`
  - `causal/*`, `exploration/*`, `forecasting/*`, `analytics/visualization.py`, `services/domain_health/*`, `cancellation.py`
- **Reference:** `/opt/seleric/test_agent/app.py`: `MCP`, `compact`, `flag_partial`, `filter_misuse`, `validate`, `grounding`, `problems`, `calendar`, `PREAMBLE`, `run_turn`. Also `eval.py` `CHECKS`.



## Verification (per phase, and in full at Phase 5)

1. **Static and tests.** `make lint typecheck test` (ruff, mypy, pytest) green. Unit tests cover the loop budget and forced final, grounding cases, MCP validation and misuse, escalate, and the runner return contract.
2. **Live golden.** Run `python3 eval/regress/run_regression.py eval/regress/regression.json <out> 1` against :8090. It uses `contains_sql` truth through ClickHouse, so numbers are cross-checked live. Run concurrency 4 once per phase for 429 behaviour.
3. **Live 6.** Run `run_regression.py eval/regress/test_agent6.json`. Compare its checks, groundedness and latency with `test_agent/eval_results/gpt5mini_low.json`. The same questions can also be run in test_agent itself (`python eval.py --reps 1 --label ref-<date>`) to get a same-day reference.
4. **UI smoke.** Through office-ui: new thread, streamed answer, progress stepper, a chart and table, a follow-up drilldown, cancel mid-run, an attachment, and memory consent. Voice token issue.
5. **Latency.** p50/p90 wall time per mission, from `runs` timestamps and the trace `stages_ms`.



## Risks and notes

- **Concurrent editors.** Other Claude sessions or teammates may build from the same live directory, and there is a stash `other-track-wip` on `gaurav`. Check at session start. A big deletion commit can clash with their work.
- **Gaps the explorations found.**
  - `MCPGateway` has no tool schemas, so Phase 1 adds `list_tools`.
  - Nothing checks cancellation inside the loop, so the loop adds that check.
  - SOURCE rows, charts and the turn record all depend on evidence artifacts, so the MCP toolset must write them.
- **Interim regression risk.** Phase 1 swaps the engine on live with no flag. The rollback tag is the safety net. Run the golden set right after deploying.
- **Uncommitted work.** Large deletions stay uncommitted until the user asks for a commit, and a stray `git checkout` or `git stash` by another session could lose them. Mitigations: back up the diff before each deletion batch, ask the user for a commit at each phase gate, and post in AGENT_CHAT what is uncommitted.
- **Moving reference.** test_agent's grounding moved from a pair-scan to row-bound checks during planning. Re-sync every session (protocol step 3).



## Status board


| Phase / task                                                                                               | Owner | State                                                          | Date       |
| ---------------------------------------------------------------------------------------------------------- | ----- | -------------------------------------------------------------- | ---------- |
| P0 plan saved + sha recorded                                                                               | SL    | done                                                           | 2026-10-13 |
| P0 superseded headers + AGENT_CHAT freeze notice                                                           | SL    | done                                                           | 2026-10-13 |
| P0 golden harness moved to `eval/regress/` (runner extended: sessions, tool checks, Q1 reference, p50/p90) | SL    | done                                                           | 2026-10-13 |
| P0 `test_agent6.json` + `agent6_checks.py`                                                                 | SL    | done                                                           | 2026-10-13 |
| P0 `replay11.json`                                                                                         | SL    | done                                                           | 2026-10-13 |
| P0 baseline run (golden 30, the 6, replay11)                                                               | SL    | PARTIAL: stopped early by user (Q1-Q4 of the 6 FAILED on live) | 2026-10-13 |
| P1 build the loop and swap it in                                                                           | SL    | DEPLOYED (rollback-20261014d); gate OPEN: 6 at 5/6, p50 61s / p90 84s; golden 30 + replay11 not run | 2026-10-14 |
| P2 delete old pipeline, wipe data                                                                          | SL    | COMPLETE with carry-overs (see Phase 2 section): deployed, data wiped, ruff + mypy green, tests 1014 pass; 6 live 4/6; uncommitted | 2026-10-14 |
| P3 untangle and slim kept tools                                                                            |       | not started                                                    |            |
| P4 domain rules to Core MCP (needs user approval)                                                          |       | not started                                                    |            |
| P5 latency and quality to the bar                                                                          |       | not started                                                    |            |




## Progress log



### 2026-10-13 (session 1, Phase 0)

- **Reference shas (test_agent):** `app.py` `422fc64e03ba2cd4db2e3a6019e80c92defa195fdb000b248d332dbd468d044b` (903 lines); `eval.py` `f45fbe44d4057779d54b9d5abfda550f5509d97ab7c81f416441c94b5c83b036` (CHECKS ported from this version); `test_grounding.py` `006d46a3a7609bbab6ac7a3e1c191a0580e386e840e216def92cc63abf9ed4e5`; `replay.py` `1e996b15697f840a50c7acb0b0e592b77d09f4a1f4529cab4d4677d428799433` (SESSION copied into `replay11.json`).
- **The port table above is STALE:** `app.py` grew from 865 to 903 lines after the snapshot (`6b3c195e…`). Owed at the start of Phase 1: diff `grounding`, `problems`, `flag_partial`, `filter_misuse`, `compact`/`_shrink`, PREAMBLE and `run_turn`, and re-port `test_grounding.py` cases.
- **Harness:** `eval/regress/{run_regression.py, regression.json, ch.sh, mcp_direct.py, agent6_checks.py, test_agent6.json, replay11.json}`. Usage: `python3 eval/regress/run_regression.py <items.json> <out_dir> [concurrency] [only=1,2]`. Items with the same `session` run in one thread. `summary.json` reports `checked_items` / `checked_pass` / `unchecked_items` (a `None` check is n/a, never a pass), wall p50/p90, revisions, retries.
- **Finding 1, golden 30 is weakly checked:** only 7 of 30 items in `regression.json` carry checks (the other scratchpad variants have 6–8). "Golden 30 pass" therefore mostly means "completed". Treat `checked_pass` as the real number, and add `contains_sql` checks for the unchecked items before the Phase 1 gate is trusted (open task, below).
- **Finding 2, tool checks cannot be scored on the current system:** since 2026-10-11 (v2 enforce) `missions.result_json.trace.steps` is null and run_events carry no tool args. The 6-question tool checks are `ok: null` (n/a) on the baseline; the baseline for the 6 is latency plus the Q1 reference only. **The Phase 1 tool-check gate compares against** `test_agent/eval_results/gpt5mini_low.json`**, not against this baseline.**
- **Finding 3, contract for the new loop's** `trace.steps`**:** emit `{"kind":"tool_call","tool","args":<dict>}` and `{"kind":"tool_return","tool","ok":<bool>}`. `tool_calls()` in the runner honours an explicit `ok`.
- **Limitation:** `numbers()` in the runner parses plain digits with commas only (answers currently print `INR 179,674`); K / lakh / crore suffixes are not handled.
- **Open Phase 0 task:** add `contains_sql` checks to the unchecked golden items (needs ClickHouse truth queries; do with the user's review of each).
- **Baseline (partial, stopped by the user to start the rebuild):** live v2-enforce system, the 6 at concurrency 1: Q1-Q4 all `failed` in 34-42s with `v2 pipeline could not answer` (`scope_uncovered:metric:net_sales`, `intent_lost:dimension:publisher_platform`, `no_template`). Q5/Q6, golden 30 and replay11 not run. Results in `eval/regress/results/baseline-20261013/`.



### 2026-10-13 (session 1, Phase 1)

- **Built and deployed** (images `seleric_agent-{api,recovery,business-state-cron}`, rollback tag `rollback-20261013a`): `agent/loop.py` (one PydanticAI run, budget hook, cancel check, one grounding correction pass, degenerate guard), `agent/mcp_toolset.py` (tools from `tools/list`, jsonschema validate, filter_misuse, flag_partial/flag_composed, per-turn dedup, compact, evidence writes), `agent/grounding.py` (test_agent grounding v3 with a character-scan number reader, no regex), `agent/local_tools.py`, `agent/prompt.py`, `agent/model.py` (`resolve_loop_model`, `EffortModel`), `agent/runner.py` rewritten (2066 -> ~400 lines, same `run_v3_mission` contract), `list_tools`/`server_instructions` on the MCP transport + gateway (`insights_explain` added to the gateway tool list), `jsonschema` added to `pyproject.toml`/`uv.lock`.
- **Deviations from the plan:** (1) number scanner is a new character scan inside `grounding.py` (needs positions for dates/units), not `validation.scan_numbers`; `validation/` is untouched until Phase 2. (2) The data rules live as a constant in `prompt.py`, not `prompts/data_rules.md` (`prompts/` is not copied into the image). (3) Local tools `run_python` / `show_chart` take `query_ids`, not evidence ids (the model never sees evidence ids). (4) Prior-turn figures are stored as a `turn_payloads` artifact; the thread's earlier queries as `turn_record.queries`, shown to the model as a dynamic instruction.
- **Escalation:** effort holder is a shared dict in a ContextVar (tools run in child tasks, a plain `ContextVar.set` in a tool would never reach the model request); `diagnose_metric_change` raises the effort automatically, `escalate` does it on request; the prompt makes `escalate` -> `diagnose_metric_change` (DoWhy) -> drilldowns the path for cause questions.
- **Tests:** `tests/unit/agent_loop/` 26 pass (17 ported grounding cases + loop: tool call/evidence, dedup, invalid args, correction pass, unverified flag, degenerate, budget withdrawal, cancel, escalation across tasks). Deleted tests of removed runner code (`test_runner_jev_routing`, `test_value_resolution`, `test_value_fragments`, `test_followup_period`, and 21 individual tests). Unit suite: 1640 pass, 10 fail; the 10 are older failures in v2 planner/spec/evals/llm_roles (deleted in Phase 2).
- **In-process smoke (real model, live MCP):** "net sales yesterday vs day before" 12.7s, 3 LLM calls, grounding 9/9; "Why did my sales drop yesterday?" 35s, correctly says sales ROSE (Q1 premise check); chart question 18.6s but the model claimed a chart without calling `show_chart` -> prompt rule added.
- **Note for the user:** `.env` `AZURE_OPENAI_MODELS` ends in Llama-4, which cannot do native tool calls on Azure; it is only reached after gpt-5-mini and gpt-4o-code both fail. Proposal: `["gpt-5-mini","gpt-4o-code"]`. `.env` left untouched.
- **Phase 1 first live pass (the 6, concurrency 1, rollback-20261013a build):** 6/6 runs COMPLETED (old system: Q1-Q4 FAILED). Wall p50 80s / p90 97s (bar 25s / 45s): Q3 34s, Q6 30s, but the diagnosis/comparison questions Q1 89s, Q4 88s, Q5 105s. Q1 answer correct and grounded (says sales ROSE vs the day before; the DoWhy reference window shows -59.8% vs the usual same-weekday level; ig_feed localised). Tool checks 3/6, the 3 failures were my check logic (diagnose_metric_change counts as compare+decompose: fixed) and a grounding false positive (see next).
- **Grounding false positive fixed:** campaign codes (`TH-383-SUSPENDER`, written by the model also with U+2011 hyphens) were read as figures and flagged "not verified" on Q2/Q5. The scanner now treats a digit run glued to a hyphenated name as an identifier; tests added (28 in `tests/unit/agent_loop`).
- **Latency profile (rollback-20261013b build, Q1 106s):** 4 LLM steps = 40s total (6.6s, 10.5s, 5.6s, 17.1s final with 48k prompt tokens); `diagnose_metric_change` = 57s of the rest. In-process the same call is 26s: it issues **182 single-metric** `metrics_query` **calls** (74s cumulative) fetching 57 days of daily series per candidate metric. **Phase 5 lever #1:** batch the diagnosis's candidate fetches by view (one `metrics_query` with many measures per view/filter set, split into the per-metric cache entries `_cached_metrics_query` already uses) and find why the container is 2x slower than in-process. Lever #2: the final step's prompt grows to 48k tokens (diagnose result) - compact the diagnosis result.
- Trace now carries `stats.llm_steps` (per LLM call: ms, tokens, cached, tool calls) and local tool `ms`.



### 2026-10-13 (session 1, fixes round after the first live pass; rollback-20261013c)

- **Fixes:** golden Q6-style broad requests default to headline metrics instead of asking (prompt); every local tool (`run_python`, `show_chart`, `business_snapshot`, `escalate`, plus the helpers) is now traced in `trace.steps` with `ms`; grounding scanner treats codes inside hyphenated names (`TH-383-...`, any Unicode hyphen) as identifiers.
- **Diagnosis query batching (**`toolsets/diagnosis.py::_prefetch_daily/_fetch_all`**):** one multi-measure `metrics_query` per (catalogue view, window, scope, dimension) fills the per-metric cache keys, composed (multi-time-axis) results are split by part, a failing group falls back to single queries, cached metrics are never refetched. In-process profile: 182 -> 109 MCP calls, 29s -> 23s; remaining time is mostly the per-dimension segment queries (~16) and metrics the server will not combine (`order_pnl`). Values verified identical to single-measure queries for 49 metrics across 4 views. One side effect: among interchangeable equal-valued series the bridge may now name a different synonym (e.g. `discounts` vs `order_pnl_discounts`); unbatched runs are deterministic. 4 unit tests (`tests/unit/agent_loop/test_diag_batch.py`); the 98 existing diagnosis/causal tests pass.
- **Env (**`.env`**, backups in** `~/work/backups/slim-20261013/`**):** `AZURE_OPENAI_MODELS` -> `["gpt-5-mini","gpt-4o-code"]` (Llama-4 removed from the loop chain; `AZURE_OPENAI_HELPER_MODEL` stays Llama for short helper calls); removed `UNDERSTAND_V2_MODE`, `PLANNER_V2_MODE`, `ANSWER_V2_MODE`, `V2_NO_FALLBACK`, `CLARIFY_MODE`, `AZURE_OPENAI_{PLANNER_MODEL,PLANNER_REASONING_EFFORT,TOOL_MODEL,TOOL_TEMPERATURE,ANSWER_MODEL,ANSWER_REASONING_EFFORT,ANSWER_DIAGNOSIS_REASONING_EFFORT,STRONG_REASONING_EFFORT,FAST_MODEL}`, `AGENT_LLM_UNDERSTAND_{CAP,TIMEOUT}_S`, `AGENT_RETRIES`; added `AGENT_REASONING_EFFORT=low`, `AGENT_ESCALATED_EFFORT=medium`. `docker-compose.yml`: 46 matching pass-through lines removed. JEV_* kept (external service, still used by the knowledge/action tools until Phase 2 decides).
- **test_agent env/format comparison:** test_agent `.env` has `AZURE_OPENAI_DEPLOYMENT` (default model), `AZURE_OPENAI_MODELS` comma-separated (`Llama-4...,gpt-5-mini`; Seleric reads a JSON array or comma list), `MCP_URL`/`MCP_TOKEN` (Seleric: `SELERIC_MCP_URL`/`SELERIC_MCP_TOKEN`), same endpoint and API version. test_agent's other knobs are env-overridable code defaults, not in its `.env`: `REASONING_EFFORT=low`, `REASONING_BUDGET=6000`, `MAX_TOKENS=2000`, `MAX_STEPS=10`, `MAX_TOOL_CALLS=20`, `TURN_TIMEOUT_S=180`, `TOOL_TIMEOUT_S=45`, `HISTORY_TURNS=6`, `MAX_ROWS=60`, `MAX_RESULT_CHARS=14000`, `KEEP_PAYLOADS=8`, `VERIFY_NUMBERS=1`, `INCLUDE_SERVER_GUIDE=0`; the loop reads the same defaults as `AGENT_MAX_TOKENS/_REASONING_BUDGET/_REASONING_EFFORT/_MAX_STEPS/_MAX_TOOL_CALLS/_TURN_BUDGET_S/_TOOL_TIMEOUT_S/_MAX_ROWS/_MAX_RESULT_CHARS` (turn budget is soft 150s, tools withdrawn). Result formats: test_agent returns `grounding = {numbers, measured, derived[], unmatched[], misbound[], placeholders[]}`; the loop stores the same dict plus `problems[]` in `trace.grounding`.
- **Removed as unwanted:** dead model resolvers (`resolve_v3_model`, `resolve_planner_model`, `resolve_role_model`, `resolve_mission_model`, `RoleSplitModel`, `_led_by`) and 5 tests of them. Unit suite: 1641 pass, 10 fail (the same older v2 planner/spec/eval/llm_roles failures).



### 2026-10-13 (session 1, quality review + product-table failure; rollback-20261013e / f)

- **User-reported failure ("gross sale and ad spend by product this month, excluding exchanges"):** Seleric answered with grand totals, no per-product rows (29s, 6 LLM calls, `revisions: 1`). Cause chain, reproduced in-process: (1) the first draft WAS the correct per-product table; (2) the grounding scanner flagged `500` from the product name "Cleaner with Paw Cleanser 500ml" (digits glued to a name suffix); (3) the ported corrective text ("if you added up rows, query the period total instead") steered the model to drop the table for totals; (4) nothing checked that the rewrite lost the content. **Fixes (deployed):** digits glued to non-unit letters are part of a name (`500ml`, `4XL`, `26SEP`); corrective text now says fix only the flagged figures and keep every table/row/breakdown; `lost_substance()` guard keeps the draft (plus the "Not verified" note) when a correction cuts its verified figures below 60%; prompt: offer only what the tools can do (it still offered a CSV once - prompt compliance is imperfect). Re-run live: 30s, per-product table, 20/20 grounded.
- **Biggest latency/cost finding:** a helper tool's whole `ToolResult` was serialized to the model. `diagnose_metric_change` returns 82k chars (summary 5.8k + `provenance.diagnosis` 65k + 158 artifact ids), re-sent on every later step. Q4 had used 491k prompt tokens and 9 LLM calls (steps of 50-96s, 301s wall). Local tools now return a `ToolView` (success, summary, warnings, error_code, evidence_rows). Q1 84k -> 33k prompt tokens, Q4 491k -> 65k, Q4 301s -> 80s. The gpt-5-mini token-per-minute limit (429s were visible) is also relieved. Remaining time in "why" questions: the diagnosis engine itself (~38s in the container vs ~23s in-process) + ~28-50s of LLM steps.
- **Answer-quality review against ClickHouse (Q1-Q4, build rollback-20261013c):** transcribed numbers all matched (Q2 per-campaign ad spend/net sales/waste exact; Q3 top products via the Cube SQL exact; Q4 net profit -30,856 -> -82,963 and ad spend 345,581 -> 395,584 exact). Quality gaps found: (a) Q4 never checked the user's premise: on order-P&L net sales / ad spend ROAS FELL 1.55 -> 1.31 (net sales 535.5k -> 518.4k) while the answer reasoned as if ROAS rose and said efficiency did not weaken; it declined to derive ROAS because `net_roas` is status=broken. (b) Q1 reports "+23.0%" for 34,377 -> 42,177 (true +22.7%): the grounding "derived" check accepted a coincidental ratio; its "usual 104,860" baseline (-59.8%) matches neither prior Mondays (mean 70k, median 51k) nor the previous 7 days (92k), and the real context (Oct 3-8 event surge, Oct 5 = 136k) is not named. (c) Q1 mixes bases (commerce 42,177 vs finance event-date "P&L net sales 8,468"); metric ids leak into prose. (d) Q2 defines waste as spend minus net sales (stated, defensible) and offers to "pause budgets" (no write tools). **Open:** derived-figure check too lenient; premise check for ratios; baseline transparency in the diagnosis; ids in prose.
- **test_agent vs the comparison question:** test_agent did not solve it unaided. Its first recorded eval (gpt5mini_low, Q5): "compares against last week" 0/3 (compared Oct 12-13 with the 2 days before). It reached 1/1 then 0.5 only after its prompt gained the "Compare like-for-like ... run the two explicit windows from the Calendar section" rule and the calendar block; even in `fixes2` Q5 is 0.5 on the window check and 0.5 on decomposition, and it compared a 7-day total with a 2-day total. Its median is 23s with ~3 LLM calls, so it iterates little.
- **Experiment (Q5 on this build, n=1 each, in-process, noisy): comparison rule + calendar windows removed vs kept, low vs medium effort.** generic/low: read "this week" as the last full week vs the week before (wrong window, 188s); generic/medium: asked a clarification and did nothing (52s); current/low and current/medium: correct like-for-like (this week so far vs last full week, per-day normalised, 145s / 177s). Higher effort alone did not resolve the period ambiguity; the calendar facts + one generic like-for-like rule did. Needs repeats (n>=5) before concluding anything finer.
- Unit suite: 1647 pass, the same 10 older v2 planner/spec/eval/llm_roles failures. `tests/unit/agent_loop`: 42 tests.


### 2026-10-14 — quality fixes, sandbox, Causal tab (SL) — DEPLOYED (rollback-20261014c, d), uncommitted
- Product-table failure: grounding scanner ignores digits glued to name suffixes (500ml), correction wording no longer pushes to totals, `lost_substance` keeps the draft if the rewrite drops verified content.
- `run_python` was failing on every call (16/16): the wrapper had dropped the contract. Restored the docstring, `query_id` on each evidence row, and sandbox errors now restate what exists (`evidence` list, no per-query variables). Live: model self-corrected on the 3rd try (2 of 3 calls still wasted — gpt-5-mini ignores the docstring; a candidate for exposing the rows differently).
- Causal tab empty for Q4: DoWhy found no drivers, so no DAG. `causal/graph_chart.py` now draws the exact accounting bridge/decomposition (kind `identity`, dashed) when no DAG was estimated; warning text says so. Tests added. UI legend still says dashed = confounder (office-ui owned by another editor: not touched).
- Open: trace `stats.errors` does not record `ok:false` run_python returns; 6-question set 5/6 (Q6 scope check), p50 61s p90 84s.


### 2026-10-14 (session 2, Phase 2)
- **Code side of Phase 2 is in the working tree** (see the Phase 2 checklist). 306 modules in the import graph; 90 were already unreachable from `main`/`recovery`/`bootstrap`/`runtime` once the runner was rewritten; the rest went through the untangling list. Net: `src` from 72k to 48k lines (see `git diff --stat`).
- **Mistake to avoid:** `ruff check --fix` over all of `src` and `tests` rewrote ~60 unrelated files (unused imports). I reverted the ones nobody had touched and re-applied five test files that carried earlier uncommitted work from `tracked.diff`. Run ruff `--fix` only on named files.
- **Unit + api + contract: 1013 pass.** The two causal-graph test failures from the earlier Causal-tab work were fixed in the tests (chart spec is not an `EvidenceArtifact`; `causal_graph` has no evidence-row builder).
- test_agent `app.py` sha now `5f7811e6fe14...` (was `422fc64e...`); the port table is still stale, to be diffed in the Phase 1 gate work.
- **Deployed Phase 2** (api/recovery/business-state-cron, built from a scratch copy because of the office-ui tsc error above). Smoke: `/readyz` ok, `/ui/` serves, threads/search endpoints 200, voice token issues, `/v1/approvals` is 404, `/v1/business-state` answers from the snapshot. The 6 live on the wiped DB: Q2, Q3, Q6 and Q1 (second run) pass; Q4 ("ROAS attempted") and Q5 ("compares against last week") fail their tool checks, the same two that flip between runs before Phase 2 (Phase 1 gate item); wall p50 90s / p90 140s on the first run right after the restart, Q1 63s and Q3 17s on the re-run. No Phase 2-specific regression seen; latency is still the Phase 5 problem.
- Nothing committed. `git diff --shortstat HEAD -- src`: 157 files, 657 insertions, 26,449 deletions.

### 2026-10-14 (session 2, ruff + mypy)
- `ruff check .` 285 -> 0 and `mypy src` 272 -> 0 (HEAD baseline -> now). Tests 1014 pass. Redeployed api/recovery/business-state-cron with a plain `docker compose build` (rollback tag `rollback-20261014f` = build before this lint pass). Live check: Q1 51s and Q3 30s pass their checks.
- Behaviour-affecting edits were renames only, plus: `forecasting/horizon.py` simplified `start = anchor`; `_finite` is a `TypeGuard[float]`; `CatalogueBootstrap` lost four unused copies of `CatalogueSnapshot` methods that referenced non-existent attributes.
- Still open: `business-state-cron` `PermissionError: /app/reports` (forecast score/backtest writes under `repo_root()/reports/forecasting`).

### 2026-10-14 (session 2, business state)
- **Reports crash fixed:** `forecasting/score.py` and `backtest.py` wrote to `<repo>/reports/forecasting`, not writable in the image. They now use `paths.reports_dir()` (`SELERIC_REPORTS_DIR`, set to `/app/.data/reports` on `business-state-cron` with a `forecast_reports` volume). No more tracebacks.
- **Domain snapshots were hiding everything:** 5 of 8 domains were UNAVAILABLE because one metric with `status=broken` in Core (net_roas, mer, cac, cpm/cpc/ctr, gross_roas, *_margin_pct, repeat_rate, product_gross_margin_pct, attributed_aov, cost_per_landing_page_view) made the whole domain UNAVAILABLE. `domain_health/resolver.py` now marks a domain DEGRADED when some metrics answer and UNAVAILABLE only when none do (2 tests added). Live: commerce, funnel, operations OK; finance, performance, attribution, product, customer DEGRADED; `business` DEGRADED.
- **Still needs Core (Phase 4):** the broken metrics above stay "No data available" in `/v1/business-state`; `config/domain_health_profiles.yaml` ids are registry ids (`metric.spend` -> catalogue `ad_spend`), left unchanged. `refund_spike` shows +2252% on `refunded_amount_excl_tax` (check whether that figure is real or a partial-day artefact).
