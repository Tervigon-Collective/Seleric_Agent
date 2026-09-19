# Profile A — Runtime & Orchestration

Owns the skeleton everything else plugs into. Ships first because B and C
both depend on its contracts (`SelericDeps`, `ToolResult`, artifact schemas)
being frozen.

## Mission

Replace `orchestration/dispatch.py::run_any_mission` + `route_for()` +
`coordinator/graph.py`'s LangGraph state machine + `Blackboard` +
`LeadershipManager`/`LeadershipController` with a single
`Agent[SelericDeps, MissionResult]` loop, backed by explicit state stores
instead of a shared mutable blackboard.

## Retires (delete only after its replacement clears parity, per strangler-fig rule)

- `src/seleric_swarm/orchestration/dispatch.py::route_for` — lookup-vs-swarm
classification disappears; there's one path.
- `src/seleric_swarm/coordinator/graph.py` — the LangGraph `build_swarm_v2_graph`
state machine (intake/decompose/plan/assemble/execute/refine/specialists/
skeptic_gate/remediate/complete/synthesize nodes).
- `src/seleric_swarm/coordinator/lookup_fast_path.py` — folded into the one
agent loop; there's no separate fast path once the main loop is cheap
enough for simple lookups (verify this assumption in Sprint 4's cost gate
before deleting — if the new loop is materially more expensive per simple
lookup than `lookup_fast_path` was, that's a real finding, not a reason to
keep two paths; bring it back to the user).
- Blackboard (7 artifact types) → `ArtifactStore` (typed, per-kind tables/collections).
- `LeadershipManager`/`LeadershipController` — no handoff concept in the new
loop; the agent decides its own next tool call each turn.
- `AgentRegistry`, `config/agent_registry.yaml`'s per-specialist `enabled`
flags — replaced by toolset registration, which is static per deployment,
not a runtime-toggle registry.
- `PromptRegistry` → folded into `agent/instructions.py` + `RuntimeConfig`.
- `governance/budget.py`'s currently-disabled `check_budget`/`check_hard_stops`
→ replaced by the bounded-loop execution limits (spec §39); this is where
that "low-priority backlog item" from `docs/TASK_SHEET.md` actually gets
resolved, not re-patched onto the old dispatcher.



## Builds

- `agent/agent.py` — the one `SelericAgent = Agent[SelericDeps, MissionResult]`.
- `agent/dependencies.py` — `SelericDeps` (frozen contract, §7 of overview).
- `agent/instructions.py` — system prompt / instructions, versioned.
- `agent/output.py` — `MissionResult` Pydantic model (spec §36).
- `agent/validation.py` — `EvidenceValidator` (replaces Skeptic's role at
the orchestration layer; Skeptic's causal-challenge *content* logic is
Profile C's, see below).
- `api/missions.py`, `api/events.py` — `POST /v1/missions`, SSE/WebSocket,
thin wrappers over Mission Service.
- Mission Service + `state/missions.py` (`Mission` model, `MissionStatus`
enum) — replaces ad hoc mission dict/context threading in
`coordinator/graph.py`.
- `state/artifacts.py` (`ArtifactStore`), `state/conversations.py`
(`ConversationMemory`), `state/cache.py` (`MissionQueryCache`).
- `observability/traces.py`, `observability/audit.py` — one trace per
mission (spec §42), OpenTelemetry/Logfire.
- `evals/` — Pydantic Evals harness + golden dataset (spec §43), seeded from
the existing `eval/datasets/lookup_commerce.jsonl` and the replay fixtures
already in `tests/replay/`.
- Execution limits (spec §39) as actual enforcement, not a disabled no-op —
`max_tool_calls`, `max_cube_queries`, `max_causal_queries`,
`max_prediction_calls`, `max_validation_revisions`.
- Durable execution: `Mission Service ↔ Temporal` for long/forensic
missions; short missions stay fully synchronous through the agent loop.



## Depends on

- Profile B's `SemanticToolset`/`ActionToolset` signatures (to wire
`TOOLSETS --> SEM/ACTIONS` in the agent).
- Profile C's `AnalyticsToolset`/`CausalToolset`/`ModelToolset`/
`KnowledgeToolset`/`ExperimentToolset` signatures.
- Nothing blocks Sprint 0/1 (contract + scaffolding) — B and C consume A's
contracts, not the other way around.



## Key risks

- Recreating LangGraph's implicit state machine as prompted agent behavior
  can regress control flow that was previously deterministic (e.g. the
  skeptic-gate's forced `REVISE` on a blocking evidence gap — spec rule 9,
  and `docs/BUG_SHEET.md` #12's documented "STRONG trust + REVISE" behavior
  must still be reproducible under the new EvidenceValidator). The general
  form of this risk, and the mitigation, is now written up as the rules 4+5
  corollary in overview §6 and `03_PROFILE_CAPABILITIES.md` §3: deterministic
  control state becomes typed tool preconditions, not prompt text. A owns the
  `ToolResult` error codes those preconditions return
  (`EVIDENCE_GRAIN_MISMATCH` — `CONTRACTS.md` A1.2, ACCEPTED).
