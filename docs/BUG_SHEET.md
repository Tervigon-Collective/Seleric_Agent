# Bug Sheet

> **Archived — swarm_v2-era historical incident record.** The entries below
> reference `lookup_v1`/`swarm_v2` code paths deleted during the V3 cleanup
> (see `docs/CURRENT_ARCHITECTURE.md` for the current system). Kept as-is for
> incident-history value, not maintained going forward — new bugs should be
> tracked separately, not appended here.

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
- **Fix (2026-09-17, superseded):** Added a preflight guard (`_budget_rejected_result`) in `src/seleric_swarm/orchestration/dispatch.py`, reusing `coordinator/governance/budget.py::check_budget`. **Removed the same day** — all budget/hard-stop enforcement (`check_budget`, `check_hard_stops`, `check_swarm_budget`) was disabled system-wide per explicit request; these are now no-ops. Re-enabling is a low-priority backlog item, see `docs/TASK_SHEET.md`.

### 5. MCP transport can hang indefinitely past its own configured timeout
- **Where found:** Observer UAT — "What was the refunded amount yesterday?" (operations_agent) hung for 10+ minutes with zero log progress.
- **Root cause:** `SelericMCPTransport` (`src/seleric_swarm/protocols/mcp/servers/seleric_remote.py`) sets `httpx.AsyncClient(timeout=30.0)` with 3 retries — worst case should be under ~2 minutes. A direct, independent call to the same MCP tool failed *fast* with `ConnectError`, proving the backend returns errors quickly when down. `py-spy dump` on the live hung process showed the asyncio event loop genuinely idle in `select()` — a true stalled socket read that httpx's own timeout never caught. Exact platform-level cause (suspected: a reused pooled/keep-alive connection hanging on read) not pinned down.
- **Fix:** Added a hard `asyncio.wait_for(..., timeout=timeout_s + 10.0)` backstop around every POST in `SelericMCPTransport._post()`, converting `asyncio.TimeoutError` into `httpx.ReadTimeout` so it flows through the existing retry/error-handling path unchanged. This bounds the failure mode regardless of *why* httpx's own timeout misses it — belt-and-suspenders, not a root-cause fix. Also closed a second gap: the `notifications/initialized` POST in `_ensure_session` had no timeout or retry protection at all; it now goes through the same hard-timeout wrapper.
- **Test:** `tests/contract/test_seleric_remote.py::test_stuck_connection_is_bounded_by_hard_timeout_backstop` — simulates the exact failure (a POST that never returns) and confirms it now fails after the hard timeout instead of hanging forever.

### 6. Remediation loop retries instead of adapting
- **Where found:** Live production trace, query "why did sales drop from few days" (`MS-2ecdfa472c`).
- **Root cause:** Skeptic verdict `REVISE` triggers a remediation round. In the observed trace, the round reran the diagnostic agent and got **byte-for-byte the same result** as the first attempt: same 3 hypotheses, same 3 contradictions, same `causal_confidence: ASSOCIATION_ONLY`, same `causal_obs_rows: 0`. Traced deeper: `coordinator/governance/remediation.py::execute_targeted_remediation` computes a nuanced remediation "kind" per follow-up (missing_causal_graph, hypothesis_test, etc.) intending a scoped retry, but the `extra` context it builds (e.g. `causal_validation_only`, `graph_id`) is silently dropped — `ctx.activate()` (`coordinator/graph.py`) has no parameter to forward it, and `agents/diagnostic/swarm_bridge.py`'s handler always re-runs from the same `mission.query`/`mission.time_range`/candidate set regardless of remediation kind or objective text. The entire "targeted remediation" classification layer was decorative — nothing about the actual computation varied between rounds.
- **What worked correctly:** The stall-detector (comparing follow-up signatures across rounds) correctly noticed the second round was identical to the first and stopped remediating rather than looping to the round cap for no gain.
- **Fix:** `coordinator/graph.py`'s `remediate()` now writes `ctx.mission.context["remediation_round"]` onto the shared `SwarmMission` object before re-activating specialists (the one channel that *does* survive across activations, since `run(blackboard, mission)` receives the same long-lived mission every time). `agents/diagnostic/swarm_bridge.py` reads it and widens the causal search on each retry: more history (`_CAUSAL_EXTRA_HISTORY_DAYS * (1 + remediation_round)`) and more ancestor candidates considered (`cap + remediation_round`). A retry now genuinely searches a larger space instead of reproducing the identical result.
- **Test:** `tests/unit/test_diagnostic_swarm_bridge.py::test_remediation_round_widens_history_and_candidate_cap`.
- **Not fixed (deliberately out of scope):** the deeper architectural gap — that `execute_targeted_remediation`'s per-kind routing (causal-graph-only re-run, prediction-only re-run, etc.) doesn't actually reach the specialist — is still there for every kind other than the generic widening this fix adds. A full fix means giving `ctx.activate()` a real extra-context channel.

