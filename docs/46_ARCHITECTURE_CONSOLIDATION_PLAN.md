# 46. Architecture Consolidation Plan

**Superseded 2026-09-17** by `docs/refactor/` (the Seleric V3 PydanticAI+Cube
migration plan). Item 2 (below) is absorbed into `docs/refactor/02_PROFILE_SEMANTIC_MCP.md`
Sprint 2 — reuse its characterization suite, don't redo it. Item 5 is
absorbed into `docs/refactor/01_PROFILE_RUNTIME.md` Sprint 4, re-scoped from
"validate the five specialists" to "validate the new toolset-based agent
loop" since the specialists themselves are being retired. Items 1/1a, 3, 4
stay as-is (already done/retracted) and are not reopened. This document is
kept for historical record; new work should track in `docs/refactor/TASK_SHEET.md`.

Status: 1 of 5 original items already done (by concurrent work, not this plan); 1a (small follow-up under Item 1) executed and verified; 2 retracted as false positives; Item 2 step 1 (characterization suite) done — 7/7 passing, real findings recorded including one transient live-data divergence worth re-testing; Item 2 steps 2-5 (diff, extract, refactor, delete) not started; Item 5 still fully open. Last verified against source: 2026-09-16.
Source: code-verified architecture scan (see `diagrams/current_architecture.mmd`), cross-checked against 2026 multi-agent orchestration and legacy-pipeline-retirement practice (see References). Every item below has since been individually re-verified by direct file read — see each item's own note on what changed between the original scan and this pass.

## Why this document exists

A direct read of the codebase (not the docs, which had drifted from the code) originally flagged five candidate duplications. On re-verification (see the process note near the end of this doc), that picture changed substantially:

1. ~~Two parallel mission pipelines (`lookup_v1` legacy, `swarm_v2`)~~ — **DONE**, by a concurrent session, before this plan was even finished. See Item 1.
2. Two — now known to be **three** — independent MCP-calling data-access paths (`HybridMcpDataProvider.fetch()`, `HybridMcpDataProvider.fetch_series()`, `business_state/series.py::fetch_series()`). **Still open.** See Item 2.
3. ~~Two unrelated `ModelRegistry` implementations~~ — **RETRACTED**, false positive. See Item 3.
4. ~~`AnomalyAgent` in the wrong module tree~~ — **RETRACTED**, false positive. See Item 4.
5. No load/cost validation before broadly re-enabling the five intelligence specialists. **Still open.** See Item 5.

Net: two real items remain (2 and 5), one small precisely-scoped follow-up was found and executed (1a, inside Item 1), and two items were dropped as never having been real. Each remaining item is independently shippable.

## Guardrail that applies to every item

**Do not touch the deterministic gate / arbiter layer while consolidating.** `coordinator/governance/skeptic_gate.py` and `coordinator/governance/conflicts.py::arbitrate_conflict` are the "supervisor" this system already has — every 2026 multi-agent production pattern that survives contact with real traffic keeps one (phase gates, shared artifacts, a final supervisor; see References). Consolidation work here is about removing *duplicate plumbing around* that core, not changing what the core decides. If a change to items 1–4 below would alter skeptic-gate or conflict-arbitration behavior, split it into its own reviewed change.

---

## Item 1 — ~~Retire the legacy `lookup_v1` pipeline~~ (DONE — completed by a concurrent session, verified 2026-09-16)

**What this item originally proposed:** a staged strangler-fig retirement of `orchestration/graph.py`/`orchestration/runner.py`, gated on measuring fallback rate first.

**What actually happened, confirmed by direct read of `orchestration/dispatch.py` and `docs/features/lookup-v1-retirement.md`:** a separate concurrent session completed the full retirement — Phases 1, 2a, 2b, and the actual deletion (Phase 2) — independently of this plan, on the same date this plan was written. `dispatch.py`'s current docstring states it plainly: *"[2026-09-16, lookup_v1 retired — see docs/features/lookup-v1-retirement.md] The legacy `orchestration.graph`/`orchestration.runner` pipeline (`run_mission`) is gone."*

