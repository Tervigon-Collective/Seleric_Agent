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
  must still be reproducible under the new EvidenceValidator).
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
- **Not planned anywhere in this folder, found 2026-09-18 while checking
  Sprint 1/2 for UI connectivity:** the live `office-ui/` frontend
  (`office-ui/src/api/missions.ts` → `GET /v1/office/missions*`) is driven
  by `api/office/normalize.py::build_office_snapshot`, which reads the
  swarm_v2-shaped raw mission dict directly — `mission_lead`,
  `leadership_epoch`, `handoff_history`, `tasks` (for the parallel-task
  fan-out view), and `artifacts` as typed buckets (`hypothesis`/
  `prediction`/`strategy`/`skeptic`/...). None of that shape exists on the
  V3 `MissionResult`/`ArtifactStore` (`agent/output.py`,
  `agent/artifacts.py`) — a straight cutover would leave the Office UI
  blank or crashing for every V3 mission. Either `api/office/normalize.py`
  needs a V3-shaped adapter path, or the Office UI needs its own V3 view,
  before any cutover percentage > 0. Not blocking now (no toolsets exist to
  produce a real V3 mission to render yet) but must land before Sprint 4's
  canary flip, not be discovered at that point.

## Exit criteria (parity gate before old pipeline deletion)

1. Full existing test suite equivalent (`tests/unit`, `tests/coordinator`,
   `tests/contract`, `tests/replay`) passes against the new runtime at the
   same pass rate `docs/TASK_SHEET.md` last recorded (534 passed, 2
   pre-existing unrelated failures, 1 known live-data flake).
2. The two live-trace repros already on record
   (`docs/TASK_SHEET.md`'s bug #8/#14 production queries) produce
   equivalent `MissionResult`s (same metric, same per-day granularity, same
   skeptic/validator verdict) end-to-end via the new agent loop.
3. Execution limits actually reject a synthetic over-budget mission in a
   test (currently impossible — `check_budget` is a no-op everywhere).
4. Program-level eval set (§8 of overview) at parity or better.
5. `office-ui/` renders a V3 mission (agents/board/artifacts/trace) without
   a blank/crashed view — either via a `build_office_snapshot`-equivalent
   V3 adapter or a dedicated V3 view. See the Key risks entry above.
