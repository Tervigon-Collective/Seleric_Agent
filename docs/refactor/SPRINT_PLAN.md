# Seleric V3 — Sprint Plan

2-week sprints, three profiles running in parallel with sync gates. A
profile that clears its gate early moves to its next sprint's tasks; it
does not wait idle for the others unless the next task explicitly depends
on a cross-profile contract (marked below).

Profile briefs: `01_PROFILE_RUNTIME.md` (A), `02_PROFILE_SEMANTIC_MCP.md`
(B), `03_PROFILE_CAPABILITIES.md` (C).

## Sprint 0 — Contracts (all three profiles, joint)

Nothing else starts until this is frozen — B and C both build against A's
contract, and A's validator needs C's evidence-classification vocabulary.

- [ ] Freeze `SelericDeps` dataclass shape (A drafts, B/C review).
- [ ] Freeze `ToolResult` envelope (A drafts, B/C review).
- [ ] Freeze `EvidenceArtifact`/`Finding`/`CausalArtifact`/`PredictionArtifact`
      schemas (A owns the store, C owns causal/prediction field needs).
- [ ] Freeze the seven toolset names + tool signatures (function names,
      params, return types — not implementations).
- [ ] Spike: confirm `seleric-mcp`'s `catalogue_search_metrics`,
      `metrics_query`, `metrics_drilldown`, `actions_propose/commit/status`
      are production-ready (Profile B, blocks its Sprint 1).
- [ ] Capture pre-migration baseline: run the full existing test suite +
      the two live-trace repros from `docs/TASK_SHEET.md` against current
      swarm_v2, record pass/fail counts as the parity bar every profile's
      exit criteria compares against.
- [ ] Add superseded-pointer note to
      `docs/46_ARCHITECTURE_CONSOLIDATION_PLAN.md` (done as part of this
      planning pass, see that file's top).

**Gate:** contracts merged and reviewed by all three profiles before any
Sprint 1 task starts.

## Sprint 1

**A — Runtime scaffolding**
- [ ] `agent/agent.py`, `agent/dependencies.py`, `agent/output.py` skeletons.
- [ ] `api/missions.py` — `POST /v1/missions` wired to a stub agent (no
      real toolsets yet) behind a feature flag, 0% traffic.
- [ ] `state/missions.py`, `state/artifacts.py` — Mission/Artifact stores.
- [ ] `evals/` harness scaffolding + golden dataset seeded from
      `eval/datasets/lookup_commerce.jsonl` and `tests/replay/` fixtures.

**B — Semantic toolset v0**
- [ ] `toolsets/semantic.py` — `query_metrics()`/`drilldown()` wrapping
      live `seleric-mcp` tools, no local heuristics.
- [ ] Re-run `tests/replay/test_data_access_characterization.py` against
      the new toolset (not yet wired into the agent) to establish it
      matches at least one of the three legacy paths before consolidation
      begins in Sprint 2.

**C — Analytics toolset v0**
- [ ] `toolsets/analytics.py::compare_periods`, `detect_anomalies` — pure
      functions, ported math from `ObserverAgent`/`AnomalyAgent`, unit
      tested against the same fixtures those modules used.
- [ ] Regression test ported forward for bug #14 (per-day granularity,
      not sum/normalize).

**Gate:** none blocking — Sprint 2 tasks for each profile don't strictly
require another profile's Sprint 1 output, except B's consolidation
(Sprint 2) needing its own Sprint 1 spike result.

## Sprint 2

**A — Evidence Validator + execution limits**
- [ ] `agent/validation.py::EvidenceValidator` — orchestration slot only
      (bounded 1-revision retry loop); content checks land once C's
      classification vocabulary is ready (parallel work, integrate end of
      sprint).
- [ ] Real execution-limit enforcement (`max_tool_calls`, `max_cube_queries`,
      etc.) — first time any budget concept in this system actually rejects
      a mission since it was disabled.
- [ ] Test: synthetic over-budget mission is actually rejected.

**B — Consolidate the three fetch paths (46_...Item 2 execution)**
- [ ] Diff `HybridMcpDataProvider.fetch()`/`fetch_series()` and
      `business_state/series.py::fetch_series()` line by line — confirm
      which behaviors are intentional differences (documented: last-point
      vs. sum) vs. bugs.
- [ ] Route all three call sites (`DomainAgent.observe()`,
      `BusinessStateService.get_metric_state()` callers, `lookup_fast_path.py`)
      through the new `SemanticToolset.query_metrics()`.
- [ ] Re-run characterization suite ≥3 separate days before trusting it as
      the safety net for the coming deletion.
- [ ] Delete `HybridMcpDataProvider`, `business_state/series.py::fetch_series`,
      `agents/intelligence/observer.py::_query_windows` once the above
      passes.

**C — Causal toolset v0 + evidence classification**
- [ ] `toolsets/causal.py` + `causal/service.py` — DoWhy/EconML wiring
      ported from `agents/diagnostic/*`, evidence-classification vocabulary
      finalized and handed to A for validator integration.
- [ ] Regression tests ported forward for bug #6 (remediation widening),
      #7 (intermittent zero-obs-rows — confirm still intermittent, not
      newly deterministic), #13 (explicit disposition — fix or ticket).

**Gate (end of sprint):** A integrates C's evidence-classification
vocabulary into the validator's content checks. B's deletion only proceeds
if its 3-day-repeated characterization suite passes clean.

## Sprint 3

**A — Mission Service + durable execution decision**
- [ ] Confirm (data, not assumption) whether any current mission needs
      Temporal's durability — if no, defer Temporal, ship synchronous-only.
- [ ] `MissionQueryCache` wired in, dedupes repeated evidence fetches
      within one mission.
- [ ] `observability/traces.py` — one trace per mission, OTel/Logfire.

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
- [ ] `toolsets/models.py` + `models/service.py` — port
      `SwarmPredictionSpecialist` logic, confirm `ModelRegistry` wiring
      (the one open question from consolidation-plan Item 3: is
      `agents/prediction/swarm_bridge.py` actually getting a YAML-seeded
      registry or an empty in-memory one).
- [ ] `EvidenceValidator` content checks — port trust-score/verdict-engine
      logic from `agents/skeptic/*`, reproduce bug #12's STRONG+REVISE case.

**Gate:** B's Sprint 3 deletions require Sprint 2's characterization suite
to have already passed 3 separate days clean — do not delete on a single
green run.

## Sprint 4

**A — Cutover flag + cost/latency gate (absorbs consolidation-plan Item 5, re-scoped)**
- [ ] Run the replay set (spec §43's queries: observe/detect/diagnose/
      predict/intervene/challenge) through the new agent loop with
      Analytics+Causal+Validator wired (Models/Knowledge/Experiments can
      lag, see below).
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

**C — Knowledge/Experiments (additive scope, non-blocking)**
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
- [ ] Delete `swarm/domain/*` (8 domain agents).
- [ ] Delete `swarm/specialists/*`, `agents/diagnostic/*`,
      `agents/prediction/*`, `agents/strategy/*`, `agents/skeptic/*`
      (each its own reviewed PR, gated on the corresponding toolset already
      running in production for the soak period).
- [ ] Delete `orchestration/dispatch.py::route_for` and
      `coordinator/lookup_fast_path.py` (only if Sprint 1's cost
      assumption held — recheck before deleting).
- [ ] Update `diagrams/current_architecture.mmd` to reflect the deletion
      (or retire the file with a pointer to `new.mmd`).

**Gate:** Program-level definition of done (overview §8) fully met.
