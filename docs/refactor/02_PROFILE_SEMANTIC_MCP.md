# Profile B — Semantic & MCP Consolidation

Owns everything between the agent's `SemanticToolset`/`ActionToolset` and
the live `seleric-mcp` server. Directly absorbs
`docs/46_ARCHITECTURE_CONSOLIDATION_PLAN.md` Item 2 — reuse its
characterization suite, don't redo it.

## Mission

Collapse this repo's **three** independent in-process MCP-query-shaping
paths into calls through the already-live `seleric-mcp` tools
(`metrics_query`, `metrics_drilldown`, `catalogue_search_metrics`,
`catalogue_get_metric`, `catalogue_resolve_term`/`resolve_dimension`/
`resolve_brand`, `insights_explain`, `actions_propose/commit/status`), and
delete this repo's own heuristic metric/dimension-resolution layer in
favor of that server's catalogue.

## Current state (confirmed by direct read, 2026-09-16/17)

Three divergent fetch implementations, all bottoming out in the same two
shared primitives (`resolve_measure()`, `call_metrics_query()`) but each
independently deciding time-range/grain/dimension handling above that:

1. `swarm/providers/mcp_data.py::HybridMcpDataProvider.fetch()` — used by
   every `DomainAgent.observe()`.
2. `HybridMcpDataProvider.fetch_series()` — same class, second independent
   implementation.
3. `services/business_state/series.py::fetch_series()` — used by
   `BusinessStateService.get_metric_state()` (in turn used by
   `lookup_fast_path.py` and anomaly-history lookups). Confirmed structural
   gap: its `actual` is the *last day's point*, not a period sum, and it has
   no dimension/grain support at all (`facade.py` reads only `brand_id` out
   of `StateRequest.dimensions`).
4. A related fourth window-shaping function,
   `agents/intelligence/observer.py`'s `_query_windows`, not yet folded
   into the characterization suite.