Verified on disk (2026-09-16): `src/seleric_swarm/orchestration/` now contains only `__init__.py`, `dispatch.py`, `state.py`, `synthesize.py` — no `graph.py`, no `runner.py`. `run_any_mission()` no longer has a fallback branch to a second pipeline; the only retry re-runs the LLM classification once (`route_for()` again) to catch a non-deterministic misclassification, then returns `ROUTING_UNSUPPORTED` if that retry doesn't resolve to `"swarm"` — there is no second pipeline being called.

The retirement doc records real value delivered along the way (not just a mechanical deletion): a genuine metric-id canonicalization bug was found and fixed in `lookup_fast_path.py`'s `_canon()` (two duplicated closures consolidated into one `_canonical_metric_id()` helper), a missing `INSUFFICIENT_EVIDENCE` error code was added, and 41 tests across 9 files were ported or retired rather than left stale.

**Residual follow-up this plan did not originally anticipate** (see the retirement doc's "Dead / parallel stacks" table — written while Phase 2 was still pending, not yet updated now that it's done): `coordinator/plane.py`, `coordinator/planning/dag_builder.py`, and `coordinator/leadership/lead_selector.py` are confirmed deleted from disk. `coordinator/planning/complexity.py` and `coordinator/governance/budget.py` are still present — but a direct grep for every caller (not an assumption from the retirement doc's table, which was written before this was checked) showed the picture was **mixed, not uniformly dead**:

- `coordinator/governance/budget.py::MissionLimits` and `check_budget` are **actively used** (still imported by `coordinator/plane.py`), not dead. **2026-09-17 update:** `orchestration/dispatch.py::_budget_rejected_result` (the fast-path LLM-budget preflight check this line originally described) was itself removed — budget/hard-stop enforcement was disabled system-wide per explicit request; `check_budget`/`check_hard_stops`/`check_swarm_budget` are now no-ops. See `docs/TASK_SHEET.md` (low-priority re-enable item) and `docs/BUG_SHEET.md`.
- `coordinator/governance/budget.py::check_hard_stops` **was** unreferenced anywhere outside its own file — deleted (see 1a below).
- `coordinator/planning/complexity.py::looks_like_diagnostic` is **actively used** by `agents/coordinator.py` (two call sites) — the file stays.
- `coordinator/planning/complexity.py::classify_complexity` and the L0-L5 band-threshold fields of the `ComplexityPolicy` class (in `coordinator/policies.py`) were genuinely unreferenced by anything except each other and their own tests — deleted (see 1a below).

**Follow-up item (1a) — DONE (2026-09-16).** Scoped precisely from the greps above, then re-verified once more before executing (the process this doc itself recommends): a whole-repo grep (not src-only) turned up two callers missed on the first pass — `check_hard_stops` had one unit test (`tests/unit/test_coordinator_control_plane.py::test_check_hard_stops_trips_on_iterations_and_transfers`), and `ComplexityPolicy` turned out to still be needed by the live `looks_like_diagnostic` (which depends on its `diagnostic_hints`/`prescriptive_hints` fields) — so the class was *trimmed*, not deleted outright as originally scoped.

Final executed change:
- `coordinator/governance/budget.py::check_hard_stops` — deleted (confirmed zero production callers; its `_elapsed_seconds` helper stays, still used elsewhere in the file).
- `coordinator/planning/complexity.py::classify_complexity` — deleted (confirmed zero callers, zero test coverage anywhere in the repo).
- `coordinator/policies.py::ComplexityPolicy` — **trimmed**, not deleted: kept `diagnostic_hints`/`prescriptive_hints` (used by live `looks_like_diagnostic`), removed the 10 L0-L5 band-threshold fields that only ever served the now-deleted `classify_complexity`.
- `config/coordinator_policies.yaml`'s `complexity:` section — trimmed to match (2 keys instead of 12).
- `tests/unit/test_coordinator_control_plane.py` — removed the `check_hard_stops` import and its one test.
- Verified: `tests/coordinator/` + `tests/unit/test_coordinator_control_plane.py` — 73 passed. Direct import smoke-test confirms `looks_like_diagnostic` still works correctly post-trim.

**Also surfaced along the way, undocumented until now:** `looks_like_diagnostic`'s two live call sites in `agents/coordinator.py` never pass a `policy` argument, so `config/coordinator_policies.yaml`'s `complexity:` section has had **zero effect** on that function since it was added — it always uses the hardcoded Python defaults via `_DEFAULT_POLICY`. Not fixed here (out of scope for a dead-code cleanup — wiring config through to a live call site is a behavior change, not a deletion); noted in both `ComplexityPolicy`'s docstring and the YAML comment so it isn't mistaken for working configuration.

**No further action needed on Item 1 — it and its follow-up are both done.**

---

## Item 2 — Consolidate the two MCP data-access paths

**Current state (verified by direct read, not just an import-list scan — see Items 3/4's retraction notes on why that distinction matters here):**
- `src/seleric_swarm/swarm/providers/mcp_data.py::HybridMcpDataProvider.fetch()` (~line 181-212) — used by every `DomainAgent.observe()` call (`swarm/domain/base.py`). Independently calls its own `_resolve_measure()` wrapper (line 138, itself wrapping `services/measure.py::resolve_measure()`) → `build_metrics_query_args()` → `call_metrics_query()` (`services/mcp_query.py`) → `MCPGateway.call()`.
- The **same class** also has a second, separately-implemented `HybridMcpDataProvider.fetch_series()` (line 274-331) — its own independent query-building logic, also calling `_resolve_measure()` and `call_metrics_query()`, coexisting with `fetch()` in the same file.
- `src/seleric_swarm/services/business_state/series.py::fetch_series()` (module-level function, not a method) — used by `BusinessStateService.get_metric_state()` (`facade.py`), which in turn is used by `lookup_fast_path.py` and anomaly-history lookups. A **third** independent implementation: its own `resolve_time_range()` + `resolve_measure()` + `call_metrics_query()` sequence, unrelated by inheritance or delegation to either of the two above.
- All three converge on the same two shared primitives (`resolve_measure()`, `call_metrics_query()`) but each independently decides how to shape the query above that — time range handling, lookback/day-count logic, dimension/grain handling. This is the layer that produced the `_query_windows` single-day-vs-range comparison bug found earlier in this investigation (that bug lived in `agents/intelligence/observer.py`, a *fourth* related but distinct window-shaping function — worth including in the audit in step 1 below).

**Target state:** one data-fetch primitive both callers use. `BusinessStateService` should be the caller of a shared fetch primitive (or vice versa) — not two independent implementations that happen to bottom out in the same two functions.

**Steps:**
1. **Done (2026-09-16).** `tests/replay/test_data_access_characterization.py` — a live-MCP characterization suite comparing `HybridMcpDataProvider.fetch()` against `BusinessStateService.get_metric_state()` for the same `(metric_id, time_range)` inputs. All three sub-cases planned for step 1 are now covered, 7/7 passing on the latest run. Findings:
   - **Single-day case: consistent.** 3/3 metrics (`metric.cac`, `metric.net_profit`, `metric.units_sold`) return identical values from both paths for a one-day window (`2026-08-01`). Confirmed twice.
   - **Multi-day case: `BusinessStateService.get_metric_state()`'s `actual` genuinely is the last day's point, not a period total** — confirmed by reading `facade.py:130` directly (`actual=series[-1].value`) and proving it empirically: for all 3 metrics, `BusinessStateService`'s `actual` over a 7-day range matched `HybridMcpDataProvider.fetch()`'s value for the *last day alone*, not the 7-day sum. This is the behavior `lookup_fast_path.py`'s own comment describes, now verified rather than assumed.
   - **One transient anomaly worth recording, not chasing further right now:** on the first run, `metric.net_profit`'s multi-day case failed — `BusinessStateService` returned `3381.96` where the last-day-alone fetch returned `1282.96` (a ~2.6x mismatch, not a rounding difference). An immediate re-run with *identical code* passed cleanly (all three metrics matched). This points to live-data non-determinism between the two calls (pipeline/ETL timing, cache staleness) rather than a reproducible logic bug — but it means **the two implementations' outputs can genuinely diverge under real production timing conditions**, which is directly relevant risk information for Item 2's eventual merge: a "characterization suite says they matched" result from one test run is not a guarantee they always will. Worth re-running this suite a few times over different days before trusting it as the full safety net step 4 needs.
   - **Grain/dimensioned fetches: confirmed structural asymmetry, not a bug.** `HybridMcpDataProvider.fetch(dimensions={"product_title": ""})` returns a real per-product breakdown (multiple rows) for `metric.units_sold`. `BusinessStateService.get_metric_state()` has no equivalent — confirmed directly from `facade.py`, which reads exactly one key out of `StateRequest.dimensions` (`brand_id`) and silently ignores everything else; two requests differing only in `product_title` returned the identical `actual`. This is a capability gap, not a bug to fix — Item 2's merge needs to decide whether the unified fetcher gains grain support in the BSS-derived path or stays scalar-only there. (Side note: `metric.net_profit`'s registry entry declares `supported_dimensions: [channel]`, but a live `fetch(dimensions={"channel": ""})` for it returned zero rows — the registry's declared-supported-dimensions list doesn't always mean a live breakdown actually works. Switched the test to `metric.units_sold`/`product_title`, which does. Worth a separate, smaller note that the registry's `supported_dimensions` field isn't fully trustworthy as a capability check on its own.)
   - Not yet covered by the suite (and not required for step 1, since the `_query_windows` bug lived elsewhere): `agents/intelligence/observer.py`'s window-shaping function, a fourth related-but-distinct implementation. Worth folding into a future extension of this suite if Item 2 expands scope to include it.
2. Diff the two implementations' query-shaping logic (time range resolution, dimension handling) line by line. Identify which one has the more correct/complete logic — do not assume; the `_query_windows` comparison-fallback bug found earlier suggests neither path is fully correct today.
3. Extract the shared logic into one function/class (candidate name: a single `MetricFetcher` or similar in `services/`) that both `HybridMcpDataProvider.fetch()` and `BusinessStateService.fetch_series()` call.
4. Re-run the characterization suite from step 1 against the refactored code — outputs must match exactly (or the diff must be an intentional, documented bug fix, not a silent behavior change).
5. Remove the now-dead duplicated logic from whichever of the two callers had its own copy.

**Rollback:** steps 1–2 are read-only. Steps 3–5 should land as one PR gated on the characterization suite passing; revert the PR if the suite regresses in production.

**Dependency:** none — Item 1 is now done (`orchestration/graph.py` is deleted), so there is no fourth legacy data-access pattern to worry about characterizing. This item's scope is exactly the three implementations named above, confirmed final.

---

## Item 3 — ~~Unify `ModelRegistry`~~ (RETRACTED — premise was wrong)

**Original claim:** skeptic's `YamlModelRegistry` and prediction's `InMemoryModelRegistry` were two unrelated, duplicated classes answering the same question.

**Why this was wrong, found on inspection:** `src/seleric_swarm/agents/prediction/registries.py:22-34` imports `InMemoryModelRegistry`, `ModelRegistry`, and `ModelRecord` directly **from `agents/skeptic/registries.py`**, with an explicit comment: *"reuse the Skeptic's model + drift infra so there is one implementation."* There is one `ModelRegistry` Protocol and one `InMemoryModelRegistry` class; prediction does not redefine it. `agents/skeptic/services/model_registry.py::YamlModelRegistry` extends that same `InMemoryModelRegistry` (`class YamlModelRegistry(InMemoryModelRegistry)`), seeding it from YAML. This is already the consolidated shape the original Item 3 was asking for.

The earlier architecture scan (an automated exploration pass over the codebase) reported these as two separate classes because it saw `InMemoryModelRegistry` named in `agents/prediction/registries.py`'s import list and misread the import as a local redefinition, without opening `agents/skeptic/registries.py` to check. This is the same failure mode Item 4 hit — surface-level pattern match instead of reading the actual code.

**Disposition:** no code change needed at the class level. The one open question worth checking separately (not part of this consolidation plan, since it's not a duplication issue): does `agents/prediction/swarm_bridge.py` actually construct a `YamlModelRegistry` (YAML-seeded, sees skeptic's registered models) or a bare `InMemoryModelRegistry` (empty unless populated in-process)? If the latter, prediction may simply have no models registered by default — a config/wiring gap, not an architecture duplication, and a much smaller fix if it matters.

---

## Item 4 — ~~Move `AnomalyAgent` to match its siblings' module convention~~ (RETRACTED — premise was wrong)

**Original claim:** `AnomalyAgent` was the only intelligence specialist not under `agents/<name>/swarm_bridge.py`, implying an inconsistent module boundary.

**Why this was wrong, found while starting execution:** `src/seleric_swarm/swarm/specialists/` is not a stray location for one file — it is a second, deliberately documented architecture tier. `swarm/specialists/base.py::SpecialistAgent`'s docstring states it explicitly: *"A specialist is a small analytical system, not a prompt... one reusable capability works with whichever Domain Agent currently leads."* That directory holds **three** lightweight specialists — `anomaly.py`, `prediction.py`, and `skeptic.py` — coexisting intentionally with the heavier `agents/prediction/swarm_bridge.py` and `agents/skeptic/swarm_bridge.py` "full subsystem" bridges. (`agents/skeptic/swarm_bridge.py`'s own docstring confirms this: *"`swarm/specialists/skeptic.py` is a lightweight in-loop attacker. This bridge exposes the same specialist interface... but delegates to `agents.skeptic.SkepticAgent`."*)

