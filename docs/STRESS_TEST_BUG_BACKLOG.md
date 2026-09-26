# Seleric Agent — Stress-Test Bug Backlog (Priority-Ranked)

_As of 2026-09-25_

Twelve questions of increasing complexity were run live on 2026-09-25: 8 passed, 2 returned partial, 2 failed. Bugs are ranked by priority — P0 breaks or hangs a mission, P3 is a labeling caveat. Each carries the mission and step numbers it was observed on.

Question ladder: `L1 lookup → L2 aggregation → L3 comparison → L4 breakdown → L5 multi-metric → L6 trend → L7 diagnostic → L8 cross-domain → L9 cohort/LTV → L10 counterfactual → L11 ambiguous → L12 out-of-data`. Results: 8 pass · 2 partial (L8, L12) · 2 fail (L4, L11).

## P0 · Critical

These make a mission fail or hang.

| Bug | Evidence | Root cause | Fix | Status |
| --- | --- | --- | --- | --- |
| Whole mission re-runs 3× and still fails despite a valid answer | L4 "net revenue by channel" — 3 `run.started`, 21.7 min; produced a channel table, marked `INSUFFICIENT_EVIDENCE` each time | Evidence gate rejects a drilldown/derived answer; retry re-runs the whole mission with no dedup | Cap re-attempts when the failure is deterministic; loosen the gate to accept drilldown/derived evidence | ✅ **Fixed & verified.** Both mechanisms already fixed at source: (a) `INSUFFICIENT_EVIDENCE` excluded from `_RETRYABLE_ERROR_CODES` (`conversations.py:1168`) → `RunExecutionResult.retryable=False` → worker never re-raises (`recovery.py:212`), so no 3× re-run; (b) `scope.py` sibling-widening lets a `lt_channel` drilldown satisfy a "by channel" scope (`_demo` covers L4). Added regression lock `test_insufficient_evidence_verdict_is_not_retried` (fault-injection confirmed it bites: `calls==2` when fix removed). |
| Vague executive question fails with zero attempt | L11 "How is the business doing?" — called `final_result` immediately, no metric fetched, `INSUFFICIENT_EVIDENCE` | Jev classifies "how is the business doing?" as `conversation`; `intent=="conversation"` strips **all** tools (`runner.py:604` → `agent.py:157` returns `[]`), so the broad-subject playbook (`instructions.py:63`) is unreachable — the agent has nothing to query with | Business-state feature (deferred) — decompose an open-ended question into headline metric fetches | ⏸️ **Deferred** — needs the business-state feature; not this pass |

## P1 · High

Wrong or inconsistent output, and the biggest waste.

| Bug | Evidence | Root cause | Fix |
| --- | --- | --- | --- |
| "Revenue by channel" resolves to two different metrics and totals | L4 used `attributed_net_revenue` by `lt_channel` = ₹34.1L (ig_feed/fb_feed); L7 used `net_sales_all_channels`/`ad_channel_net_sales` = ₹28.0L (Meta/Google) | **Traced 2026-09-26.** "Revenue by channel" is a genuinely ambiguous concept spanning two catalogue metrics from **different Cube views** — attribution (`attributed_net_revenue`, dim `lt_channel`, last-touch) vs P&L/commerce (`net_sales_all_channels`, sales/ad channel). `search_semantics` takes whatever `catalogue_search_metrics` ranks #1, and that ranking is **phrasing-sensitive** (server-side glossary+vector), so the top hit flips between the two. Compounded by `instructions.py:56-61` telling the agent to take the top match and "not agonize" because top hits are "near-identical siblings" — false here (~20% apart, won't reconcile). The distinguishing `view` + description *are* returned to the agent (`_slim_match`, `semantic.py:393`) but the instruction says to ignore them. | **Root fix is in the cube/MCP catalogue, not this repo** — designate a canonical metric for the bare "revenue by channel" concept (or tag attribution-channel vs P&L-channel as distinct concepts) so `catalogue_search_metrics` returns a stable #1 regardless of phrasing. Complementary agent-side hardening (this repo): when the top-2 matches come from different views/domains and won't reconcile, don't treat them as interchangeable — pick the operator-default (P&L) and/or disclose "attributed vs P&L". Catalogue defs live in the MCP/cube (mage-ai/infra), served over `SELERIC_MCP_URL`; they are **not** defined in Seleric_Agent. |
| Identical failing tool call repeated despite an explicit stop guard | L3 `compare_periods` 3× (steps 11,15,17), each `success=false`; system returned "REPEATED CALL — do not call again" and it called again | Agent ignores the repeat-guard signal | Hard-block a second identical call in the tool layer (return cached result, spend no LLM turn) |
| Fabricated metric ids queried before resolving | L12 tried `inventory_turnover_by_warehouse`, `inventory_on_hand_value_by_warehouse` (don't exist) → retries | **Fixed 2026-09-26.** `_reject_unknown_metric` (`semantic.py:563`) had a fall-through: an id absent from the catalogue **with no close candidate** returned silently, so the fabricated id reached Cube, whose error is `retryable=True` (`_fetch_failure`) → the agent looped guessing more fake ids. | ✅ **Fixed.** Guard now returns a non-retryable `UNSUPPORTED_QUERY` for an unknown id with no near-match (before any Cube call), telling the model to search or report unavailability; an unknown id that *does* have near-matches still `ModelRetry`s with the real ids. Applied to both `query_metrics` and `get_metric_definition`. Snapshot membership is the resolve gate (a real id — from search or the prompt catalogue — is in the snapshot; a fabricated one isn't). Regression: `test_query_metrics_rejects_fabricated_metric_without_calling_cube` (fault-injection confirmed it bites). **No catalogue/search change needed** — L12 was the agent skipping resolution, not a search-quality miss. |

