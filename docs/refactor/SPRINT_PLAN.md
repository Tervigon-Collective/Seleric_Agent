# Seleric V3 — Sprint Plan

2-week sprints, three profiles running in parallel with sync gates. A
profile that clears its gate early moves to its next sprint's tasks; it
does not wait idle for the others unless the next task explicitly depends
on a cross-profile contract (marked below).

Profile briefs: `01_PROFILE_RUNTIME.md` (A), `02_PROFILE_SEMANTIC_MCP.md`
(B), `03_PROFILE_CAPABILITIES.md` (C). Frozen contracts: `CONTRACTS.md`.

> **Revised 2026-09-18** after a design review of Profile C against source.
> Sprint 0 is closed and its contracts are frozen, so the review's findings
> landed as **Amendment A1** in `CONTRACTS.md`. **A1 ACCEPTED 2026-09-18**
> (applied into frozen §2/§4). Profile C's Sprint 1/2 tasks below are also
> re-scoped: only two of its six frozen analytics functions are ports, the
> rest are greenfield. Causal Sprint 2 is unblocked.

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
- [x] Review `CONTRACTS.md` "Amendment A1"; A, B and C each sign off.
      **ACCEPTED 2026-09-18** — applied into frozen §2/§4 (`search_breadth`
      on `estimate_effect`; Analytics grain rule; named error codes). See
      `CONTRACTS.md` change log.
- [x] Spike: add `pydantic-ai` to `pyproject.toml` and confirm the real
      toolset/`RunContext` API matches the signatures frozen in `CONTRACTS.md`
      §4 (A1.6). Done — `pydantic-ai-slim>=2.45`; stub agent validates
      `RunContext[SelericDeps]`.
- [x] Decide owner for the `CAUSALLY_SUPPORTED_UNDER_ASSUMPTIONS` →
      `CAUSALLY_SUPPORTED` migration, including
      `config/diagnostic_policies.yaml:24` (A1.4). Done — owner = Profile C;
      migration executes during Causal Sprint 2 (not as part of A1 landing).

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
2026-09-18 — see `TASK_SHEET.md`. **Amendment A1 ACCEPTED 2026-09-18** —
C's Sprint 2 Causal work is unblocked.

**Gate:** cleared for Causal start.
1. ~~Amendment A1.1 must be signed off before C's Sprint 2 starts~~ —
   **ACCEPTED** — `search_breadth` is in frozen §4.
2. ~~B's consolidation (Sprint 2) needs its own Sprint 1 spike result.~~
   Cleared — B Sprint 1 characterization done; B Sprint 2 consolidation
   largely executed (see below).

Everything else in Sprint 2 is startable. A1.2 was implemented in C's
analytics toolset ahead of formal A1 sign-off and is now contract-authoritative.

## Sprint 2

**A — Evidence Validator + execution limits**
- [x] `agent/validation.py::EvidenceValidator` — orchestration slot only
      (bounded 1-revision retry loop); content checks land once C's
      classification vocabulary is ready (parallel work, integrate end of
      sprint). Done — `agent/validation.py` + `tests/unit/test_v3_validation.py`.
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
- [x] Re-run characterization suite before trusting it as the safety net for
      the coming deletion. Broadened to all 3 legacy `_CASES` metrics,
      re-run clean multiple times on 2026-09-18. Done.
- [x] Delete `HybridMcpDataProvider`, `business_state/series.py::fetch_series`,
      `agents/intelligence/observer.py::_query_windows` once the above
      passes. **Done 2026-09-18 (extract-wrap-delete):** class renamed
      `McpDataProvider`; series → `semantic.query_metric_series`;
      `_query_windows` → `_observation_windows`; BSS `fetch_series` kept as
      thin `raw_query_metric` adapter.

**C — Causal toolset v0 + evidence classification** *(Done — Sprint 2 close 2026-09-18)*
- [x] `toolsets/causal.py` + `causal/service.py` — DoWhy wiring extracted in
      place from `agents/diagnostic/*` then wrapped (same three-step pattern
      as Sprint 1). **EconML is not a dependency of this repo** — there is no
      EconML wiring to port; adding it is a separate scope decision.
      `estimate_effect(..., search_breadth: Literal[0,1,2]=0)` per frozen §4.
      Verified: `tests/unit/test_causal_toolset.py` (7 passed).
- [x] Evidence-classification vocabulary finalized and handed to A, applying
      A1.4's migration mapping across `src/` **and**
      `config/diagnostic_policies.yaml:24` (owner = Profile C).
      Validator minimal check: `agent/validation.py` rejects mission causal
      artifacts without a valid frozen classification.
- [x] Bug #6 regression against the new `search_breadth` escalation (A1.1):
      a wider retry genuinely searches a larger space, not a byte-identical
      re-run. (`test_search_breadth_widens_history_and_candidate_cap`,
      `test_estimate_effect_records_widened_caps_in_query`).
