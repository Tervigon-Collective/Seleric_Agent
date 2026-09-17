# Bug Sheet

Bugs found during the Meta/Google/Shopify scope-trim, phased intelligence-agent
rollout (Part B), lookup_v1 retirement, and subsequent production UAT
(2026-09-16). Status reflects the state at time of writing, not necessarily
current — check the referenced files before assuming a "Fixed" entry is still
fixed as described.

## Fixed

### 1. Stale server process serving outdated config
- **Where found:** UAT — diagnostic/prediction never activated despite being enabled in `config/agent_registry.yaml`.
- **Root cause:** A `uvicorn --reload` dev server had been running since before the config edits. Its file-watcher reloads on `.py` changes only, not YAML, so it kept serving requests with the pre-rollout registry snapshot in memory.
- **Category:** Operational, not a code bug.
- **Fix:** Killed the stale process (`taskkill`), started a fresh single-process server.

### 2. Metric-ID canonicalization bug in `lookup_fast_path.py`
- **Where found:** Porting `tests/replay/test_domain_lookups.py` and `test_lookup_v1.py` off the legacy pipeline.
- **Root cause:** `_canon()` called `MetricRegistry.get()` directly, which prioritizes the live-catalogue definition over the YAML-registered `metric.xxx` id once the catalogue is warm. Evidence for `gross_sales`, `units_sold`, `cac`, `repeat_rate`, etc. came back keyed inconsistently — bare id, canonical id, or (for one query) an entirely different live metric name (`commerce_net_revenue_daily`) instead of `metric.net_sales`. Two duplicated `_canon` closures (ungrained + grained/breakdown paths) both had this bug independently.
- **Fix:** Consolidated into one shared `_canonical_metric_id()` helper in `src/seleric_swarm/coordinator/lookup_fast_path.py` that checks `MetricRegistry.yaml_all()` explicitly before falling back to `.get()`.

### 3. Missing `INSUFFICIENT_EVIDENCE` error code
- **Where found:** Porting `tests/adversarial/test_missing_data.py`.
- **Root cause:** `run_lookup_fast_path` correctly failed closed (no fabricated numbers) when no evidence existed for a query, but left `MissionResult.error = None` instead of setting an error code. Legacy `run_mission` always set `INSUFFICIENT_EVIDENCE` in this case.
- **Fix:** `lookup_fast_path.py` now sets `error=MissionError(code="INSUFFICIENT_EVIDENCE", ...)` whenever `status == "failed"`.

### 4. No LLM budget enforcement in the new dispatch path
- **Where found:** Porting `tests/replay/test_comparison_and_budget.py::test_llm_budget_is_enforced`.
- **Root cause:** Legacy `run_mission` pre-checked `settings.max_llm_calls` and failed fast with `BUDGET_EXCEEDED` before making any LLM call. Its replacement, `run_any_mission`, had no equivalent — a real cost-control safety check would have silently disappeared when the legacy pipeline was deleted.
- **Fix:** Added a preflight guard (`_budget_rejected_result`) in `src/seleric_swarm/orchestration/dispatch.py`, reusing `coordinator/governance/budget.py::check_budget`.

## Found and root-caused, not yet fixed

### 5. MCP transport can hang indefinitely past its own configured timeout
- **Where found:** Observer UAT — "What was the refunded amount yesterday?" (operations_agent) hung for 10+ minutes with zero log progress.
- **Root cause:** `SelericMCPTransport` (`src/seleric_swarm/protocols/mcp/servers/seleric_remote.py`) sets `httpx.AsyncClient(timeout=30.0)` with 3 retries (`stop_after_attempt(3)`) — worst case should be under ~2 minutes. A direct, independent call to the same MCP tool failed *fast* with `ConnectError`, proving the backend itself returns errors quickly when down. `py-spy dump` on the live hung process showed the asyncio event loop genuinely idle in `select()` — not looping, not deadlocked on a Python lock — meaning it was a true stalled socket read that the configured timeout never caught.
- **Suspected cause:** A pooled/keep-alive connection getting reused after going stale, hanging on read instead of raising promptly. Not confirmed.
- **Next step:** Needs a deliberate repro (a mock server that accepts a connection then goes silent) to pin down why `timeout=30.0` isn't bounding this specific failure mode.

