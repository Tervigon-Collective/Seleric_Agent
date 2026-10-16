# Seleric Agent — Current Architecture (single loop)

**As of 2026-10-14.** One PydanticAI agent runs one loop per turn over the Core MCP tools and a few local analysis
helpers. There is no understand / spec / plan stage, no validated-revision wrapper and no write actions. The previous
V3 architecture document is kept in `docs/archive/CURRENT_ARCHITECTURE_V3_2026-09-28.md`; the rebuild plan and its
progress log are in `plans/SLIM_LOOP_PLAN.md`.

## 1. Request path

```
office-ui / voice / cron / business-state ─► api (conversations, office, business_state, async_missions)
        │  durable runs: lease + heartbeat queue (api/async_missions.py), executed by the `recovery` container
        ▼
agent/runner.py::run_v3_mission      context (history, memories, earlier queries + figures) → loop → persist
        ▼
agent/loop.py::run_turn              one Agent run + one grounding correction pass
        ▼
tools: agent/mcp_toolset.py (Core MCP read tools)   agent/local_tools.py (helpers)
        ▼
protocols/mcp  ─►  Seleric_Agent_Core :8765  ─►  Cube v2  ─►  ClickHouse
```

Missions execute in the `recovery` container, not in `api`: after an agent change rebuild and restart
`api recovery business-state-cron` together.

## 2. The loop (`agent/loop.py`)

- `Agent.run` with parallel tool calls, an event handler for progress (`agent.stage`, `tool_started`,
  `tool_completed`, `answering`), cancellation checked before every model request.
- Budgets: `AGENT_MAX_STEPS` (10), `AGENT_MAX_TOOL_CALLS` (20), `AGENT_TURN_BUDGET_S` (150, soft). When one is
  exhausted a `PrepareTools` hook withdraws all tools and the model is told to answer from the evidence it has.
- Degenerate output (content filter, repetition, `length` without a tool call) is retried once; a second time the
  user gets a fixed "couldn't produce a reliable answer" text, never the junk.
- Grounding (`agent/grounding.py`): every figure in the answer must be in a tool result of this turn or the thread's
  last 8 payloads, or follow from them by one arithmetic step on figures that belong together. Findings (unmatched,
  misbound to another row, placeholders) go back for ONE corrective pass; `lost_substance` keeps the draft if the
  rewrite drops verified content. Anything still failing ships with a visible "Not verified against the data" note,
  also written to `limitations` and `trace.grounding`.
- Answer cleaning (`agent/answer_text.py`): metric ids replaced by names, tables realigned, decimals rounded.
- `agent/auto_chart.py` attaches one chart of the main result when the model made none.

## 3. Tools

| Group | Where | Notes |
|---|---|---|
| Core MCP read tools | `agent/mcp_toolset.py` | Schemas from `tools/list` at boot, read-only allowlist (no `actions_*`, no `semantic_sql`), jsonschema validation, `filter_misuse`, `flag_partial` / `flag_composed`, per-turn dedup, `compact`, evidence artifacts per `query_id` |
| `diagnose_metric_change` | `toolsets/diagnosis.py`, `causal/*` | Exact decomposition, segment localisation, DoWhy driver estimates in a forkserver pool (`causal/estimate_pool.py`) |
| `break_down_metric` | `toolsets/composition.py` | P&L / waterfall from the catalogue's verified composition |
| `forecast_metrics` | `toolsets/forecasting.py`, `forecasting/*` | Chronos service |
| `explore_data` | `toolsets/exploration.py` | What is unusual |
| `run_python`, `show_chart`, `show_table` | `toolsets/sandbox.py`, `analytics.py` | Take `query_id`s of fetched results |
| `business_snapshot` | `agent/local_tools.py` | Latest hourly snapshot when fresh |
| `escalate` | `agent/local_tools.py` | Raises reasoning effort; the diagnosis and breakdown helpers also raise it |

Local tools return a slim `ToolView` (summary, warnings, error code) to the model; the bulky structured result stays
server-side. A failed helper call (`ok:false`) is recorded in `trace.stats.errors` like an MCP failure.

## 4. Prompt and model

- `agent/prompt.py`: working rules + answer rules, the Core MCP server instructions (data rules live in Core:
  `Seleric_Agent_Core/.../gateway/prompts.py`), a compact catalogue index, and the calendar last so the prefix is
  byte-stable.
- `agent/model.py`: `resolve_loop_model` builds HealthGated → Fallback → Patient over `AZURE_OPENAI_MODELS`
  (`gpt-5-mini`, then `gpt-4o-code`). `EffortModel` reads a per-mission effort holder: `AGENT_REASONING_EFFORT` (low)
  by default, `AGENT_ESCALATED_EFFORT` (medium) after `escalate`, `AGENT_WRITEUP_EFFORT` (low) after the diagnosis /
  breakdown helper returns.