## P2 · Medium

Redundancy, thrash, and weak recovery — these inflate steps and latency.

| Bug | Evidence | Root cause | Fix |
| --- | --- | --- | --- |
| Redundant definition and metric fetches | L3 `get_metric_definition(gross_sales)` 3× (steps 21,23,25), same Sep/Aug `query_metrics` ≈3×; L10 fetched definitions after already querying | **Traced/fixed 2026-09-26.** Most of the prescribed memo already existed: `query_metrics` → per-mission `query_cache` (arg-keyed, a hit skips even the Cube-query budget), and `RepeatCallGuard` (`agent.py`) replays any byte-identical tool call from cache *without* running the tool body — so identical `get_metric_definition`/`query_metrics` repeats already cost no MCP call and no budget, withdrawing the tool after 3. The remaining gap: definitions had **no by-id cache**, so the singular↔batch overlap (a metric fetched by one re-fetched by the other — different args, invisible to the guard) still hit MCP and burned the 4-lookup budget (L10). | ✅ **Fixed.** Added a shared per-mission by-id definition cache (`_definition_cache_key`, reuses `query_cache`): both `get_metric_definition` and `get_metric_definitions` consult/populate it, a cache hit spends no lookup budget, and an all-cached batch makes no MCP call. Regressions: `test_metric_definition_is_cached_per_mission`, `test_definition_cache_is_shared_across_singular_and_batch` (both fault-injection-confirmed). |
| Search/knowledge thrash on missing data | L12 `search_semantics` 4× until budget exhausted, `search_knowledge` 3× after the first return said "corpus is empty" | Both tools let the model burn the full budget (`_MAX_SEARCHES=3`, `_MAX_KNOWLEDGE_SEARCHES=2`) even when the first result was a definitive "nothing here" — a miss returned `success=True` with no early stop | ✅ **Fixed 2026-09-26.** `search_knowledge`: an empty corpus is a deployment/data state that retrying can't fix → withdraws the tool on the first empty-corpus signal (like `MCP_UNAVAILABLE`), so no re-search. `search_semantics`: withdraws after the **2nd** empty result (`_MAX_EMPTY_SEARCHES=2`, `_empty_search_verdict`) — tolerates one rephrase, then returns a decisive "not modelled — tell the user it is not available" before the 3-search hard cap. Regressions: `test_empty_corpus_withdraws_the_tool_so_it_is_not_re_searched`, `test_search_semantics_stops_after_repeated_empty_results` (both fault-injection-confirmed). |
| No recovery from a deterministic tool error | L3 `compare_periods` refuses unequal windows (25 vs 31 days); agent retried instead of normalizing to a daily rate | Tool constraint not handled by the agent | ✅ **Fixed 2026-09-26.** `compare_periods` now appends the recovery path to its refusal ("re-fetch both periods over the same number of days, or fetch each as a daily-grain series and compare daily averages"), non-retryable so the agent re-fetches instead of repeating the identical call. The related "repeat despite stop guard" is already structurally handled — `_refuse` is non-retryable, so `RepeatCallGuard` replays + withdraws after 3. Regression: `test_compare_periods_unequal_windows_teaches_the_recovery_path`. |
| "Last month" resolves inconsistently | L1 → August (previous complete month); L7 → September (current partial vs prior) | **Fixed 2026-09-26.** The runner only pinned `today`/`yesterday` in the system block; every other relative window was left to the LLM. And `window_from_query` had no branch for bare "last month/week/year" (previous complete period) — it returned `None`. | ✅ **Fixed.** Added bare "last month/week/year" → previous complete calendar period in `window_from_query`/`resolve_time_range`, and the runner now pins the resolved dates in the system block (`_resolved_window_line`) exactly like today/yesterday, so "last month" is the same August for every mission. Regressions: `test_window_from_query_bare_last_period`, `test_resolve_time_range_bare_last_period_tokens`, `test_resolved_window_line_pins_bare_last_month` (fault-injection-confirmed). |

## P3 · Low

Correctness is fine; the framing could mislead. Both were caveated in-answer.