### 7. Diagnostic causal engine gets zero observation rows for some missions — INTERMITTENT, not a deterministic defect
- **Where found:** Same trace as #6.
- **Investigated:** Traced the full path (ontology → causal graph ancestor resolution → BusinessStateService fetch) for the exact failing query. Every layer checked out correctly in isolation: `metric.net_sales`'s `depends_on` ancestors (`gross_sales`, `discounts`, `returns_cancels`, ...) all resolve to real registered metrics; calling `_fetch_observations` directly with realistic inputs produced 29 rows across 4 columns with no issue; re-running the *exact same failing query* against a live server produced `causal_obs_rows: 29` and `CAUSALLY_SUPPORTED_UNDER_ASSUMPTIONS` on the second attempt.
- **Conclusion:** Not a deterministic code defect — the mechanism is sound. The zero-rows outcome is tied to real LLM classification variance (already a documented nondeterminism elsewhere in this codebase, even at temperature 0) picking a different `primary_metric`/anomaly on an unlucky run, one that happens to have thin history. Bug #6's fix (widening history/candidates on remediation retry) directly mitigates this: a retry after a thin first attempt now has a better chance of finding enough data.

### 8. "Per day" phrasing misclassified as a day-of-week breakdown
- **Where found:** Live production trace, query "why did sales drop from 5 days, get per day data" (`MS-982a559290`).
- **Root cause:** Traced to `coordinator/catalogue_grounding.py::dimensions_in_query()`. It matches a query against a multi-word dimension name if **any single token** overlaps — for `session_day_of_week` (tokens: `session`, `day`, `week`), the generic word "day" in "get per day data" was enough to match, even though `commerce_net_revenue_daily` can't slice by that dimension at all (only `brand_id`, `report_date`). Same false-positive class the code already defended against for `status` → `fulfillment_status` via a `_GENERIC_DIM_TOKENS` exclusion set — calendar words just weren't in it. The wrong dimension got attached, the MCP fetch was rejected outright, evidence came back empty, and every downstream specialist (anomaly, diagnostic, prediction, strategy) correctly skipped via policy gates for lack of data — one misclassification cascaded into a fully empty mission.
- **Fix:** Widened `_GENERIC_DIM_TOKENS` in `catalogue_grounding.py` with calendar words (`day`, `days`, `daily`, `week`, `weekly`, `month`, `monthly`, `year`, `yearly`, `date`, ...) — same mechanism already proven for `status`, no new logic.
- **Test:** `tests/unit/test_catalogue_grounding.py::test_per_day_does_not_match_session_day_of_week` (regression) and `test_real_multi_word_dimension_still_matches` (confirms legitimate multi-word matches like `product_title` still work — the fix is scoped to generic words, not a blanket suppression).

