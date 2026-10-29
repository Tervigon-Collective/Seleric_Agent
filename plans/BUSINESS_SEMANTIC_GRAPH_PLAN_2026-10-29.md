# Business semantic graph + UAT fixes: plan (2026-10-29)

## Context

The 2026-10-29 UAT ran 82 regression items: 52 new persona, BI and why items, all cross-checked in ClickHouse. It found 40 bugs.

Most of them share one cause. The catalogue is a good **metric dictionary**, but it has no **business model**:
- no entity keys or conformed joins
- no ordered funnel with units (an event vs a distinct session)
- causal direction mixed with accounting identities
- no measured availability (grain twins, fill rate, freshness)
- no period or default policies

On top of that, the runtime has its own gaps:
- no structured context carried across turns
- claims are checked for numbers only
- the analysis is orchestrated by the LLM, which takes 89% of turn time
- tool errors are hidden from the harness

**Correction to what I told you earlier.** Part of this graph is already read at runtime, on the **DoWhy branch** (`~/work/agent_dowhy`, branch `DoWhy`):
- `scripts/vendor_priors.py` turns Core's `dependency_graph.json` into `diagnosis/data/priors.json`. It holds 40 causal edges, 105 pairs and the tiers.
- `diagnosis/graph/live.py` builds a per-question graph from the live catalogue plus those priors.
- It was deployed on 10-16 (D-61). The peer rebuild at 01:57 IST today came from `/opt/seleric/Seleric_Agent` (branch `gaurav`), which does not contain it. Checked: the running container has no `diagnosis/` package, and 0 missions in the last 30 hours carry an investigation id.
- So **all the why-tests (r72–r82) ran on the legacy engine.**

**Decision (owner, today):**
- Do **not** merge DoWhy into `gaurav`.
- `main` and `gaurav` stay untouched until the owner says otherwise.
- **Rebase `DoWhy` onto `gaurav`**, so DoWhy's 32 commits sit on top of the live code.
- All agent work then happens on `DoWhy`.

Checked:
- `main` (191e86c, 26 Sep) is already contained in both DoWhy and `gaurav`.
- What DoWhy lacks is the 9 commits on the live `gaurav` branch (presentation, mail, the catalogue registry, date-axis).

**Goal:** one business semantic graph, kept as data in Agent Core `catalogue_src/`:
- served by catalogue tools
- carried into the agent through the existing vendor path
- consumed by the planner, the diagnosis engine and the answer checks

No second graph file or loader in the agent. No hardcoded metric names, no regex.

## Rules for every phase
- `main` and `gaurav` (Seleric_Agent), and the live dir `/opt/seleric/Seleric_Agent`, stay untouched until told. Agent code changes go on `DoWhy` only. Core changes go in a Core worktree on its own branch, never in the live Core dir.
- Commit or push only when the owner says.
- Before any build or deploy, coordinate with peers. There is uncommitted peer WIP in:
  - `Seleric_Agent`: 16 files, including office-ui
  - Core: `query_planner.py`, `views.yaml`, `dataset_builder.py`, `catalogue_service/service.py`, pdp catalogue files
- Cut a rollback tag before every deploy.
- Run live tests with only the golden 6 plus the relevant items from 31–82. Never the full golden 30.
- Check numbers in ClickHouse (`serve.mart_profit FINAL`, brand 20), not just unit tests.
- mage-ai changes: `EXPLAIN (TYPE VALIDATE)`, then watch a real orchestrator run before leaving.

---

## Phase 0: Rebase DoWhy onto gaurav (Seleric_Agent)

`main`, `gaurav` and the live dir `/opt/seleric/Seleric_Agent` are not touched: no merge, no rebase and no checkout there.

1. Work only in the DoWhy worktree `~/work/agent_dowhy`, which is clean.
   - First, `git branch DoWhy-pre-rebase DoWhy` as a local safety copy.
   - Then `git rebase gaurav`: DoWhy's 32 commits are replayed on top of `gaurav`'s 9.
2. Resolve conflicts commit by commit. Expected in:
   - `agent/runner.py`
   - `causal/diagnosis.py`
   - `toolsets/diagnosis.py`
   - prompt and grounding files

   Keep `gaurav`'s behaviour plus DoWhy's diagnosis changes.