`AnomalyAgent` has no heavier `agents/anomaly/` counterpart today, but that is not evidence of a missing move — it's evidence there is currently only the lightweight tier for anomaly detection, same as there being no `swarm/specialists/diagnostic.py` or `strategy.py` lightweight tier for those two. Moving `AnomalyAgent` into a newly-invented `agents/anomaly/swarm_bridge.py` would have fabricated a "full subsystem" wrapper around code that doesn't have one to bridge to — manufactured work based on a surface-level directory-listing pattern match, not an actual inconsistency.

**Disposition:** no code change. If a genuinely heavier anomaly-detection subsystem (multiple detector strategies, its own contracts/policies file, a registry) gets built later, *that* would be the trigger to add `agents/anomaly/`, with `swarm/specialists/anomaly.py` becoming the lightweight fallback bridge — mirroring prediction and skeptic's existing two-tier shape. Until then, leave it where it is.

**Lesson for the rest of this plan:** every remaining item should get the same one-line sanity check before execution — confirm the "duplication" is actually unintentional by reading the code's own docstrings/comments, not just its file layout, before moving or deleting anything.

---

## Item 5 — Load/cost validation before broadly re-enabling intelligence specialists

**Current state:** `config/agent_registry.yaml`'s `enabled` flags for `anomaly_agent`, `diagnostic_agent`, `prediction_agent`, `strategy_agent`, `skeptic_agent` have been toggled multiple times across concurrent work sessions on this repo, with no measurement step in between. `coordinator/graph.py::_agent_enabled()` gates each one's activation per mission, but nothing currently measures the cost/latency impact of turning them all on together at production traffic volume.

