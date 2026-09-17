# Retiring lookup_v1

**Status:** Phase 1 + 2a + 2b done and live-verified. Both structural blockers to deleting lookup_v1 are now closed; deletion itself (Phase 2) has not been done yet — see "Readiness for actual deletion" below for what's still unverified.

## Background

`orchestration/graph.py` + `orchestration/runner.py` ("lookup_v1") is the legacy mission pipeline that handled every non-diagnostic query (`orchestration/dispatch.py::route_for`). It tracks "is this metric already fetched" via raw string equality between metric ids that can legitimately take different forms (bare catalogue names vs. registry-canonical `metric.` ids). That check never converges when a metric is asked for under both forms, so domain-agent leadership ping-pongs forever on data it already has — root-caused against a live production query, `"how is attribution doing for the last 7 day per channel"`, which hit `HANDOFF_REJECTED` after 4 handoffs despite 44 evidence rows already fetched.

## Phase 1 (done)

- **Bug fixed at the root**: `MetricRegistry.canonical_id()` already existed (used throughout the modern coordinator/swarm_v2 code) but the legacy handoff logic never called it. Wired into `agents/domains/common.py::assigned_work_for_domain` and `orchestration/graph.py::_unresolved_foreign`. Live-verified: the exact failing query went from 4 ping-pong handoffs → `HANDOFF_REJECTED` to 2 clean handoffs → `completed`.
- **New fast path**: `coordinator/lookup_fast_path.py` answers plain lookup queries directly via `BusinessStateService.get_metric_state()` per domain question — no agent-to-agent handoff at all, structurally immune to this bug class (not just patched around it). Wired into `orchestration/dispatch.py` ahead of `run_mission`, which is now an explicit, documented fallback rather than an ambiguous second live route.

## Phase 2a (done)

Extended `lookup_fast_path.py` to also answer **dimensioned/"per channel" breakdown** queries, reusing the already-proven MCP group-by fetch path (`swarm/domain/base.py::observe` → `HybridMcpDataProvider.fetch(dimensions=...)` → `services/mcp_query.py::build_metrics_query_args(dimensions=[...])`) via `build_hybrid_bundle()`, instead of `BusinessStateService` (which only ever returns one scalar per metric — no field exists to hold a per-dimension breakdown). `BusinessStateService`/`MetricState` (Sprints 1-5 of the Business State Service, see `docs/features/business-state-service/`) were deliberately left untouched.

Live-verified against real MCP data (see the mixed grained/ungrained result for `"how is attribution doing for the last 7 day per channel"`):
```
channel_orders: 3.0
google_attribution_net_sales: 2266.8
meta_attribution_net_sales: 0.0
events_per_session: 11.011494252873563
```

**Known gap, not fixed here**: the LLM classifier doesn't reliably attach `grain` for phrasing like "per channel" (confirmed against several natural phrasings — none attached grain). When grain detection misses, the fast path answers with a correct aggregate rather than the requested breakdown — still strictly better than the pre-Phase-1 `HANDOFF_REJECTED` failure, but the breakdown capability built in Phase 2a won't fire until that separate classification gap is addressed.

## Phase 2b (done)

The comparison-intent blocker closed itself: a concurrent process finished the `TimeRange`/`start_b`/`end_b` fix in `coordinator/intake/__init__.py::normalize_query` — `NormalizedQuery.comparison_range` is now genuinely populated for a detected two-period query, and `resolve_mission_time_range` propagates it through. Once that landed:

- Fixed `tests/replay/test_comparison_and_budget.py::test_comparison_computes_deterministic_delta`, which had gone stale against the new `period_a - period_b` sign convention in `observer.py::_comparison_deltas` (was asserting the old `period_b - period_a`/"later minus earlier" convention).
- Added comparison support to `lookup_fast_path.py`: `BusinessStateService.get_metric_state()` called twice per metric (period A, period B), one `.delta` row per metric computed as `period_a - period_b` — same convention, same no-handoff structural safety as the lookup/breakdown paths.
- Live-verified: `"Compare net sales on 2026-08-01 and 2026-08-02"` → `status=completed`, `query_class=comparison`, real period A/B values and a real delta, in ~5.6s via the real dispatcher.
- Spot-checked reliability of `comparison_range` detection across 4 phrasings ("compare X on date1 and date2", "X this week vs last week", "compare X in month1 vs month2") — all 4 correctly populated `comparison_range`. Notably more reliable than the grain-detection gap in Phase 2a.

Comparison + grain together (a dimensioned breakdown across two periods) is still explicitly out of scope — `run_lookup_fast_path` falls back to `run_mission` if a comparison query's `DomainQuestion` also carries `grain`.

## Readiness for actual deletion (Phase 2) — not done yet

Both structural blockers (comparison, dimensioned breakdowns) are closed, but deleting `orchestration/graph.py`/`runner.py` outright hasn't been attempted and shouldn't be without checking these first:

- **Budget/LLM-call-limit enforcement**: `tests/replay/test_comparison_and_budget.py::test_llm_budget_is_enforced` exercises a real feature of `run_mission` (`runtime.settings.max_llm_calls` → `BUDGET_EXCEEDED`) that `lookup_fast_path.py` has no equivalent of at all.
- **Initial-lead-selection parity**: the 3 still-failing tests in `tests/replay/test_leadership_transfer.py`/`test_domain_lookups.py` are about lookup_v1's own `initial_mission_lead` selection for certain multi-domain queries (confirmed pre-existing/live-classification-nondeterminism, unrelated to this retirement work) — they test lookup_v1 behavior directly, not whether the fast path handles those same scenarios equivalently. Not verified either way.
- The classifier's `grain`-detection gap (Phase 2a) means some "per channel"-style queries will keep answering with an aggregate via the fast path once lookup_v1 no longer exists as a fallback — that's a quality gap, not a crash, but worth deciding if it's acceptable before deleting the fallback that currently masks it.

`leadership/manager.py` and `orchestration/state.py::MissionState` are shared with swarm_v2 and are never deleted regardless.

## Dead / parallel stacks (delete candidates with lookup_v1)

These modules implement a **second** planning/budget/leadership path that swarm_v2 does **not** call. Leave them until Phase 2 deletion; do not dual-maintain:

| Module | Role | Live swarm_v2 equivalent |
|---|---|---|
| `coordinator/plane.py` | Legacy control-plane facade (budget + completion) | `coordinator/graph.py` + `governance/*` |
| `coordinator/planning/complexity.py` | Legacy complexity band | `coordinator/intake` complexity_band |
| `coordinator/planning/dag_builder.py` | Legacy DAG builder | `coordinator/planning/mission_planner.py` |
| `coordinator/leadership/lead_selector.py` | Legacy initial lead | intake candidate_domains → `{domain}_agent` |
| `coordinator/governance/budget.py::MissionLimits` | Legacy per-mission ceilings | `MissionBudget` / `check_swarm_budget` |

Changing a limit in only one of `MissionLimits` / `MissionBudget` will drift — prefer `MissionBudget` for any new swarm_v2 work.