3. **Not pushed.** `origin/DoWhy` already has the old history, so pushing the rebased branch needs a force push, and only when the owner says.
4. Run the gates on the rebased branch:
   - the unit suite
   - `tests/unit/diagnosis`
   - `eval/diag/`: 18 cases + 8 controls against the committed baseline, with a float tolerance of 1e-6
   - replay of the 40 records
5. Deploy the same way as D-42/D-61:
   - Sync the staging dir `~/work/agent_dowhy_deploy` from the DoWhy worktree, overlaying the live dir's uncommitted peer files as before.
   - Tag `rollback-<date>` from the running image, then build from the staging dir.
   - Agree the slot with the peer first: their next rebuild from `/opt/seleric/Seleric_Agent` drops DoWhy again, as it did at 01:57 today.
6. Re-run why items 72–82. Sort causal bugs 21–30 into two groups: still failing on DoWhy, or already fixed.

**Gate 0:** the rebase is clean, tests green, `ENGINE_VERSION diagnosis-core/2` in the container, the golden 6 at least 5/6 (the same as 10-16).

## Phase 1: Make the harness tell the truth (Seleric_Agent `eval/regress/`)

Everything after this phase uses items 31–82 as gates, so they must stop passing hollow.
- `run_regression.py`: a turn counts as failed if it has any `trace.stats.errors`, not only the runner's `failed_tools`. Write tool errors and the step model (gpt-5-mini vs gpt-4o fallback) into each result.
- Trace truncation: today tool returns are cut at 2,000 characters. Keep full tool returns in the run record and truncate only for display.
- Golden items 22–23: move their SQL checks from the dropped `serve.order_pnl_daily` to `serve.mart_profit`.
- Items 31–82: add a number check against ClickHouse wherever the question has a ground truth (the ground truths are listed in the memory file). Loosen r77.
- Record each item's bug ids in a `bugs` field, so a run reports which bugs are still open.

**Gate 1:** a run on the current build reports the known bugs as FAIL instead of 17/17 PASS.

## Phase 2: The business semantic graph (Agent Core, data first)

Each layer follows the same pattern:
1. YAML in `catalogue_src/`
2. sections emitted by `scripts/build_dependency_graph.py`
3. checked by `validate_catalogue.py` / `catalogue_gates.py`
4. served by catalogue tools
5. re-vendored into the agent by `scripts/vendor_priors.py`, with the schema version bumped. Its file-equals-script test stays.

`priors.json` is never edited by hand.

| Layer | Where | What it adds | Bugs it fixes |
|---|---|---|---|
| **2a Entities and keys** | extend `datasets.yaml` (`declared_keys` exists) | Entities: order, customer, session, product, campaign, ad set. Each gets its key column per view and its conformed join paths, including a product key across `product_ad_spend` and `pdp_page_views`. `capability_audit.py` fails if a view lacks a declared key | 15, r67, product_handle/product_title tool errors |
| **2b Processes and stages** | `causal_priors.yaml` (tiers exist) | An ordered funnel: session → product view → add to cart → checkout → purchase. Each stage names its **event metric** and its **distinct-session metric**, and declares the unit | 4, 20, 21, r82 |
| **2c Causal vs accounting** | `causal_priors.yaml` + composition metadata | (1) Missing co-measures: customers≡orders, add_to_carts ~ sessions reaching add to cart, the two net-sales bases. (2) A direction on each identity: which side is the outcome, so bridges only decompose top-down. `ad_spend = contribution − net_profit` becomes illegal | 23, 26, r59, r76 |
| **2d Availability** | `datasets.yaml` `physical_bindings` (daily/hourly exist) + a new generated `availability.json` | (1) Grain twins derived from daily/hourly bindings, e.g. `paid_media` ↔ `paid_media_hourly`. (2) Fill rate per dimension per view, measured by a script against ClickHouse, not written by hand; `device_type` = 0%. (3) Freshness from `serve.quality_checks`. A dimension under a threshold is flagged as unavailable in tool output | 1, 31 (exposure side), r64 |
| **2e Period and default policies** | `agent_policy.yaml` | (1) A default window per question kind, always stated in the answer. (2) Equal-length comparison: say what was trimmed. (3) Returns and RTO on event date, everything else on order date (owner rule of 10-21). (4) Diagnosis takes its window from the context, never "yesterday" by default | 5, 7, 8, 14 |
| **2f Definitions as objects** | existing metric definitions | `catalogue_get_metrics` already returns the formula. Add an "answer by definition" policy flag so Phase 6 can check definition claims against it (Net ROAS = contribution before ads ÷ ad spend) | 2, 10 |