`tests/replay/test_data_access_characterization.py` (7/7 passing as of
2026-09-16) already proved: single-day case consistent across paths;
multi-day case has the "last point vs. sum" divergence above (not a bug —
by design difference, previously undocumented); one transient live-data
mismatch recorded but not reproduced on retry (real risk for the merge
step's safety net — re-run this suite across a few different days before
trusting it as sufficient, per the consolidation plan's own note).

## Retires

- **Done (Sprint 2, 2026-09-18)**: `services/measure.py::resolve_measure()`/
  `measure_keywords_overlap()` — the keyword-overlap catalogue-search
  fallback, bug #8's actual root cause — deleted outright, zero remaining
  callers (whole-repo grep). Every fetch path (including the two below,
  which still exist as classes) now resolves a metric via
  `MetricDefinition.catalogue_metric` directly and calls
  `toolsets/semantic.py::raw_query_metric()`, the one shared no-heuristic
  MCP-call primitive both the new toolset and the legacy providers use.
- **Not yet deleted, but internally consolidated**:
  `swarm/providers/mcp_data.py::HybridMcpDataProvider` (both `fetch()` and
  `fetch_series()`) and `services/business_state/series.py::fetch_series()`
  — no longer contain the heuristic (see above), but still exist as the live
  call path since nothing yet replaces them end-to-end at the call-site
  level (in particular `fetch_series()`'s multi-metric pandas-DataFrame
  output feeding `agents/diagnostic/swarm_bridge.py`'s DoWhy causal
  specialist has no `SemanticToolset` equivalent yet — a Sprint 3 gap, not a
  Sprint 2 one). Deleting these classes outright is still gated on building
  that replacement.
  `services/business_state/facade.py::BusinessStateService` — if its "last
  day's point" semantics is actually needed somewhere, it becomes an
  explicit `query_metrics(as_of=..., grain="day")` call, not a separate code
  path — not yet done.
- `agents/intelligence/observer.py::_query_windows` — pure date-range
  shaping, not itself an MCP call; still live, becomes dead code once
  `HybridMcpDataProvider` itself is deleted.
- `coordinator/catalogue_grounding.py` in full —
  `dimensions_in_query()`/`apply_catalogue_grain()`/`hints_from_catalogue()`/
  `ground_live_grain()`/`_GENERIC_DIM_TOKENS` and friends. This is the exact
  heuristic layer `docs/BUG_SHEET.md` bugs #8 and the original
  `docs/TASK_SHEET.md` plan already identified as the root cause of a whole
  bug class (false-positive dimension matching on generic tokens). The
  fix under this plan isn't "widen the exclusion set again" — it's "the LLM
  states the metric/dimension id directly against the live catalogue
  (already true per `docs/TASK_SHEET.md` Phase 1/2), and `seleric-mcp`'s own
  `catalogue_resolve_dimension`/`catalogue_resolve_term` validates it — no
  local token-matching heuristic at all."
- `services/metrics.py::MetricRegistry`, `MetricSemanticsRegistry` (as
  standalone in-repo registries) — the live catalogue behind `seleric-mcp`
  is now the single source; this repo keeps at most a thin typed wrapper
  for prompt-building (`catalog_prompt()`'s job), not a parallel registry
  with its own YAML.
- `ProviderRegistry` — no longer needed once there's one provider (the MCP
  client), not several swappable ones.

## Builds

- `toolsets/semantic.py` — `SemanticToolset`: `search_semantics()`,
  `get_metric_definition()`, `query_metrics()`, `drilldown()`. Thin
  wrappers over `mcp__seleric-mcp__catalogue_*` / `metrics_query` /
  `metrics_drilldown`. This is the **only** normal path allowed to fetch
  numeric business data (non-negotiable rule 5 in the overview).
  **v0 done (Sprint 1, 2026-09-18)**: implemented in
  `src/seleric_swarm/toolsets/semantic.py`, wired through the existing
  `MCPGateway`/`services/mcp_query.py` (reused, not rebuilt). `drilldown()`
  runs the live `metrics_query` → `metrics_drilldown` two-call sequence
  (the real tool requires a parent `query_id`; the frozen signature hides
  that bookkeeping from the agent). Not yet wired into an actual agent loop
  or into the three legacy call sites — that's Sprint 2 consolidation.
  Currently authorizes MCP calls under the existing `observer_agent`
  identity (see `TASK_SHEET.md`) since `config/agent_registry.yaml`'s
  per-agent allowlist is itself retired by this migration and isn't the
  right place to add a new entry for the future single-agent identity.
- `toolsets/actions.py` — `ActionToolset`: `propose_action()`,
  `validate()`, `preview()`, `commit_action()`, wrapping
  `mcp__seleric-mcp__actions_propose/commit/status` and the Meta/Google Ads
  write tools, always through the propose→confirm→commit sequence
  (non-negotiable rule 13).
- `semantic/cube_client.py` — typed wrapper if a thin local abstraction is
  still useful for retries/tracing; does **not** talk to Cube/ClickHouse
  directly (confirmed invariant, `new.mmd`).
- `semantic/discovery.py` — semantic search over Cube metadata (spec §12),
  backed by `catalogue_search_metrics` if that tool already does embedding
  search server-side (check before building a second vector index — ponytail
  rung 2: reuse before rebuild).

## Depends on

- Profile A's `SelericDeps`/`ToolResult` contract (frozen Sprint 0).
- Direct confirmation from the `seleric-mcp` server's own repo/docs that
  `catalogue_search_metrics`, `metrics_query`, `metrics_drilldown`,
  `actions_propose/commit/status` are production-ready, not stubs — this is
  a Sprint 1 spike, not an assumption.

## Key risks

- The "one transient anomaly" in the characterization suite (a 2.6x value
  mismatch that didn't reproduce) means the merge's safety net isn't fully
  trustworthy yet from one run. Re-run the suite across several different
  days/times before treating step 4 (re-run suite against refactored code)
  as sufficient evidence of parity.
- `metric.net_profit`'s registry `supported_dimensions: [channel]` returning
  zero rows live (already found, consolidation plan Item 2 step 1) means
  the live catalogue's declared capabilities aren't fully trustworthy either
  — don't just trust `catalogue_get_metric`'s metadata at face value in
  Sprint 2; spot-check against a live `query_metrics` call.
- Deleting `catalogue_grounding.py`'s heuristics removes a safety net for
  legacy/degraded LLM behavior (spec doesn't reintroduce keyword matching
  by design) — Sprint 2's exit criteria must include the exact regression
  cases from bug #8's fix, run against the new LLM-direct + MCP-validate
  path, not just a "tests pass" check.

## Cross-profile note (added 2026-09-18)

Two things Profile C depends on B for, recorded here because C's brief was
previously written as if it owned them:

- **Bugs #2 and #8 are B's, not C's.** Both root-cause in modules B retires
  (`lookup_fast_path.py`, `catalogue_grounding.py`). C's exit criteria used
  to demand its own passing test for each; they now cite criteria 2 and 3
  below instead. C reviews and signs off; B owns the gate.
- **B is the writer of `EvidenceArtifact.grain`.** Profile C's analytics
  precondition (`CONTRACTS.md` amendment A1.2 — reject an evidence set whose
  grain doesn't match the baseline it's compared against, with
  `error_code="EVIDENCE_GRAIN_MISMATCH"`) is only as good as that field
  being set correctly at the source. `SemanticToolset.query_metrics()` must
  set `grain` from what it actually asked Cube for, never a default or an
  inference — a wrong-but-consistent grain would pass C's precondition and
  reproduce bug #14 silently.

## Exit criteria

1. `tests/replay/test_data_access_characterization.py`-equivalent suite
   passes with the new single fetch path against the same fixtures the old
   three paths were compared on, re-run on ≥3 separate days.
2. Bug #8's original repro ("get per day data" misclassified as
   `session_day_of_week`) does not reproduce through the new
   catalogue-validate path.
3. Bug #2's original repro (metric-ID canonicalization inconsistency)
   does not reproduce.
4. `docs/46_ARCHITECTURE_CONSOLIDATION_PLAN.md` Item 2 marked done, pointing
   here.