**Why this matters:** industry data on comparable systems shows research-style multi-agent orchestration running roughly 15x the token cost of a plain chat call, with decomposition/aggregation overhead compounding per worker — a workflow that costs $0.50 in testing has been observed to reach $50k/month at 100k executions in comparable architectures (see References). This system already has the right knobs (`max_agent_calls`, `max_llm_calls`, `max_remediation_rounds`, `token_budget` in `config/coordinator_policies.yaml`) — this item is about validating them against real load before broad rollout, not adding new mechanism.

**Target state:** documented, measured token/latency/cost-per-mission figures for each specialist combination, with `coordinator_policies.yaml`'s budget values chosen deliberately rather than left at their current defaults.

**Steps:**
1. Pick a fixed replay set of representative queries spanning `diagnostic`, `predictive`, `prescriptive`, and `executive_health` intents (the four that activate specialists per `_make_specialists` in `coordinator/graph.py`).
2. With all five specialists enabled, run the replay set and record per-mission: `agent_calls`, `llm_calls`, total tokens (via `MeteredLLMPort.usage_for()`, already wired per `_mission_token_usage()` in `coordinator/graph.py`), wall-clock time, and terminal status (`completed`/`partial`/`failed`).
3. Compute cost-per-mission at current model pricing and extrapolate to expected production query volume.
4. If projected cost or latency exceeds acceptable thresholds, tune `coordinator_policies.yaml`'s `budgets:` section (`max_agent_calls`, `max_llm_calls`, `max_remediation_rounds`, `token_budget`, `max_runtime_s`) and re-run step 2 until within budget — this is an iterative tuning loop, not a one-shot config change.
5. Document the final chosen budget values and the measured cost/latency figures in this file or a follow-up doc, so future changes to the specialist set have a baseline to compare against.
6. Only after this measurement is complete, treat `config/agent_registry.yaml`'s `enabled: true` for the five specialists as a deliberate, reviewed production decision rather than a default left over from ad hoc testing.

