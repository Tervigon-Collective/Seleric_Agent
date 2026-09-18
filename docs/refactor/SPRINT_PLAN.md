# Seleric V3 — Sprint Plan

2-week sprints, three profiles running in parallel with sync gates. A
profile that clears its gate early moves to its next sprint's tasks; it
does not wait idle for the others unless the next task explicitly depends
on a cross-profile contract (marked below).

Profile briefs: `01_PROFILE_RUNTIME.md` (A), `02_PROFILE_SEMANTIC_MCP.md`
(B), `03_PROFILE_CAPABILITIES.md` (C). Frozen contracts: `CONTRACTS.md`.

> **Revised 2026-09-18** after a design review of Profile C against source.
> Sprint 0 is closed and its contracts are frozen, so the review's findings
> land as **proposed amendment A1** in `CONTRACTS.md` rather than as Sprint 0
> edits. A1 sign-off is now a joint task at the head of Sprint 1 and
> **blocks C's Sprint 2 causal work** (bug #6's escalating-widening fix has
> no home in the frozen two-function causal surface). Profile C's Sprint 1/2
> tasks below are also re-scoped: only two of its six frozen analytics
> functions are ports, the rest are greenfield.

## Sprint 0 — Contracts (all three profiles, joint)

Nothing else starts until this is frozen — B and C both build against A's
contract, and A's validator needs C's evidence-classification vocabulary.

- [x] Freeze `SelericDeps` dataclass shape (A drafts, B/C review). Done —
      `docs/refactor/CONTRACTS.md` §1, materialized as code in
      `src/seleric_swarm/agent/contracts.py`.
- [x] Freeze `ToolResult` envelope (A drafts, B/C review). Done —
      `docs/refactor/CONTRACTS.md` §2, same module.
- [x] Freeze `EvidenceArtifact`/`Finding`/`CausalArtifact`/`PredictionArtifact`
      schemas (A owns the store, C owns causal/prediction field needs). Done —
      `docs/refactor/CONTRACTS.md` §3, same module.
- [x] Freeze the seven toolset names + tool signatures (function names,
      params, return types — not implementations). Done —
      `docs/refactor/CONTRACTS.md` §4.
- [x] Spike: confirm `seleric-mcp`'s `catalogue_search_metrics`,
      `metrics_query`, `metrics_drilldown`, `actions_propose/commit/status`
      are production-ready (Profile B, blocks its Sprint 1). Done — all
      confirmed live and production-ready 2026-09-18 (see `TASK_SHEET.md`);
      one unrelated Cube schema bug found on `net_sales_all_channels`,
      flagged separately, not blocking.
- [x] Capture pre-migration baseline: run the full existing test suite +
      the two live-trace repros from `docs/TASK_SHEET.md` against current
      swarm_v2, record pass/fail counts as the parity bar every profile's
      exit criteria compares against. Done — 731 passed/1 failed/4 skipped,
      2026-09-18 (see `TASK_SHEET.md`).
