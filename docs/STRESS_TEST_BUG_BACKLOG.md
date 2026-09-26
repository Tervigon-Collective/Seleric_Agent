# Seleric Agent — Stress-Test Bug Backlog (Priority-Ranked)

_As of 2026-09-25_

Twelve questions of increasing complexity were run live on 2026-09-25: 8 passed, 2 returned partial, 2 failed. Bugs are ranked by priority — P0 breaks or hangs a mission, P3 is a labeling caveat. Each carries the mission and step numbers it was observed on.

Question ladder: `L1 lookup → L2 aggregation → L3 comparison → L4 breakdown → L5 multi-metric → L6 trend → L7 diagnostic → L8 cross-domain → L9 cohort/LTV → L10 counterfactual → L11 ambiguous → L12 out-of-data`. Results: 8 pass · 2 partial (L8, L12) · 2 fail (L4, L11).

## P0 · Critical

These make a mission fail or hang.

| Bug | Evidence | Root cause | Fix |
| --- | --- | --- | --- |
| Whole mission re-runs 3× and still fails despite a valid answer | L4 "net revenue by channel" — 3 `run.started`, 21.7 min; produced a channel table, marked `INSUFFICIENT_EVIDENCE` each time | Evidence gate rejects a drilldown/derived answer; retry re-runs the whole mission with no dedup | Cap re-attempts when the failure is deterministic; loosen the gate to accept drilldown/derived evidence |
| Vague executive question fails with zero attempt | L11 "How is the business doing?" — called `final_result` immediately, no metric fetched, `INSUFFICIENT_EVIDENCE` | No playbook to decompose an open-ended question into headline metric fetches | Add a business-overview routine (revenue, profit, MER, trend) for broad questions |

## P1 · High

Wrong or inconsistent output, and the biggest waste.

| Bug | Evidence | Root cause | Fix |
| --- | --- | --- | --- |
| "Revenue by channel" resolves to two different metrics and totals | L4 used `attributed_net_revenue` by `lt_channel` = ₹34.1L (ig_feed/fb_feed); L7 used `net_sales_all_channels`/`ad_channel_net_sales` = ₹28.0L (Meta/Google) | No canonical metric for a concept; selection depends on phrasing | Canonical "by channel" mapping; reconcile attributed vs P&L grain, or disambiguate to the user |
| Identical failing tool call repeated despite an explicit stop guard | L3 `compare_periods` 3× (steps 11,15,17), each `success=false`; system returned "REPEATED CALL — do not call again" and it called again | Agent ignores the repeat-guard signal | Hard-block a second identical call in the tool layer (return cached result, spend no LLM turn) |
| Fabricated metric ids queried before resolving | L12 tried `inventory_turnover_by_warehouse`, `inventory_on_hand_value_by_warehouse` (don't exist) → retries | Agent guesses metric ids instead of resolving them first | Reject unknown ids fast; require a search/resolve hit before `query_metrics` |

## P2 · Medium

Redundancy, thrash, and weak recovery — these inflate steps and latency.

| Bug | Evidence | Root cause | Fix |
| --- | --- | --- | --- |
| Redundant definition and metric fetches | L3 `get_metric_definition(gross_sales)` 3× (steps 21,23,25), same Sep/Aug `query_metrics` ≈3×; L10 fetched definitions after already querying | No memo of what was already fetched this mission | Cache tool results per mission; skip a call whose args were already served |
| Search/knowledge thrash on missing data | L12 `search_semantics` 4× until budget exhausted, `search_knowledge` 3× after the first return said "corpus is empty" | Agent keeps searching for data that isn't modelled | Stop after one empty-corpus / no-match signal; conclude "not available" early |
| No recovery from a deterministic tool error | L3 `compare_periods` refuses unequal windows (25 vs 31 days); agent retried instead of normalizing to a daily rate | Tool constraint not handled by the agent | Teach the recovery path (equal windows or daily-average) in the tool's error message |
| "Last month" resolves inconsistently | L1 → August (previous complete month); L7 → September (current partial vs prior) | No canonical relative-date resolver | Resolve relative dates once, deterministically, before the agent runs |

## P3 · Low

Correctness is fine; the framing could mislead. Both were caveated in-answer.

| Bug | Evidence | Fix |
| --- | --- | --- |
| LTV denominator is orders, labeled customers | L9 divided by `attributed_new_customer_orders` (orders) but called it LTV per customer | Label it revenue-per-new-order, or divide by distinct customers |
| "Best contribution margin" names the most-loss-making channel | L8 called Meta "best" (CM ₹6.75L) while Meta had the most negative net profit; CM = net profit + spend | Lead with net profit, or clarify CM is pre-advertising |

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

| # | Fix | Bucket | Bugs it closes | Risk |
| --- | --- | --- | --- | --- |
| 1 | Hard-block identical repeated tool calls (return cached result, no LLM turn) | Tool layer | P1 repeats, P2 redundancy; cuts L3/L12 thrash | Low |
| 2 | Loosen the evidence gate to accept drilldown/derived answers; cap re-attempts on deterministic failure | Validation | P0 (L4), P0/P1 false-negatives | Medium |
| 3 | Business-overview playbook for open-ended questions | Prompt | P0 (L11) | Low |
| 4 | Canonical "by channel" metric mapping (attributed vs P&L) | Metric layer | P1 metric non-determinism | Medium |
| 5 | Deterministic relative-date resolver before the agent runs | Metric layer | P2 "last month" drift | Low |
| 6 | Reject unknown metric ids fast; require resolve before query | Tool layer | P1 fabricated ids, P2 search thrash | Low |
| 7 | Worker concurrency for burst load | Infra | Latency #5 | Medium |

---

_Method: 12 questions submitted as standalone threads via the live conversations API, as_of 2026-09-25, Asia/Kolkata; status, elapsed, step count, limitations, and answer text read from the mission store. Companion report artifact: https://claude.ai/code/artifact/6d7fe659-c308-42cf-8c84-91566c6a175d · Claude Doc: https://claude.ai/code/artifact/ae494a06-dc22-4ee0-b2e5-ac70b68d6b91_