**Rollback:** N/A — this is a measurement exercise, not a code change. If numbers come back unacceptable, the rollback is simply not flipping the flags to `true` in production until Item 5 or further tuning resolves it.

**Dependency:** none, but should be done before or alongside any decision to leave the five specialists permanently enabled — several sessions in this repo have already flipped them on/off without this data.

---

## Suggested sequencing

Item 1 (and its follow-up, 1a) are done. Items 3 and 4 were retracted on inspection — no code duplication existed for either. What remains:

1. **Item 2** (consolidate MCP data-access — now known to be **three** independent implementations, not two) — the highest-leverage item now remaining. No dependency on anything else.
2. **Item 5** (load/cost validation) — do continuously alongside Item 2, and treat the final measured sign-off as a gate before calling the specialist rollout "done."

## Process note

Two of this plan's five original items (3 and 4) were generated from an automated codebase-exploration pass that read import statements and directory layouts but did not open every file to confirm the claimed duplication was real. Both turned out to be false positives once the actual source was read — see their retraction notes above. Item 1 also turned out to be stale in a different way: it was already fully completed by a concurrent session on the same day this plan was written, independently of this plan, with its own detailed record in `docs/features/lookup-v1-retirement.md`.

The corrected process, applied for the rest of this plan: **before scheduling or starting a consolidation item, read the target files directly and confirm both (a) the duplication is real and unintentional, and (b) nobody else has already fixed it**, not inferred from a class name appearing in two places, a directory structure that looks asymmetric, or this document's own prior text. This cost nothing to fix here since it was caught before any code moved, but it's exactly why Item 2's file paths, line numbers, and dependency note above were re-verified against current disk state rather than left as originally written — and it's exactly what Item 1a's own execution demonstrated in miniature: a first grep pass (src-only) missed a live test caller of `check_hard_stops` and a live dependency of `ComplexityPolicy`, caught only by re-running the grep against the whole repo immediately before deleting anything. Item 5 should get the same direct check (confirm `config/agent_registry.yaml`'s current `enabled` flags and whether load-testing has already happened) immediately before anyone starts it, not just at planning time.