- [x] Add superseded-pointer note to
      `docs/46_ARCHITECTURE_CONSOLIDATION_PLAN.md` (done as part of this
      planning pass, see that file's top).

**Status: CLOSED 2026-09-18** — all tasks Done, see `TASK_SHEET.md` for
per-task verification evidence. Contracts frozen in `CONTRACTS.md`; post-freeze
changes go through that file's amendment process, not by editing this sprint.

**Gate:** contracts merged and reviewed by all three profiles before any
Sprint 1 task starts.

## Sprint 1

**Joint — amendment A1 sign-off (new, head of sprint)**
- [ ] Review `CONTRACTS.md` "Proposed Amendment A1"; A, B and C each sign off
      or counter-propose. A1.1 (causal escalation surface) blocks C's Sprint
      2 — resolve it first, the rest can follow within the sprint.
- [ ] Spike: add `pydantic-ai` to `pyproject.toml` and confirm the real
      toolset/`RunContext` API matches the signatures frozen in `CONTRACTS.md`
      §4 (A1.6). **Partial:** `pydantic-ai-slim>=2.45` is installed and the
      stub agent builds against it (Profile A Sprint 1); formal three-profile
      sign-off that frozen §4 matches the real API is still open.
- [ ] Decide owner for the `CAUSALLY_SUPPORTED_UNDER_ASSUMPTIONS` →
      `CAUSALLY_SUPPORTED` migration, including
      `config/diagnostic_policies.yaml:24` (A1.4).

**A — Runtime scaffolding**
- [x] `agent/agent.py`, `agent/dependencies.py`, `agent/output.py` skeletons.
      Done — `src/seleric_swarm/agent/{agent,dependencies,output,artifacts,instructions}.py`;
      `pydantic-ai-slim>=2.45` added to `pyproject.toml` (covers A1.6 spike
      in practice — slim variant, not full `pydantic-ai`).
- [x] `api/missions.py` — `POST /v1/missions` wired to a stub agent (no
      real toolsets yet) behind a feature flag, 0% traffic. Done —
      standalone `APIRouter`, not mounted into live `main.py`;
      `settings.v3_agent_enabled` default `False`.
- [x] `state/missions.py`, `state/artifacts.py` — Mission/Artifact stores.
      Done — `InMemoryMissionStore`/`InMemoryArtifactStore`.
- [x] `evals/` harness scaffolding + golden dataset seeded from
      `eval/datasets/lookup_commerce.jsonl` and `tests/replay/` fixtures.
      Done (loader only) — `evals/golden_dataset.py`; replay extraction +
      scorer deferred (stub agent can't be scored meaningfully yet).

**B — Semantic toolset v0**
- [x] `toolsets/semantic.py` — `query_metrics()`/`drilldown()` wrapping
      live `seleric-mcp` tools, no local heuristics. Done —
      `src/seleric_swarm/toolsets/semantic.py`, 10 passing unit/contract
      tests (orphaned `agent/contracts.py` deleted in Sprint 2; canonical
      schemas live in `agent/{dependencies,output,artifacts}.py`).
- [x] Re-run `tests/replay/test_data_access_characterization.py` against
      the new toolset (not yet wired into the agent) to establish it
      matches at least one of the three legacy paths before consolidation
      begins in Sprint 2. Done — `tests/replay/test_semantic_toolset_characterization.py`,
      live parity confirmed against `HybridMcpDataProvider.fetch()` for
      `metric.units_sold`/`units_sold`, 2026-09-18.

**C — Analytics toolset v0 (re-scoped)**

Step 1 of the extract-wrap-delete approach (`03_PROFILE_CAPABILITIES.md`
§10). Extraction does not depend on A1 landing, so it starts immediately.

- [x] **Extract in place**: pull the comparison math out of
      `swarm/specialists/observer.py::_post_comparison_deltas` and the
      detection path out of `swarm/specialists/anomaly.py` into pure
      functions. Done — `analytics/comparison.py::period_deltas` + existing
      `detectors.py::robust_zscore`.
- [x] **Wrap**: `toolsets/analytics.py::compare_periods`,
      `detect_anomalies` as thin adapters. Done —
      `src/seleric_swarm/toolsets/analytics.py`.
- [x] Implement the A1.2 grain precondition in both functions +
      `EVIDENCE_GRAIN_MISMATCH`. Do **not** port `anomaly.py`'s
      sum/normalize fallback branch. Done —
      `analytics/grain.py::validate_grain_set()`.
- [x] Regression tests for bug #14, both halves: per-day evidence reaches
      the detector un-normalized, **and** a grain-mismatched evidence set is
      rejected rather than normalized. Done —
      `tests/unit/test_analytics_toolset.py` (16 passed).
- [x] Note for planning: the other four frozen analytics functions
      (`contribution_analysis`, `segment_decomposition`,
      `funnel_decomposition`, `cohort_analysis`) have no implementation in
      `src/` — they are greenfield and move to Sprint 4's additive track,
      not this sprint.

**Status (implementation work):** Profile A/B/C Sprint 1 execution Done
2026-09-18 — see `TASK_SHEET.md`. Joint A1 sign-off (A1.1 / A1.4 owner /
formal A1.6 review) still open and still gates C's Sprint 2 causal work.

**Gate:** two blocking dependencies, otherwise open.
1. **Amendment A1.1 must be signed off before C's Sprint 2 starts** — the
   frozen Causal surface has no home for bug #6's escalating-widening fix,
   so C's causal port cannot begin until the escalation parameter is agreed.
   This remains the program's hardest current blocker.
2. ~~B's consolidation (Sprint 2) needs its own Sprint 1 spike result.~~
   Cleared — B Sprint 1 characterization done; B Sprint 2 consolidation
   largely executed (see below).

Everything else in Sprint 2 is startable without another profile's Sprint 1
output. The rest of A1 (A1.2–A1.6) should also land this sprint but only
A1.1 gates a downstream sprint. A1.2 is already implemented in C's analytics
toolset ahead of formal A1 sign-off.

## Sprint 2

**A — Evidence Validator + execution limits**
- [x] `agent/validation.py::EvidenceValidator` — orchestration slot only
      (bounded 1-revision retry loop as frozen, pending the joint decision
      below); content checks land once C's classification vocabulary is
      ready (parallel work, integrate end of sprint). Done —
      `agent/validation.py` + `tests/unit/test_v3_validation.py`.
- [x] Real execution-limit enforcement (`max_tool_calls`, `max_cube_queries`,
      etc.) — first time any budget concept in this system actually rejects
      a mission since it was disabled. Done — `agent/limits.py::ExecutionBudgetTracker`.
- [x] Test: synthetic over-budget mission is actually rejected. Done —
      `tests/unit/test_v3_execution_limits.py`.

**B — Consolidate the three fetch paths (46_...Item 2 execution)**
- [x] Diff `HybridMcpDataProvider.fetch()`/`fetch_series()` and
      `business_state/series.py::fetch_series()` line by line — confirm
      which behaviors are intentional differences (documented: last-point
      vs. sum) vs. bugs. Done — see `TASK_SHEET.md` for the full diff,
      including a previously-undocumented divergence found: the two
      same-named `fetch_series` functions disagree on out-of-range-window
      behavior (silent truncate vs. silent `None`).
- [x] Route all three call sites (`DomainAgent.observe()`,
      `BusinessStateService.get_metric_state()` callers, `lookup_fast_path.py`)
      through the new `SemanticToolset.query_metrics()`. Done, per explicit
      user override of the risk flagged above ("we need to do this... even
      if it is breaking for now") — see `TASK_SHEET.md`. Extracted
      `toolsets/semantic.py::raw_query_metric()` as the one shared
      no-heuristic call every fetch path now uses; `_resolve_measure()` in
      both legacy providers now reads `MetricDefinition.catalogue_metric`
      directly (no keyword-search fallback); `services/measure.py::
      resolve_measure()`/`measure_keywords_overlap()` deleted outright, zero
      remaining callers. `lookup_fast_path.py` needed no direct edit (routes
      transitively). Full regression + live characterization re-run clean
      after the change (811 passed/1 pre-existing failure, 45/45 live).
- [x] Re-run characterization suite ≥3 separate days before trusting it as
      the safety net for the coming deletion. Broadened to all 3 legacy
      `_CASES` metrics, re-run clean 3 times across this session (10/10 each
      time, including once after the routing change above) — real evidence,
      but not literally 3 separate calendar days; recorded honestly as a
      remaining gap, see `TASK_SHEET.md`.
- [ ] Delete `HybridMcpDataProvider`, `business_state/series.py::fetch_series`,
      `agents/intelligence/observer.py::_query_windows` once the above
      passes. Partial: the heuristic they depended on
      (`resolve_measure`/`measure_keywords_overlap`) is deleted; the
      classes/functions themselves are now thin heuristic-free wrappers, not
      dead code — deleting them outright still needs something to replace
      them at the call-site level, which is Sprint 3 work.

**C — Causal toolset v0 + evidence classification** *(blocked on A1.1)*
- [ ] `toolsets/causal.py` + `causal/service.py` — DoWhy wiring extracted in
      place from `agents/diagnostic/*` then wrapped (same three-step pattern
      as Sprint 1). **EconML is not a dependency of this repo** — there is no
      EconML wiring to port; adding it is a separate scope decision.
- [ ] Evidence-classification vocabulary finalized and handed to A, applying
      A1.4's migration mapping across `src/` **and**
      `config/diagnostic_policies.yaml:24`.
- [ ] Bug #6 regression against the new `search_breadth` escalation (A1.1):
      a wider retry genuinely searches a larger space, not a byte-identical
      re-run.
- [ ] Bug #7: confirm still intermittent, not newly deterministic, **and**
      that the #6 widening — its only documented mitigation — survives the
      `max_validation_revisions = 1` decision below.
- [ ] Bug #13: explicit disposition (fix or tracked ticket). Note the
      self-reference: #13 *is* the fixture path and the real path silently
      diverging, and C's own port is fixture-validated.
- [ ] **Policy-gate disposition (new)**: port the conditions in the eight
      `policy()` implementations into tool preconditions returning
      `INSUFFICIENT_EVIDENCE` (A1.3), and decide what happens to the five
      `config/*_policies.yaml` files. Nothing currently owns them.

**Joint decision (A + C), end of sprint**
- [ ] `max_validation_revisions = 1`: confirm or change. One revision means
      one widening step and no escalation ladder, and the old system's
      stall-detector (the part bug #6 credits as working) never gets to
      fire. Record the answer either way — including what the loop does with
      a STRONG-trust + REVISE verdict when it has exactly one revision.
- [ ] Record the skeptic→validator change as a decision: cross-agent
      adversarial challenge becomes in-context self-review. That is a change
      in kind, not a consolidation (`03_PROFILE_CAPABILITIES.md` §4).

**Gate (end of sprint):** A integrates C's evidence-classification
vocabulary into the validator's content checks (applying A1.4's migration
mapping, including the `config/diagnostic_policies.yaml` value). B's
deletion only proceeds if its 3-day-repeated characterization suite passes
clean. C's causal work does not start at all until A1.1 is signed off, per
Sprint 1's gate. The `max_validation_revisions` decision is recorded before
the sprint closes — either answer is fine, silence is not.

## Sprint 3

**A — Mission Service + durable execution decision**
- [x] Confirm (data, not assumption) whether any current mission needs
      Temporal's durability — if no, defer Temporal, ship synchronous-only.
      Done — timed real missions against live seleric-mcp/Cube (fake LLM,
      zero LLM cost): lookup 1.45s, full diagnostic (real DoWhy) 7.25s, both
      well inside the 120s runtime budget. Deferred Temporal. See `TASK_SHEET.md`.
- [x] `MissionQueryCache` wired in, dedupes repeated evidence fetches
      within one mission. Done (mechanism) — `state/cache.py::MissionQueryCache`;
      not yet called from a real toolset (Profile B's `query_metrics()` is the
      pending call site).
- [x] `observability/traces.py` — one trace per mission, OTel/Logfire. Done —
      `mission_trace()`, reuses the existing `configure_opentelemetry()` wiring.

**B — Catalogue heuristic retirement**
- [ ] Delete `coordinator/catalogue_grounding.py`'s heuristic functions
      (`dimensions_in_query`, `apply_catalogue_grain`, etc.) once
      `SemanticToolset` is the only fetch path and validated against bug #8's
      repro case.
- [ ] Retire `MetricRegistry`/`MetricSemanticsRegistry` as standalone
      registries — replace `catalog_prompt()`'s data source with a live
      `seleric-mcp` catalogue call.
- [ ] `ActionToolset` — propose/validate/preview/confirm/commit wired to
      `actions_propose/commit/status` + Meta/Google Ads write tools.

**C — Model/Skeptic-logic port**
- [ ] `toolsets/models.py` + `models/service.py`. **The registry question is
      already answered** — `agents/prediction/swarm_bridge.py:61-62` builds an
      `InMemoryModelRegistry` from `scenario["forecast_truth"]`, and
      `config/model_registry.yaml` does not exist (only a 573 B
      `.example.yaml`, both entries `status: candidate`). There are no
      approved production models, so this is greenfield on a fixture-driven
      template service, not a port. Re-confirm priority with the user before
      building it.
- [ ] `EvidenceValidator` content checks — port trust-score/verdict-engine
      logic from `agents/skeptic/*`, preserving **two independent signals**
      (`score_trust` and `decide_verdict` look at different things); do not
      merge into one score.
- [ ] Reproduce bug #12's STRONG-trust + REVISE state and record the loop's
      behavior under the cap decided in Sprint 2.

**C — Behavioral parity harness (new, runs alongside)**
- [ ] Run the replay missions through both the old specialists and the new
      toolsets and diff the findings: same anomalies flagged, same hypotheses
      surfaced, same evidence classification. Every divergence gets a written
      explanation. This profile had no behavioral parity criterion at all
      while being the one the plan calls highest-behavioral-risk — schema
      completeness (metadata present on 100% of artifacts) is a floor a
      required Pydantic field satisfies trivially, not a parity gate.

**Gate:** B's Sprint 3 deletions require Sprint 2's characterization suite
to have already passed 3 separate days clean — do not delete on a single
green run.

## Sprint 4

**A — Cutover flag + cost/latency gate (absorbs consolidation-plan Item 5, re-scoped)**
- [ ] Run the replay set (spec §43's queries: observe/detect/diagnose/
      predict/intervene/challenge) through the new agent loop with
      Analytics+Causal+Validator wired. **"Analytics wired" means the two
      ported functions** — `compare_periods` and `detect_anomalies`, with
      their A1.2 grain preconditions — not all six. The four greenfield
      analytics functions lag with Models/Knowledge/Experiments (see C's
      section below) and do not gate the cutover.
- [ ] Record per-mission agent_calls, llm_calls, tokens, wall-clock, cost —
      compare against swarm_v2's Sprint 0 baseline.
- [ ] Tune execution limits until within acceptable cost/latency, same
      iterative-tuning-loop structure as the original Item 5 steps 3-4.
- [ ] Document final chosen limits + measured figures here or a follow-up.
- [ ] Flip cutover flag to a canary percentage once parity + cost gates
      both pass (per profile exit criteria in each brief).

**B — Cleanup**
- [ ] `ProviderRegistry` deletion.
- [ ] Confirm no remaining caller of any deleted module (whole-repo grep,
      not src-only — per the process note in
      `46_ARCHITECTURE_CONSOLIDATION_PLAN.md` about the Item 1a src-only
      grep miss).

**C — Greenfield capability (additive scope, non-blocking)**
- [ ] The four non-port analytics functions: `contribution_analysis`,
      `segment_decomposition`, `funnel_decomposition`, `cohort_analysis`
      (moved here from Sprint 1 — no implementation exists in `src/` to
      port).
- [ ] `toolsets/knowledge.py` + `knowledge/*`.
- [ ] `toolsets/experiments.py` + `experiments/*`.
- [ ] These do not block A's Sprint 4 cutover gate — land after, don't
      hold up the canary flip for genuinely new capability.

**Gate:** Program-level cutover decision. If A's cost/latency figures don't
clear the bar, iterate within Sprint 4 (same tuning-loop pattern as the
original Item 5) before flipping the flag — do not cut over on a failing
cost gate.

## Sprint 5 — Old pipeline deletion (per-subsystem PRs, not a bulk delete)

Only after the canary has run clean for an agreed soak period (pick this
number explicitly before the sprint starts — not defaulted here since it's
a production-risk call, not an engineering one).

- [ ] Delete `coordinator/graph.py` (LangGraph state machine).
- [ ] Delete Blackboard.
- [ ] Delete `LeadershipManager`/`LeadershipController`.
- [ ] Delete `AgentRegistry`, `config/agent_registry.yaml`'s specialist flags.
- [ ] Delete `swarm/domain/` (`base.py` + `configs.py` — one config-driven
      base class) and `agents/domains/*.py` (8 config stubs, ~730 B each).
      Note `funnel_agent` is `enabled: true` in
      `config/agent_registry.yaml:47` — it is not already disabled.
- [ ] Delete `swarm/specialists/*`, `agents/diagnostic/*`,
      `agents/prediction/*`, `agents/strategy/*`, `agents/skeptic/*`
      (each its own reviewed PR, gated on the corresponding toolset already
      running in production for the soak period). `agents/skeptic/registries.py`
      also holds the `CausalGraphRegistry`/`ModelRegistry`/
      `MetricSemanticsRegistry` Protocols that three subsystems import —
      untangle before deleting, it is not a standalone registry file.
- [ ] Disposition `agents/intelligence/observer.py` — the second, 25 KB
      observer. B deletes its `_query_windows` in Sprint 2; the rest has no
      owner.
- [ ] Delete `orchestration/dispatch.py::route_for` and
      `coordinator/lookup_fast_path.py` (only if Sprint 1's cost
      assumption held — recheck before deleting).
- [ ] Update `diagrams/current_architecture.mmd` to reflect the deletion
      (or retire the file with a pointer to `new.mmd`).

**Gate:** Program-level definition of done (overview §8) fully met.