| Bug | Evidence | Fix |
| --- | --- | --- |
| LTV denominator is orders, labeled customers | L9 divided by `attributed_new_customer_orders` (orders) but called it LTV per customer | ✅ **Fixed 2026-09-26.** `instructions.py` now tells the agent to label a per-unit figure by its denominator — "per order" unless the denominator is a distinct customer count — and not to relabel orders as customers. Guard: `test_agent_labels_per_unit_figures_by_their_denominator`. |
| "Best contribution margin" names the most-loss-making channel | L8 called Meta "best" (CM ₹6.75L) while Meta had the most negative net profit; CM = net profit + spend | ✅ **Fixed 2026-09-26.** `instructions.py` now tells the agent to rank "best/top/most profitable" by net profit (not contribution margin), and that CM is pre-advertising so a channel can lead on CM while losing money after ad spend. Guard: `test_agent_ranks_profitability_by_net_profit_not_contribution_margin`. |

## Latency root causes

Response time ≈ number of sequential LLM round-trips × per-call latency. Ranked by impact:

1. Reasoning-model per-call latency (`DeepSeek-V4-Pro`) — dominant; every extra tool call adds a full round-trip. Fast-model routing only helps simple intents.
2. Redundant tool calls inflate the step count — L3 33 steps / 62s, L12 28 steps / 41s.
3. Whole-mission re-attempts on evidence-gate failure — L4: 3× = 21.7 min.
4. JEV classification adds a ~3s floor per query.
5. Worker queue backpressure — 12 concurrent submissions; L4 waited 106s to start.
6. Deterministic tool failures (`compare_periods` unequal windows) trigger extra recovery round-trips.

## Fix plan

Ordered by return-on-effort against risk. First one is the cheapest, highest-impact.

Status as of 2026-09-26.

| # | Fix | Bucket | Bugs it closes | Risk | Status |
| --- | --- | --- | --- | --- | --- |
| 1 | Hard-block identical repeated tool calls (return cached result, no LLM turn) | Tool layer | P1 repeats, P2 redundancy; cuts L3/L12 thrash | Low | ✅ **Done.** Already shipped via `RepeatCallGuard` (all tools: replay 2nd, withdraw 3rd) + per-mission `query_cache` for `query_metrics`. Closed the remaining gap: added a shared by-id definition cache so the singular↔batch overlap is deduped too. |
| 2 | Loosen the evidence gate to accept drilldown/derived answers; cap re-attempts on deterministic failure | Validation | P0 (L4), P0/P1 false-negatives | Medium | ✅ **Done.** Both halves were fixed at source (scope sibling-widening in `scope.py`; `INSUFFICIENT_EVIDENCE` excluded from `_RETRYABLE_ERROR_CODES`). Added the missing regression lock (`test_insufficient_evidence_verdict_is_not_retried`). |
| 3 | Business-overview playbook for open-ended questions | Prompt | P0 (L11) | Low | ⏸️ **Deferred.** Needs the business-state feature (a prompt line alone can't help — a `conversation`-classified query is stripped of all tools). Tracked as P0-2. |
| 4 | Canonical "by channel" metric mapping (attributed vs P&L) | Metric layer | P1 metric non-determinism | Medium | ⏳ **Remaining — root fix not in this repo.** The non-determinism originates in the cube/MCP catalogue ranking; the durable fix (canonical concept→metric, or tag attribution- vs P&L-channel as distinct) is an edit there. Traced under P1-1. Optional agent-side mitigation (don't treat cross-view top matches as interchangeable siblings) is a heuristic, not the root fix — not applied. |
| 5 | Deterministic relative-date resolver before the agent runs | Metric layer | P2 "last month" drift | Low | ✅ **Done.** `window_from_query`/`resolve_time_range` gained bare "last month/week/year" (previous complete period); the runner pins the resolved dates in the system block. |
| 6 | Reject unknown metric ids fast; require resolve before query | Tool layer | P1 fabricated ids, P2 search thrash | Low | ✅ **Done.** `_reject_unknown_metric` now fails an unknown id fast (non-retryable, before Cube) instead of falling through; snapshot membership is the resolve gate. Search-thrash also closed: `search_knowledge` withdraws on empty corpus, `search_semantics` withdraws after 2 empty results. |
| 7 | Worker concurrency for burst load | Infra | Latency #5 | Medium | ✅ **Done.** `RunRecoveryWorker.run_once` executed claimed attempts serially (`await self._execute` in the loop); now runs up to `run_max_concurrency` (default 4) at once via a slot-before-claim semaphore, so a burst no longer queues behind one mission. Conservative default — missions share the Azure quota. |

**Done:** 1, 2, 5, 6, 7, plus every other in-repo bug — P1-2 (repeat guard), all P2 items (redundant fetches, search/knowledge thrash, `compare_periods` recovery, "last month" drift), and both P3 framing items. **Deferred:** 3 / P0-2 (business-state feature, by decision). **Remaining:** 4 / P1-1 only — its root fix lives in the cube/MCP catalogue (being rebuilt separately), not here.

---

_Method: 12 questions submitted as standalone threads via the live conversations API, as_of 2026-09-25, Asia/Kolkata; status, elapsed, step count, limitations, and answer text read from the mission store. Companion report artifact: https://claude.ai/code/artifact/6d7fe659-c308-42cf-8c84-91566c6a175d · Claude Doc: https://claude.ai/code/artifact/ae494a06-dc22-4ee0-b2e5-ac70b68d6b91_