- **`EvidenceValidator` is not a drop-in replacement for the skeptic.** The
  skeptic is a separate agent with separate context, adversarial to the
  diagnostic agent's output; the validator sits in the same loop, informed by
  the same context that produced the claim. In-context self-review is a
  weaker check than cross-agent challenge. **Decision recorded 2026-09-18
  with A1 acceptance:** accepted as a change in kind, not a consolidation
  (`03_PROFILE_CAPABILITIES.md` §4; `CONTRACTS.md` A1 joint decisions).
- **`max_validation_revisions = 1` confirmed 2026-09-18 with A1.** Causal
  escalation is `search_breadth` on `estimate_effect` (A1.1), not the
  validation-revision counter. On STRONG-trust + REVISE when revisions are
  exhausted → fail closed with `INSUFFICIENT_EVIDENCE`.
- Removing `lookup_fast_path` risks the exact bug class it was built to
fix (`docs/BUG_SHEET.md` #2's metric-ID canonicalization bug, #9's
intentional no-handoff simplicity) reappearing if the new loop
reintroduces multi-hop handoff-like behavior. Explicitly test against
those two bugs' original repro cases before cutover.
- Temporal introduces new operational surface (durable execution) — confirm
  it's actually needed for this workload before building it; if no mission
  today runs long enough to need durability, defer this to a later sprint
  and ship synchronous-only first (ponytail: don't build durability for a
  workload that doesn't need it yet).
- **Found 2026-09-18, fixed the same day (not "not planned" any more).**
  The live `office-ui/` frontend (`office-ui/src/api/missions.ts` →
  `GET /v1/office/missions*`) is driven by
  `api/office/normalize.py::build_office_snapshot`, which reads the
  swarm_v2-shaped raw mission dict directly. None of that shape exists on
  the V3 `MissionResult`/`ArtifactStore` — a straight cutover would have
  left the Office UI blank/crashing for every V3 mission. **Done**:
  `api/v3_state.py` (shared V3 Mission/Artifact store singletons —
  `api/missions.py` previously built a throwaway store per request and
  never persisted the `Mission`, so it was unretrievable after the
  response) + `api/office/v3_adapter.py::v3_raw_snapshot()` (translates a
  V3 `Mission`+`Artifact`s into the same raw dict shape
  `build_office_snapshot` already renders, reusing that logic rather than
  forking it), wired into `api/office/gateway.py::_raw()` as a fallback.
  V3's one agent maps to the `"coordinator"` office-ui node as a
  stand-in, not a real per-specialist mapping — that narrower scope is
  still open, tracked in exit criterion 5 below, but the blank/crash
  failure mode itself is fixed.

## Exit criteria (parity gate before old pipeline deletion)

1. **Partially met, ongoing.** Full existing test suite passes against
   `main`/`src` at every sprint checkpoint — most recent full run
   (2026-09-19, `tests/refactor` fix pass): **903 passed, 1 failed
   (pre-existing, unrelated `test_health_combo_never_returns_running`), 4
   skipped**. This criterion is about the *new runtime specifically*
   reaching this pass rate once real toolsets are wired into an actual
   agent loop (Sprint 4) — today's number is the whole repo including the
   still-100%-traffic swarm_v2 path, not yet a V3-loop-only measurement.
2. **Not yet — requires Sprint 4's agent loop.** The two live-trace repros
   (`docs/TASK_SHEET.md`'s bug #8/#14 production queries) need to run
   through the new agent loop end-to-end once Analytics+Causal+Validator
   are wired into it; today they're only verified against the underlying
   toolsets directly (`tests/unit/test_semantic_toolset_bug_regressions.py`,
   `tests/unit/test_analytics_toolset.py`), not through an agent making its
   own tool-call decisions.
3. **Met (Sprint 2).** `tests/unit/test_v3_execution_limits.py` —
   `ExecutionBudgetTracker.consume()` actually rejects a synthetic
   over-budget mission; 5 cases including counter-unchanged-on-reject.
4. **Not yet — Sprint 4 task.** Program-level eval set at parity or better.
5. **Met for the failure mode; narrower scope still open.** `office-ui/`
   no longer renders blank/crashed for a V3 mission (see Key risks entry
   above) — but every V3 mission still maps to a single `"coordinator"`
   node rather than the swarm_v2 per-specialist board view. Whether that
   narrower rendering is acceptable for cutover is a Sprint 4 call, not
   answered here.
