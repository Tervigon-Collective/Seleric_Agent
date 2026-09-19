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

## Original state (as found, 2026-09-16/17 — superseded by "Status as of Sprint 3 close" below)

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
mismatch recorded but not reproduced on retry.

## Status as of Sprint 3 close (2026-09-19) — see `TASK_SHEET.md` for evidence

- **Done (Sprint 2)**: `services/measure.py::resolve_measure()`/
  `measure_keywords_overlap()` — the keyword-overlap catalogue-search
  fallback, bug #8's actual root cause — deleted outright, zero remaining
  callers (whole-repo grep). Every fetch path resolves a metric via
  `MetricDefinition.catalogue_metric` directly and calls
  `toolsets/semantic.py::raw_query_metric()`, the one shared no-heuristic
  MCP-call primitive both the new toolset and the legacy providers use.
- **Done (Sprint 2, extract-wrap-delete)**: `HybridMcpDataProvider` renamed
  `McpDataProvider` (class kept as a thin `DomainAgent` adapter, not
  deleted outright — its heuristic is gone, not the class); `fetch_series()`
  body extracted to `toolsets/semantic.py::query_metric_series()`, with the
  provider method now a thin wrapper (closing the "no `SemanticToolset`
  equivalent" gap this section originally flagged — `agents/diagnostic/
  swarm_bridge.py`'s DoWhy causal specialist reaches it transitively);
  `business_state/series.py::fetch_series()` kept as a brand-scoped adapter
  over `raw_query_metric()` (`BusinessStateService.get_metric_state()`'s
  live, proven-nonempty caller graph — folding it fully into `query_metrics()`
  was evaluated and explicitly deferred, not silently dropped); `_query_windows`
  renamed `_observation_windows` (pure date-range shaping, not an MCP call —
  still live, disposition of the rest of that module tracked separately,
  Sprint 5).
- **Done (Sprint 3)**: `coordinator/catalogue_grounding.py`'s heuristic
  functions (`dimensions_in_query()`, `apply_catalogue_grain()`,
  `hints_from_catalogue()`, `ground_live_grain()`, `_GENERIC_DIM_TOKENS` and
  friends) deleted outright — 671 lines down to 322. The one live caller
  found afterward (`agents/coordinator.py`'s `lookup_v1` classify path,
  which had regressed to a hardcoded empty `resolved_dimensions`) was fixed
  2026-09-19 with `resolve_grain_for_metrics()` — grain now resolves via the
  live `catalogue_resolve_dimension`/`catalogue_resolve_term` resolver only,
  corroborated against the canonical metric's own `supported_dimensions`.
  `breakdown_from_query`/`pick_grain`/`query_has_grain_intent` remain, still
  used by `agents/intelligence/observer.py`'s own grain path — not a
  heuristic in the retired sense (no query-vs-name keyword matching).
- **Resolved without deletion (Sprint 3)**: `services/metrics.py::MetricRegistry`/
  `MetricSemanticsRegistry` — already catalogue-first in practice
  (`bind_catalogue()` makes the live catalogue authoritative once warm;
  YAML is a cold-start/exception overlay only). Kept as a class by explicit
  user decision — ~15 live callers (swarm_v2's classifier, diagnostic
  pipeline, skeptic) have no isolated test harness for a full rewrite. Not
  carried to Sprint 5.
- **Done (Sprint 4, 2026-09-19)**: `ProviderRegistry` deletion — see
  `SPRINT_PLAN.md`/`TASK_SHEET.md` Sprint 4 Profile B for the full
  disposition (it was a config-driven anomaly-strategy selector, unrelated
  to the MCP fetch-path duplication this section otherwise tracks; its two
  shipped overrides are now hardcoded directly in
  `swarm/providers/provider_selection.py`).

## Builds

- `toolsets/semantic.py` — `SemanticToolset`: `search_semantics()`,
  `get_metric_definition()`, `query_metrics()`, `drilldown()`. Thin
  wrappers over `mcp__seleric-mcp__catalogue_*` / `metrics_query` /
  `metrics_drilldown`. This is the **only** normal path allowed to fetch
  numeric business data (non-negotiable rule 5 in the overview).
  **Done**: implemented in `src/seleric_swarm/toolsets/semantic.py` (Sprint 1),
  wired through the existing `MCPGateway`/`services/mcp_query.py` (reused,
  not rebuilt). `drilldown()` runs the live `metrics_query` → `metrics_drilldown`
  two-call sequence (the real tool requires a parent `query_id`; the frozen
  signature hides that bookkeeping from the agent). All three legacy call
  sites now route through it (`raw_query_metric()`/`query_metric_series()`,
  Sprint 2 consolidation — see status section above); still not wired into
  a real agent loop, since no such loop exists to run traffic yet (Sprint 4).
  Currently authorizes MCP calls under the existing `observer_agent`
  identity (see `TASK_SHEET.md`) since `config/agent_registry.yaml`'s
  per-agent allowlist is itself retired by this migration and isn't the
  right place to add a new entry for the future single-agent identity.
- `toolsets/actions.py` — `ActionToolset`: `propose_action()`,
  `validate()`, `preview()`, `commit_action()`, wrapping
  `mcp__seleric-mcp__actions_propose/commit/status` and the Meta write
  tools, always through the propose→confirm→commit sequence (non-negotiable
  rule 13). **Google Ads action execution is not part of this program's
  requirements** — the live MCP action catalogue only ever needed to cover
  Meta (`pause_meta_ad`); there is no Google Ads action contract to build
  against and none is planned. **Done 2026-09-18**: the remote transport
  registers all four action endpoints, a dedicated `v3_agent` allowlist
  prevents legacy read agents from gaining writes, and the adapter keeps
  confirmation tokens out of model-visible provenance.
- `semantic/cube_client.py` / `semantic/discovery.py` — **not built, not
  planned (Sprint 3 finding)**. `toolsets/semantic.py::search_semantics()`
  (server-side `catalogue_search_metrics` embedding search) and
  `get_metric_definition()` already do what these two files would have
  built; a local `discovery.py` vector index would duplicate server-side
  search — exactly the "check before building a second vector index"
  ponytail rung 2 this brief's own text warned against.

## Depends on

- Profile A's `SelericDeps`/`ToolResult` contract (frozen Sprint 0).
- Direct confirmation from the `seleric-mcp` server's own repo/docs that
  `catalogue_search_metrics`, `metrics_query`, `metrics_drilldown`,
  `actions_propose/commit/status` are production-ready, not stubs — this is
  a Sprint 1 spike, not an assumption.

## Key risks (as planned; see Status section above for what actually happened)

- The "one transient anomaly" in the characterization suite (a 2.6x value
  mismatch that didn't reproduce) meant the merge's safety net wasn't fully
  trustworthy from one run alone. **Resolved by explicit override, not by
  waiting out the re-run plan**: the user directed deletion to proceed on
  the existing green run ("delete it, we are almost rebuilding this"),
  recorded in `TASK_SHEET.md` rather than silently skipped. The live
  `tests/replay/` suite (40/40) was re-run again 2026-09-19 during the
  grain-resolution fix and stayed clean.
- `metric.net_profit`'s registry `supported_dimensions: [channel]` returning
  zero rows live was a real finding — no further action recorded against it
  in this profile; not re-verified this pass.
- Deleting `catalogue_grounding.py`'s heuristics removed a safety net for
  legacy/degraded LLM behavior by design. The exact bug #8/#2 regression
  cases now have dedicated tests against the new path
  (`tests/unit/test_semantic_toolset_bug_regressions.py`), closing exit
  criteria 2 and 3 below.

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

## Exit criteria — all met, Sprint 3 close 2026-09-19

1. **Met, by override not by the 3-day plan.** `tests/replay/test_data_access_characterization.py`
   passed 7/7 on the day of deletion; the original "≥3 separate days" bar
   was explicitly waived by the user rather than satisfied literally (see
   Key risks above). Re-confirmed clean again 2026-09-19
   (`tests/replay/` 40/40 live).
2. **Met.** Bug #8's original repro does not reproduce through the new
   catalogue-validate path —
   `tests/unit/test_semantic_toolset_bug_regressions.py::test_dimensions_are_passed_through_verbatim_no_keyword_matching`
   and siblings.
3. **Met.** Bug #2's original repro does not reproduce — same file,
   `test_two_spellings_of_same_metric_each_produce_their_own_artifact_no_silent_remap`.
4. Not verified this pass whether `docs/46_ARCHITECTURE_CONSOLIDATION_PLAN.md`
   Item 2 itself was marked done pointing here — check that file directly if
   it matters for external tracking; it is not re-derived from source the
   way the other three criteria are.