## 5. Persistence and surfaces (unchanged contracts)

Threads / runs / messages / message parts, memories and attachments, mission events timeline, `v3_artifacts` (evidence,
`turn_payloads` per turn for follow-up grounding), `TurnRecord` with the query digest, Langfuse tracing, metering,
voice, `/v1/business-state` (runs the loop with `business_snapshot`), the business-state cron.
`trace.steps` holds every tool call / return (with `ms`, `phase_ms` for diagnosis); `trace.stats.llm_steps` holds per
step ms, tokens, cached and reasoning tokens, effort; `trace.grounding` the grounding result.
There is no Prometheus exporter in this repo.

## 6. Evaluation

`eval/regress/run_regression.py <items.json> <out_dir> [concurrency] [only=…]` drives the real API: wall time, LLM and
tool counts, grounding, answer checks (`contains_sql` against ClickHouse truth, `window_reference` against direct MCP
totals) and p50/p90. Sets: `test_agent6.json`, `replay11.json` (multi-turn), `regression.json` (golden 30, 7 items
carry checks). Run the 6 plus the golden items relevant to a change; do not run the whole golden 30.

## 7. Known open items

See the progress log in `plans/SLIM_LOOP_PLAN.md`: latency above the p50 25s / p90 45s bar, real token streaming of the
last step, tuning of budgets, a decision on Prometheus, office-ui leftovers for the removed approvals endpoints.

## 8. Diagnosis investigations (`diagnosis/`, since 2026-10-16)

`diagnose_metric_change` is a wrapper: it opens an *investigation* (`inv_<12 hex>`), runs the unchanged analysis
(`toolsets/diagnosis.py::_diagnose`) and closes it. While it runs:

- **Trace.** Ordered events (`investigation_started`, `window_resolved`, `graph_loaded`, `exclusion`/`graph_issue`,
  `operator_started/finished/failed`, `query_issued`, `hypothesis_resolved`, `refuter_result`, `unexplained_share_updated`,
  `investigation_finished`) go through `agent/progress.py::emit_progress` as `diag.<kind>`, so they are stored in
  `run_events` and streamed over SSE like any other mission event. The whole envelope travels under the one payload key
  `investigation` (`conversations/privacy.py` allows only that key); each payload is capped at 4 KB. About 240 events per
  investigation.
- **Graph.** The reviewed metric dependency graph (`causal/data/dependency_graph.json`, generated in Agent Core) is loaded
  and checked against the live catalogue definitions (aggregation, unit, grain, view); metrics that are missing or have
  drifted are reported as `exclusion` events. It does not yet choose the candidate causes (Phase 3 of the rebuild).
- **Correlation.** The id is bound on log records (`LogRecord.investigation_id`) and structlog context, set on the active
  tracing span, and returned on the tool result (`provenance.source_metadata.investigation_id`).
- **Code layout.** `seleric_swarm/diagnosis/` is strictly typed and layered (`domain` < `ports`, `libs` < core < `adapters`,
  `cli`); the layering, determinism (no clock or global randomness in the core) and no-catalogue-names rules are enforced by
  `tests/unit/diagnosis/test_import_contract.py`. The anomaly primitives shared by diagnosis, business state, forecasting
  quality and exploration live in `diagnosis/detect/`. The engine itself (formerly the 2,917-line `causal/diagnosis.py`, now
  a re-export shim) is in `domain/` (lineage, report, input), `detect/` (calendar fit, seasonal profile, window values),
  `operators/` (the four stages `is_real`, `what`, `where`, `why_event`, run in that order by `operators/pipeline.py`, also
  registered operators in `operators/engine_ops.py`) and `report/` (headline, narrative, assembly). Thresholds come from
  `DiagnosisConfig`; the driver estimator and value formatting are ports. The analysis tool's output is unchanged.
- **Record and replay.** `diagnosis/trace/run.py` can record a run (events, config, seed, fetched series, causal estimates,
  clock readings) and replay it offline byte for byte (`python -m seleric_swarm.diagnosis.cli replay <record.json>`). The
  engine replays this way (`adapters/legacy_engine_runner.py`, runner `legacy_engine`, whose request carries the engine
  input). The live tool does not yet write such a record: its fetch phase is outside the ports, so a live call leaves a
  trace but no replayable record (plan O-12).
- **Fixtures.** `python -m seleric_swarm.diagnosis.cli export-fixture <record|input.json> --operator <stage>` writes one
  operator's input and expected answer; `check-fixture` runs it again. Shipped fixtures: `tests/fixtures/diagnosis_ops/`.

Plan, decisions and status: `docs/DoWhy/REBUILD_PLAN_2026-10-15.md`. Operations: the stack is built from
`~/work/agent_dowhy_deploy` (plan D-42).