- [x] Bug #7: confirm still intermittent, not newly deterministic, **and**
      that the #6 widening — its only documented mitigation — survives via
      `search_breadth` under the confirmed `max_validation_revisions = 1`.
      Disposition recorded in `TASK_SHEET.md`.
- [x] Bug #13: explicit disposition (tracked ticket — fixture/template gap
      ported forward; not silently "fixed" by deleting the fixture path).
      See `TASK_SHEET.md`.
- [x] **Policy-gate disposition**: port the conditions in the eight
      `policy()` implementations into tool preconditions returning
      `INSUFFICIENT_EVIDENCE` (A1.3), and migrate the five
      `config/*_policies.yaml` thresholds into toolset config (YAML stay
      until that port lands). Thresholds live in
      `toolsets/policy_config.py`; YAML files retained for swarm_v2 callers.

**Joint decision (A + C), end of sprint**
- [x] `max_validation_revisions = 1`: **confirmed**. Causal escalation is
      `search_breadth` (caller-chosen), not validation revisions. STRONG-
      trust + REVISE with revisions exhausted → fail closed with
      `INSUFFICIENT_EVIDENCE` (`agent/validation.py`).
- [x] Skeptic → validator recorded as a **change in kind**: cross-agent
      adversarial challenge becomes in-context self-review
      (`03_PROFILE_CAPABILITIES.md` §4; `CONTRACTS.md` A1 joint decisions).

**Gate (end of sprint):** A integrates C's evidence-classification
vocabulary into the validator's content checks (applying A1.4's migration
mapping, including the `config/diagnostic_policies.yaml` value) — **Done
(minimal presence/validity check; full two-signal stays Sprint 3)**. B's
deletion only proceeds if its 3-day-repeated characterization suite passes
clean — **not met; carry-forward to Sprint 3**. C's Causal work Done. The
`max_validation_revisions` decision is recorded (confirmed = 1).

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
- [x] Delete `coordinator/catalogue_grounding.py`'s heuristic functions
      (`dimensions_in_query`, `apply_catalogue_grain`, etc.) once
      `SemanticToolset` is the only fetch path and validated against bug #8's
      repro case. Done.
- [x] Retire `MetricRegistry`/`MetricSemanticsRegistry` as standalone
      registries — replace `catalog_prompt()`'s data source with a live
      `seleric-mcp` catalogue call. **Resolved, not a deletion.** Direct
      read of `services/metrics.py` (2026-09-18) found the standalone-registry
      problem already fixed in practice: `MetricRegistry.bind_catalogue()`
      already makes every read method prefer the live catalogue once warm;
      `metric_registry.yaml` is only a cold-start/exception overlay, not a
      competing metric list — exactly what this line item asked for. The
      class itself stays (confirmed with user): its ~15 live callers
      (swarm_v2's classifier, diagnostic pipeline, skeptic) have no isolated
      test harness for a full rewrite, unlike the fetch-path/
      `catalogue_grounding.py` deletions this sprint did complete. See
      `TASK_SHEET.md` for the full finding. Not carried to Sprint 5.
- [x] `ActionToolset` — propose/validate/preview/confirm/commit wired to
      `actions_propose/commit/status`. Done 2026-09-18:
      `toolsets/actions.py`, remote transport registration, dedicated
      `v3_agent` action allowlist (legacy observer/domain agents remain
      read-only), and `tests/unit/test_action_toolset.py`. Confirmation is the
      user turn between preview and commit; the server's short-lived bearer
      token stays process-local and never enters model-visible provenance.
      The server broker owns executor dispatch, kill switch, audit, and
      payload idempotency. **Google Ads action execution is not part of
      this program's requirements** (decided 2026-09-19) — the action
      catalogue only ever needed to cover Meta (`pause_meta_ad`, the only
      pattern the upstream `seleric-mcp` audit found live); there is no
      Google Ads action contract to build against or wait on.

**C — Model/Skeptic-logic port**
- [x] `toolsets/models.py` + `models/service.py`. **The registry question
      was already answered** — `agents/prediction/swarm_bridge.py:61-62` builds
      an `InMemoryModelRegistry` from `scenario["forecast_truth"]`, and
      `config/model_registry.yaml` didn't exist (only a 573 B
      `.example.yaml`, both entries `status: candidate`) — no approved
      production models, so this was greenfield on a fixture-driven
      template service, not a port. Priority confirmed with user
      2026-09-18: **full build incl. a real forecaster**. Done —
      `models/service.py` (Holt/exponential smoothing via `statsmodels`),
      `models/evaluation.py`, `toolsets/models.py`, `config/model_registry.yaml`
      (4 approved daily forecast models). See `TASK_SHEET.md`.