Serving:
- `catalogue_resolve_dimension(text)` (`gateway/server.py:738`) gains optional `metric` / `view` context, so a follow-up resolves inside the current view.
- One new read tool returns a metric's entities, stages, availability and grain twin. Or extend `catalogue_get_metrics` instead; choose whichever means fewer tool calls for the agent.

**Gate 2:**
- `validate_catalogue.py`, `catalogue_gates.py`, `verify_compositions.py`, `verify_ratio_components.py` and `capability_audit.py` all pass.
- `vendor_priors.py` output test passes.
- The graph explorer shows the new sections.

## Phase 3: Planner fixes (Core, coordinate with the peer WIP in `query_planner.py` first)
- `partial_compare_window` (`app/query_planner.py:100`, called around line 1019) returns None when a view has no datetime_dimension. It should instead route to the grain twin from 2d. With no twin, it must fail closed and say "full-day vs partial-day" rather than return None silently. Fixes bug 1.
- Module aliases (`paid_media` vs `paidmedia`), the unknown `product_profit`, rejected multi_view ids, and `build_dataset_query` grain `none`: return a clear error naming the valid options, taken from the catalogue.

**Gate 3:** Core tests pass (`tests/test_query_planner.py`). On a live MCP call, same-hour ad spend today vs yesterday matches ClickHouse hourly (yesterday hours 0–1 ≈ ₹1.8k).

## Phase 4: Analysis context across turns (Seleric_Agent, DoWhy tree)
- One `AnalysisContext` record per turn, written for **every** tool, including `break_down_metric`, `diagnose_metric_change` and catalogue tools. Today only tools with a query_id are recorded. Fields:
  - metrics
  - view
  - window and comparison window
  - filters
  - dimensions
  - result ids
  - a compact copy of the result rows
- Code sites: `agent/runner.py` `_persist_turn`, `_thread_memory`, `_history`.
- The next turn gets the record as structured data, not prose. The resolver (Phase 2 serving) and diagnose take window and scope from it.
- Rehydrate the previous turn's rows by result id, which fixes "rows were not kept".
- Tool notes (such as the trim note) are carried into the answer instead of dropped.

**Gate 4:** live sessions bi1–bi10 follow-ups (r49/r50) answer with no clarification. r54 diagnoses September COD. r52 states or avoids the August trim.

## Phase 5: Graph-driven drill and diagnosis (DoWhy tree `diagnosis/` + `toolsets/diagnosis.py`)
Fix bugs in their DoWhy location, not in legacy `causal/diagnosis.py`, which becomes a shim. Only what Phase 0 shows is still failing:
- **Funnel ladder for additive outcomes:** `_funnel_pool` (`toolsets/diagnosis.py:889`) uses the 2b stages, so orders/net sales are split into sessions × stage rates on daily data. Fixes 21, 30, r82.
- **Tautology exclusion:** comes from the 2c pairs through `diagnosis/graph/policies.py` (`co_measure_of_outcome`). The legacy name-token filter at `toolsets/diagnosis.py:166` is removed. Fixes 26.
- **Bridge direction:** taken from 2c. Profit questions go down to orders and conversion through the graph, so profit framing and sales framing agree. Fixes 23.
- **Mix vs rate text:** `diagnosis/report/narrative.py:181` says "rates within each group held" only when the measured within-group rate change is small. Otherwise it reports both, e.g. Demand Gen CPC 1.36→4.15. Fixes r72, r73.
- **Segment drill:** `exploration/space.py plan_dimensions` drills recursively:
  1. the top segment, then inside it
  2. narrate the top-k by share of change, not only `where.top[0]`
  3. rank new or grown entities, e.g. TRAVELPODBED, as well as existing ones

  Ratio outcomes such as CPC are split into numerator and denominator per segment. Fixes 24, 25, 27, 28.
- **Break-even and loss:** answered from compositions, e.g. break-even MER = 1 + ad spend / contribution, CAC ≤ contribution per order. No new hardcoded formula. Fixes 29.

**Gate 5:**
- `eval/diag` scores no worse than the baseline.
- Live why items 72–82 name the ClickHouse-verified cause:
  - 9 Oct: ATC stage
  - Google: Demand Gen clicks and Search CPC
  - Meta: TRAVELPODBED
  - Oct 19 week: sessions, not customers