**Separately, unrelated to this plan's own content:** while executing Item 1a, `git status` showed an active, unrelated merge conflict in progress across `src/seleric_swarm/agents/diagnostic/*`, `swarm/artifacts.py`, and `agents/skeptic/swarm_bridge.py` (several files marked `UU`/unmerged). This consolidation work did not touch any of those files and the conflict was left alone; noting it here only because this doc's own file briefly disappeared from disk during the same session (it was never git-tracked, so the merge itself isn't the direct cause) and had to be recreated from held content. Whoever owns that merge should resolve it independently of this plan.

## References

- [6 Multi-Agent Orchestration Patterns for Production (2026)](https://beam.ai/agentic-insights/multi-agent-orchestration-patterns-production)
- [Multi-Agent in Production in 2026: What Actually Survived](https://medium.com/@Micheal-Lanham/multi-agent-in-production-in-2026-what-actually-survived-f86de8bb1cd1)
- [Best Multi-agent Orchestration Frameworks in 2026](https://www.truefoundry.com/blog/multi-agent-orchestration-frameworks)
- [Legacy Data Pipeline Modernization Without Rewriting Everything](https://simorconsulting.com/blog/legacy-data-pipeline-modernization-without-rewriting-everything/)
- [The Right Way to Modernize a Legacy Data Pipeline Before You Layer AI On Top](https://www.inworkglobal.com/blog/the-right-way-to-modernize-a-legacy-data-pipeline-before-you-layer-ai-)
- [Engineering Data Migration: The Complete Guide to Moving from Legacy Systems](https://www.getleo.ai/blog/engineering-data-migration-legacy-systems)
- Code map: `diagrams/current_architecture.mmd` (this repo, generated by direct source-code scan)
- `docs/features/lookup-v1-retirement.md` (this repo — the completed record for Item 1, including the "Dead / parallel stacks" table that seeded Item 1a)