- [x] `EvidenceValidator` content checks — ported trust-score/verdict-engine
      logic from `agents/skeptic/*`, preserving **two independent signals**
      (`score_trust` and `decide_verdict` look at different things), not
      merged into one score. Done — `agent/validation/` package
      (`signals.py`/`trust.py`/`verdict.py`), faithful port verified against
      `tests/skeptic/` staying green (34 passed, untouched).
- [x] Reproduced bug #12's STRONG-trust + REVISE state and recorded the
      loop's behavior under the Sprint 2 cap. Done —
      `tests/unit/test_v3_validation_signals.py` (19 passed); loop decision:
      REVISE consumes a revision and re-prompts, REJECT fails closed
      without consuming one, exhaustion mid-REVISE → `INSUFFICIENT_EVIDENCE`.

**C — Behavioral parity harness (new, runs alongside)**
- [x] Run the replay missions through both the old specialists and the new
      toolsets and diff the findings: same anomalies flagged, same hypotheses
      surfaced, same evidence classification. Every divergence gets a written
      explanation. This profile had no behavioral parity criterion at all
      while being the one the plan calls highest-behavioral-risk — schema
      completeness (metadata present on 100% of artifacts) is a floor a
      required Pydantic field satisfies trivially, not a parity gate. Done —
      `evals/parity.py` + `tests/replay/test_v3_parity.py`; bar is
      structural equivalence (metric/direction/classification/hypothesis),
      not exact-float parity, per user decision 2026-09-18. See `TASK_SHEET.md`.

**Gate (original):** B's Sprint 3 deletions require Sprint 2's
characterization suite to have already passed 3 separate days clean — do
not delete on a single green run. **Overridden by explicit user decision,
2026-09-18** ("delete it, we are almost rebuilding this") — the deletion
proceeded on the existing green run instead; recorded, not silently
skipped. See `TASK_SHEET.md` Sprint 3 Profile B.

**Status: CLOSED 2026-09-19** — all Sprint 3 tasks across A/B/C Done (the
one item genuinely still open past Sprint 3, `lookup_v1` grain-resolution
follow-on, was found and fixed 2026-09-19 — see `TASK_SHEET.md`). Google
Ads action execution was scoped out of this program's requirements
2026-09-19, not carried forward as a gap. Full suite: 903 passed, 1 failed
(pre-existing, unrelated), 4 skipped.

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
- [x] `ProviderRegistry` deletion. Done 2026-09-19 —
      `src/seleric_swarm/registry/provider_registry.py` and
      `config/provider_registry.yaml` deleted outright. It was a
      YAML-backed per-{domain,metric} anomaly-strategy selector consumed
      in exactly one place (`swarm/providers/provider_selection.py::
      ConfiguredAnomalyDetector`, built by
      `swarm/providers/mcp_data.py::build_mcp_bundle()`); the shipped
      config had only two real overrides (domain `commerce`; metrics
      `metric.spend`/`metric.net_profit`), both now hardcoded directly in
      `provider_selection.py` (`_ROBUST_ZSCORE_DOMAINS`/
      `_ROBUST_ZSCORE_METRICS`) rather than kept as a swappable table with
      one shipped configuration ever plugged into it. `force_robust_zscore`
      override and the sparse-history-degrades-to-template fallback are
      unchanged. Per explicit user direction this was deleted without
      strangler-fig deferral even though it was still live in swarm_v2's
      100%-traffic `AnomalyAgent` path — this repo has nothing in
      production yet, so a live call-graph dependency wasn't treated as a
      reason to wait for swarm_v2's own Sprint 5 retirement.
- [x] Confirm no remaining caller of any deleted module (whole-repo grep,
      not src-only — per the process note in
      `46_ARCHITECTURE_CONSOLIDATION_PLAN.md` about the Item 1a src-only
      grep miss). Done — zero `ProviderRegistry`/`provider_registry` hits
      in `src`/`tests`/`config`; three stale comments referencing the
      deleted YAML (`swarm/specialists/anomaly.py`,
      `tests/unit/test_business_state_mission_integration.py`) updated to
      name the new hardcoded set instead.

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

Note: `services/metrics.py::MetricRegistry`/`MetricSemanticsRegistry` is
**not** on this list. Resolved in Sprint 3 as already catalogue-first in
practice (`bind_catalogue()` makes the live catalogue authoritative); kept
as a standalone class/abstraction by explicit decision, not carried here.
See `TASK_SHEET.md` Sprint 3 Profile B.
- [ ] Delete `orchestration/dispatch.py::route_for` and
      `coordinator/lookup_fast_path.py` (only if Sprint 1's cost
      assumption held — recheck before deleting).
- [ ] Update `diagrams/current_architecture.mmd` to reflect the deletion
      (or retire the file with a pointer to `new.mmd`).

**Gate:** Program-level definition of done (overview §8) fully met.