## Phase 6: Answer checks, tool policy, latency (Seleric_Agent)
- **Claim checks beyond numbers** (`agent/grounding.py`):
  - A definition claim must match the catalogue formula.
  - "Nothing stale or missing" requires an availability or quality call in the same turn.
  - An assumed window or period must be stated.
  - Fix the false positives on r52, r58, r60, r80 and r81.
- **No zero-tool data answers:** a final answer about the brand's numbers, definitions or data health with no tool call is sent back once, through the existing revision path. It applies to the gpt-4o fallback too. Fixes 3, 16, r63, r64.
- **Model fallback visible:** the step model goes into the trace. The shared `MODEL_HEALTH` cooldown (`agent/model.py resolve_loop_model`) is logged when it switches models.
- **Hidden failures:** the runner's `failed_tools` merges `trace.stats.errors`, so tool errors reach the revision step.
- **Latency:** drill and diagnosis plans now run deterministically inside one tool call (Phase 5), which cuts LLM steps. Target: median under 45s on items 31–82 (today 48–79s).

**Gate 6:** relevant items 31–82 pass with number checks. The golden 6 pass at least 5/6.

## Phase 7: Data fixes (mage-ai)
- **device_type:**
  - Gold is built from Iceberg, which has no user agent.
  - Add UA parsing from the ClickHouse `atomic.events` path, or carry the UA into Iceberg enrichment.
  - Until then, 2d reports it as unavailable.
- **ATC naming:** split into `add_to_cart_events` and `sessions_with_add_to_cart`. Fold in the dual-tracker double-fire dedupe from the 10-24 web-funnel audit.
- **One net-sales basis:** make `mart_profit` canonical (my call; it is the P&L the rest of the catalogue uses). Mark `commerce_performance_daily` as a different basis via the 2c co-measure pair until it is reconciled.
- **Unallocated product ad spend (46%):** add a campaign→product mapping table, keyed by the 2a product key.

**Gate 7:**
- `EXPLAIN (TYPE VALIDATE)` passes on the changed models.
- One real orchestrator run completes.
- `device_type` fill rate is above 0 in ClickHouse, and 2d picks it up.

---

## Critical files
- **Core:**
  - `catalogue_src/{causal_priors,datasets,agent_policy}.yaml`
  - `scripts/build_dependency_graph.py`
  - `scripts/{validate_catalogue,catalogue_gates,capability_audit}.py`
  - `src/seleric_mcp/app/query_planner.py` (peer WIP)
  - `src/seleric_mcp/gateway/server.py`
  - `src/seleric_mcp/dataset_contracts.py`
- **Agent (DoWhy tree):**
  - `scripts/vendor_priors.py`
  - `diagnosis/data/priors.json` (generated)
  - `diagnosis/graph/{live,policies,subgraph}.py`
  - `diagnosis/report/narrative.py`
  - `toolsets/diagnosis.py`
  - `exploration/space.py`
  - `agent/{runner,grounding,model}.py`
  - `eval/regress/{run_regression.py,regression.json}`
- **mage-ai:**
  - `dbt/models/iceberg/snowplow/silver/int_snowplow_session_device_geo.sql`
  - the web funnel gold models

## Reused, not rebuilt
- DoWhy graph path: `vendor_priors.py`, then `graph/live.py`, then `subgraph.py` / `policies.py`
- Edge confirmation (D-56/D-59)
- Run record and replay, `eval/diag`
- `dataset_contracts.py` (daily/hourly bindings)
- Core gate scripts
- Prompt registry: the 12 uat-* prompts already added

## Verification
- **Per phase:** the gate above.
- **End to end:**
  1. Build from the staging dir synced from DoWhy, after cutting a rollback tag.
  2. Run the golden 6 plus the relevant items from 31–82: `python3 run_regression.py regression.json results/<dir> 2 only=…`, launched as the background command itself.
  3. Every number check is against ClickHouse.
  4. Compare the open-bug list from the `bugs` field against the 40 found today.
- **Done when:** no wrong-answer bug (group A) is still open; groups B and C are down to those needing Phase 7 data; tool errors are 0 or surfaced.
- **Recommended order:** 0 → 1 → 2a/2d/2e → 3 → 4 → 2b/2c → 5 → 6, with Phase 7 alongside once mage-ai is free. Phase 1 must land before any gate is trusted.