### 14. Multi-day evidence sum compared against a single-day anomaly baseline
- **Where found:** Live production trace, "why did X change over the last N days"-style diagnostic queries — same bug class as #8, traced during the swarm_v2 heuristic-removal work (`docs/TASK_SHEET.md`).
- **Root cause:** For "over the last N days" diagnostic phrasing, `swarm/specialists/observer.py::ObserverAgent` fetched one window-aggregate Evidence row (a raw multi-day *sum*) instead of one row per day. `swarm/specialists/anomaly.py` then compared that sum directly against a single-day `expected_range` baseline, producing a false "+208% spike" for a metric that was actually declining day over day — the band-aid fix at the time was to normalize the sum to a per-day average before comparing (`anomaly.py`'s sum/normalize branch).
- **Fix (direct, not the band-aid):** `SwarmClassificationV1`/`NormalizedQuery` gained a `granularity: Literal["day","week","month","none"]` field, set directly by the LLM classifier for multi-day diagnostic phrasing, threaded into `mission.context["granularity"]` (`coordinator/graph.py`). `ObserverAgent.run()` now fetches one real Evidence row per day (capped at 31 days, `_daily_windows` helper) whenever `granularity == "day"`, so `anomaly.py`'s per-evidence loop compares like-for-like (single day vs. single-day baseline) without needing to normalize a sum at all. The sum/normalize branch in `anomaly.py` is kept only as a defensive fallback for comparison missions or diagnostic phrasing the classifier doesn't flag as per-day.
- **Test:** `tests/unit/test_domain_questions.py::test_observer_fetches_one_evidence_row_per_day_when_granularity_is_day` and `tests/unit/test_anomaly_specialist.py::test_observer_per_day_evidence_reaches_anomaly_unnormalized` (chains `ObserverAgent` → `AnomalyAgent` on one blackboard, proves 5 distinct daily values reach the detector rather than a divided average).

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

### 12. `skeptic_done` event shows `trust_label: STRONG` alongside `verdict: REVISE` — INVESTIGATED, NOT A BUG
- **Where found:** Same trace as #6 (`MS-2ecdfa472c`), first skeptic pass (seq 19).
- **Investigated:** Read `agents/skeptic/scoring/trust_score.py` and `verdict_engine.py`. `trust_label`/`trust_score` (from `score_trust`) and `verdict` (from `decide_verdict`) are computed by two independent functions. `decide_verdict` forces `REVISE` on a **blocking evidence gap**, an **unresolved high-priority alternative hypothesis**, or specific warning-category challenges — none of which necessarily lower the trust score of the evidence that *was* validated.
- **Conclusion:** Intentional, coherent design. "STRONG trust + REVISE" means "the evidence we have is solid, but there's an unresolved competing explanation we haven't ruled out yet" — matches the trace, where diagnostic reported 3 unresolved contradictions alongside the claim. Not a bug.

## Found, not yet fixed (new)

### 13. `test_diagnostic_bridge_is_idempotent` fails on template/scenario-based causal runs
- **Where found:** Full test suite run while verifying bug #6/#8 fixes (`tests/swarm/test_bridge_idempotency.py::test_diagnostic_bridge_is_idempotent`).
- **Confirmed pre-existing:** Fails identically on unmodified `HEAD` via an isolated `git worktree` check — unrelated to any fix in this document.
- **Symptom:** `SwarmDiagnosticSpecialist(scenario=_CAC_REGRESSION_SCENARIO).run(...)` produces 0 hypotheses and 0 causal artifacts (expected >0 of each). Log: `diagnostic.discovery.skipped_unobserved_no_frame metric_id=metric.sessions outcome=metric.purchase_cvr`.
- **Likely cause (not confirmed):** This scenario uses the `causal_truth`-driven `TemplateCausalEstimationService` path, which never populates the `observations` dataframe (that's only fetched in the DoWhy branch). Hypothesis discovery's "skipped_unobserved_no_frame" check appears to unconditionally require an observations frame to consider a candidate node, which would make it incompatible with the synthetic/template-scenario path by construction — not investigated further.
- **Next step:** Read `agents/diagnostic/causal_discovery.py::identify_candidate_nodes`'s frame-requirement check and determine whether it should special-case `causal_truth`-scenario runs.