### 6. Remediation loop retries instead of adapting
- **Where found:** Live production trace, query "why did sales drop from few days" (`MS-2ecdfa472c`).
- **Root cause:** Skeptic verdict `REVISE` triggers a remediation round. In the observed trace, the round reran the diagnostic agent and got **byte-for-byte the same result** as the first attempt: same 3 hypotheses, same 3 contradictions, same `causal_confidence: ASSOCIATION_ONLY`, same `causal_obs_rows: 0`. Remediation planning (`coordinator/governance/skeptic_gate.py` / remediation-task construction) doesn't vary its approach based on *why* the previous attempt failed — it just asks the same agent to try again with the same inputs.
- **What worked correctly:** The stall-detector (comparing follow-up signatures across rounds) correctly noticed the second round was identical to the first and stopped remediating rather than looping to the round cap for no gain. The mission finished `partial` with an honest "additional refutation tests are still needed" message instead of a false-confidence answer.
- **Next step:** Remediation planning needs to change *what it asks for* between rounds (e.g., widen the time window, fetch more confounder history, try an alternate estimator) rather than re-issuing an identical request.

### 7. Diagnostic causal engine gets zero observation rows for some missions
- **Where found:** Same trace as #6.
- **Root cause:** `causal_obs_rows: 0` in the diagnostic event — DoWhy had no observations dataframe to run refutation tests (placebo, random-common-cause, data-subset) against, so no hypothesis could ever be promoted to "retained" regardless of how many remediation rounds ran. Whatever populates the observations dataframe for this domain/metric combination isn't supplying real historical rows for this query shape (possibly because "a few days" is too short a window for the confounder fetch).
- **Next step:** Trace where the DoWhy `observations` dataframe gets built in `src/seleric_swarm/agents/diagnostic/` to find why it's empty for this case.

### 8. "Per day" phrasing misclassified as a day-of-week breakdown
- **Where found:** Live production trace, query "why did sales drop from 5 days, get per day data" (`MS-982a559290`).
- **Root cause:** The query classifier/intake layer attached `session_day_of_week` as a breakdown dimension in response to "get per day data" — interpreting "per day" as a day-of-week grouping instead of daily time granularity. `commerce_net_revenue_daily` doesn't support that dimension at all (only `brand_id`, `report_date`), so the MCP fetch was rejected outright (`"Dimension 'session_day_of_week' ... is not supported"`), evidence came back empty, and every downstream specialist (anomaly, diagnostic, prediction, strategy) correctly skipped via policy gates for lack of data — a single misclassification cascaded into a fully empty mission.
- **Next step:** Find where natural-language time-grain phrases ("per day", "daily", "by day") get mapped to MCP query parameters (likely in `coordinator/intake/`) and fix the mapping to set `granularity: "day"` / use `report_date`, not a `session_day_of_week` dimension.

## Design tradeoffs discovered (not bugs)

### 9. `mission_lead` / `handoff_history` / `active_specialist` / `claims` always empty in the fast path
- **Where found:** Porting tests off the deleted legacy `orchestration/runner.py::run_mission`.
- **Explanation:** By design. `lookup_fast_path.py` answers every `DomainQuestion` in one pass with no agent-to-agent handoff and no Claim/Skeptic synthesis layer (see its module docstring). `mission_lead` always equals `initial_mission_lead`; `handoff_history` is always `[]`. This is the intentional fix for the metric-id ping-pong bug class the old pipeline had — a real behavior difference, not a defect.

### 10. Grain/dimension detection unreliable for "top N" / "per channel" phrasing
- **Where found:** `tests/replay/test_domain_lookups.py`, and documented independently in `docs/features/lookup-v1-retirement.md` (Phase 2a).
- **Explanation:** The LLM classifier doesn't reliably attach a `grain` hint for ranking/breakdown-shaped questions. When it misses, the fast path answers with a correct aggregate instead of the requested per-dimension ranking — silently degraded, not crashed. Still strictly better than the pre-fix `HANDOFF_REJECTED` failure. Accepted, tracked gap; related to but distinct from bug #8 (which is a wrong-dimension error, not a missing one).

### 11. Gold eval dataset went stale mid-rollout
- **Where found:** `eval/datasets/lookup_commerce.jsonl`.
- **Explanation:** The "unsupported-why-cac" row expected `query_class: unsupported, error_code: ROUTING_UNSUPPORTED` for "Why did CAC increase?" — correct before `diagnostic_agent` was enabled, wrong after (Phases 2–6 made that query correctly succeed via the swarm route). Removed the row rather than keep asserting stale behavior against a now-working code path.

## Noticed, not yet investigated

### 12. `skeptic_done` event shows `trust_label: STRONG` alongside `verdict: REVISE`
- **Where found:** Same trace as #6 (`MS-2ecdfa472c`), first skeptic pass (seq 19).
- **Observation:** A `REVISE` verdict (not yet confirmed) paired with a `STRONG` trust label reads as two contradictory confidence signals. Not traced to where `trust_label` and `verdict` are computed relative to each other in `agents/skeptic/` — unclear whether they're intentionally measuring different things (e.g., trust in the *evidence quality* vs. confidence in the *verdict*) or a genuine inconsistency.
- **Next step:** Read `agents/skeptic/`'s verdict/trust-label computation to determine if this is expected or a bug.
