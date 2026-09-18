# Seleric V3 — Refactor Overview

Status: execution underway. Sprint 0 (contracts) Done; Sprint 1 A/B/C Done;
Sprint 2 A Done and B largely Done; **Amendment A1 ACCEPTED 2026-09-18**
(unblocks C Causal). Next: Profile C Causal toolset v0. See
`SPRINT_PLAN.md` / `TASK_SHEET.md` for live status. Last verified against
source: 2026-09-18.

This folder is the single source of truth for the swarm_v2 → PydanticAI+Cube
migration. Everything produced during the refactor (profile briefs, sprint
plan, task sheet) lives here, not scattered across `docs/`.

## 1. What is actually changing

Target architecture: **`diagrams/new.mmd`** (authoritative — confirmed with
the user 2026-09-17, superseding the earlier draft pasted into chat that
called for PydanticAI to hit Cube directly). The non-negotiable invariant
from `new.mmd` carries forward unchanged:

> The LLM never calls Cube or ClickHouse directly. All semantic/data/action
> access goes through Seleric MCP.

The long-form spec text from chat (sections 1–48: SelericDeps shape,
MissionResult contract, artifact types, toolset list, non-negotiable rules
13–16, repo layout) is still useful **wherever it doesn't contradict that
invariant** — i.e. everywhere except its "Agent → CubeClient → Cube" query
path, which `new.mmd` replaces with "Agent → Toolset → Seleric MCP Gateway →
CubeClient → Cube".

## 2. Scope boundary (unchanged, already true today)

```
Existing Data Platform → ClickHouse marts → Cube → Seleric
```

Ingestion, dbt, Kafka, Flink, Airflow/Mage, Iceberg are out of scope — they
already are today. This was verified, not assumed: `catalogue_bootstrap.py`
already warms a live Cube catalogue cache, and `seleric-mcp` already exposes
`catalogue_*` / `metrics_query` / `metrics_drilldown` as live tools. Cube is
not a future addition; it is already the metric backend behind the MCP
server this repo calls. **The refactor's real subject is this repo's
orchestration layer (swarm_v2, LangGraph, Blackboard, domain agents), not
the data platform.**

## 3. Scope-reducing finding (read before estimating Profile 2)

`new.mmd`'s "Seleric MCP — proposed Cube MCP architecture" subgraph
(Catalogue Resolver, Query Planner, Deterministic Insight Engine, Action
Broker, Provenance Composer) is **largely already live**, as a separate MCP
server this repo already calls (`mcp__seleric-mcp__*`: `catalogue_search_metrics`,
`catalogue_get_metric`, `catalogue_get_ontology`, `metrics_query`,
`metrics_drilldown`, `insights_explain`, `actions_propose/commit/status`,
plus the Meta/Google Ads write tools). Profile 2's job is **consolidating
this repo's three duplicate in-process fetch paths onto that existing
surface and deleting the duplicates** — not building a gateway from
scratch. Confirm this by direct inspection of the `seleric-mcp` server repo
before Sprint 1; if any of those tools turn out to be stubs, that's a
Sprint-1-blocking finding, not a Sprint-3 surprise.

## 4. Existing conversation/memory/artifact platform — reuse, don't rebuild

Confirmed by direct read 2026-09-17: `src/seleric_swarm/conversations/`
(~5,000 lines: `contracts.py`, `memory.py`, `repositories.py`, `postgres.py`,
`phase7.py`, `events.py`, `privacy.py`, `blobs.py`) plus `api/conversations.py`
is a mature, already-live platform that implements several things the spec
(§24–26, non-negotiable rules 6/13) asks the runtime to have. This is not
Profile A greenfield work — it's existing infrastructure Profile A wires the
new agent into.

| Spec ask | Already implemented as |
|---|---|
| Evidence-backed artifacts (rule 6) | `Artifact.require_provenance()` — factual/derived artifacts already require `evidence_ids` + a calculation/query/prompt/tool/model version, enforced at write time. |
| Propose → validate → preview → confirm → commit → audit (rule 13) | `ApprovalRequest` (`dry_run`, `action_preview`, `idempotency_key`) + `ApprovalDecisionEvent` + `RollbackRecord`, in `conversations/phase7.py`. |
| Memory with consent (spec §26, `docs/47_CONVERSATION_DATA_POLICY.md`) | `MemoryItem` full lifecycle: `PENDING_CONSENT → ACTIVE`, supersession (`memories_contradict`), expiry, opt-out preference — `conversations/memory.py`. |
| Mission/runtime context assembly (`SelericDeps`) | `ContextBuilder.build()` — token-budgeted assembly of recent messages + latest summary + scored memories + artifacts into one `ContextBundle`. |
| Durable execution / worker recovery | `Run`/`RunAttempt` already has lease/heartbeat/claim/CAS semantics (`conversations/memory.py::InMemoryRunRepository`) — a working alternative to Temporal, not a blank slate. |
| Mission Store | `persistence/memory.py::InMemoryMissionStore` already exists, including cancellation-race protection (won't let a late background write clobber a client-cancelled mission). |

**Correction to the mental model in `new.mmd`/spec §30-31:** `Run.mission_id`
already links a `Run` (inside a `Thread`) to a `Mission` — missions already
execute *inside* this conversation platform's Run/Thread model via
`api/conversations.py`, not as an independent top-level API. Profile A's
"Mission Service"/"API" work is about wiring the new agent loop to *this*
existing Run-attempt execution path, not building a parallel one.

**Genuinely retires with the old pipeline (LangGraph-specific, not reusable):**
- `checkpointing.py`'s `CheckpointProvider` family (`InMemoryCheckpointProvider`,
  `PostgresCheckpointProvider`) — wraps `langgraph.checkpoint.*` directly;
  goes when `coordinator/graph.py` goes. Whatever durability the new agent
  loop needs should be evaluated against the *existing* `Run`/`RunAttempt`
  lease mechanism first (already Postgres-backable via `conversations/postgres.py`)
  before reaching for Temporal or a new checkpoint mechanism.

This finding **directly changes Profile A's Sprint 1/3 tasks** (state stores,
Temporal decision) and **Profile B's Sprint 3 `ActionToolset` task**
(approval flow already exists) — see those briefs and `SPRINT_PLAN.md`,
updated accordingly.

## 5. Relationship to existing docs — what this supersedes

- **`docs/46_ARCHITECTURE_CONSOLIDATION_PLAN.md` Item 2** (consolidate the
  three MCP data-access paths) is absorbed into **Profile 2, Sprint 2**. Its
  characterization suite (`tests/replay/test_data_access_characterization.py`,
  7/7 passing as of 2026-09-16) is the starting safety net — reuse it, don't
  redo it.
- **Item 5** (load/cost validation before broadly enabling specialists) is
  absorbed into **Profile 1, Sprint 4 gate** — re-scoped from "validate the
  old five specialists" to "validate the new toolset-based agent loop",
  since the specialists themselves are being retired.
- **Items 1/1a, 3, 4** are already done/retracted — no further action, not
  reopened by this plan.
- A pointer has been added to the top of `46_ARCHITECTURE_CONSOLIDATION_PLAN.md`
  marking it superseded by this folder (see that file).
- `docs/features/lookup-v1-retirement.md`, `docs/BUG_SHEET.md`,
  `docs/TASK_SHEET.md` stay as historical record of swarm_v2's own life —
  not rewritten, referenced where relevant (e.g. bug #6/#8/#14's root
  causes are exactly the heuristic-layer problems Profile 3 deletes wholesale
  rather than patches again).

## 5. Migration strategy: strangler fig, in-place

This is the live repo, not a clean-slate rewrite (confirmed with the user).
Rules for the whole program:

1. `orchestration/dispatch.py::run_any_mission` keeps routing 100% of
   traffic to swarm_v2 until a profile's replacement passes its exit
   criteria for parity, on the same replay/eval set swarm_v2 currently
   passes.
2. Nothing in `swarm_v2`, `coordinator/graph.py`, or the specialists is
   deleted until its PydanticAI-toolset replacement is live behind a flag
   and has run in shadow (or a canary %) against production-shaped traffic.
3. Each profile's own exit criteria (below) is the gate, not a calendar
   date. A sprint that doesn't clear its gate does not advance; the next
   sprint's tasks still start (profiles are decoupled by contract, see §7),
   but the cutover flag stays off.
4. Final deletion of the old pipeline (LangGraph graph, Blackboard,
   LeadershipManager/Controller, AgentRegistry, domain agents,
   swarm/specialists/*, MetricRegistry/MetricSemanticsRegistry,
   catalogue_grounding.py's heuristics) is its own reviewed PR per
   subsystem, gated on its replacement's parity — never a bulk delete.

## 6. Non-negotiable rules (carried from the spec, unchanged)

1. Cube is the only authority for business metrics.
2. The LLM does not generate production analytical SQL.
3. Only the central Seleric Agent decides the next capability/tool call.
4. Tools never call other tools — only `Agent → Tool → Agent`.
5. Analytics tools calculate; they don't independently fetch data (they
   consume evidence already fetched via the semantic toolset).
6. Every numerical claim in a `MissionResult` maps to an `EvidenceArtifact`.
7. Every mission has exactly one `as_of` timestamp, inherited by every step.
8. Evidence is immutable; findings can be superseded, never rewritten.
9. Causal claims carry an evidence classification
   (OBSERVATION/ASSOCIATION/HYPOTHESIS/CAUSALLY_SUPPORTED/EXPERIMENTALLY_VALIDATED).
10. Predictions carry model id + version metadata.
11. Every agent loop is bounded (`max_tool_calls`, `max_cube_queries`, etc.).
12. Knowledge retrieval and conversation memory never substitute for a Cube
    query on a live metric value.
13. Write/action operations follow propose → validate → preview → confirm →
    commit → audit. Never direct autonomous irreversible execution.
14. A new autonomous agent is added only if there is a genuine isolation
    boundary that can't be represented as a tool (bar: same as `new.mmd`'s
    rule 16).

**Corollary to rules 4 + 5 (added 2026-09-18, **ACCEPTED** as
`CONTRACTS.md` amendment A1):** because a tool may neither fetch evidence
nor call the tool that does, the **caller** chooses the grain, the window
and the retry. Today that caller is deterministic code (e.g. the
`granularity` field threaded `llm_classifier.py:57 → intake/__init__.py:411
→ graph.py:1194 → observer.py:42`, which is what makes bug #14's fix hold);
after the refactor it is the LLM. So: **if the caller picks the inputs, the
tool must validate them.** Control state that today lives in typed mission
state becomes typed *preconditions the tool enforces* — returning
`success=False` with a named `error_code` — not prose in a tool description
the model may or may not honor. Without this, several shipped fixes port
forward as conventions rather than guarantees and will reappear as
intermittents rather than test failures. See `03_PROFILE_CAPABILITIES.md` §3.

## 7. Three profiles

| Profile | Owns | Full brief |
|---|---|---|
| **A — Runtime & Orchestration** | API, Mission Service, PydanticAI `Agent[SelericDeps, MissionResult]`, state (Mission/Artifact/Memory/Cache stores), Evidence Validator, observability, evals, execution limits | `01_PROFILE_RUNTIME.md` |
| **B — Semantic & MCP Consolidation** | Consolidating this repo's 3 duplicate MCP fetch paths, retiring `MetricRegistry`/`MetricSemanticsRegistry`/`catalogue_grounding.py` heuristics in favor of the live `seleric-mcp` catalogue, `SemanticToolset`, `ActionToolset` wiring | `02_PROFILE_SEMANTIC_MCP.md` |
| **C — Capabilities & Intelligence** | Turning Observer/Anomaly/Diagnostic/Prediction/Strategy/Skeptic into `AnalyticsEngine`/`CausalEngine`/`ModelService`/`EvidenceValidator` + `KnowledgeToolset`/`ExperimentToolset` | `03_PROFILE_CAPABILITIES.md` |

**Contract between profiles** (must be frozen in Sprint 0, owned jointly,
changes require all three profiles to sign off):

- `SelericDeps` dataclass shape.
- `ToolResult` envelope (`success`, `artifact_ids`, `summary`, `provenance`,
  `warnings`, `error_code`, `retryable`).
- `EvidenceArtifact` / `Finding` / `CausalArtifact` / `PredictionArtifact`
  schemas (Profile A owns the store; B and C are the only writers).
- The seven toolset names and their tool signatures (owned by B for
  Semantic/Action, by C for Analytics/Causal/Models/Knowledge/Experiment).

## 8. Program-level definition of done

- swarm_v2, LangGraph graph, Blackboard, LeadershipManager/Controller,
  AgentRegistry, PromptRegistry, ProviderRegistry, CausalGraphRegistry (as a
  standalone registry), domain agents, `swarm/specialists/*`, `MetricRegistry`,
  `MetricSemanticsRegistry`, `catalogue_grounding.py` are deleted from `src/`.
- `orchestration/dispatch.py` routes 100% of traffic to the PydanticAI
  agent; `route_for()`/lookup-vs-swarm classification is gone.
- The program-level eval set (Pydantic Evals, golden dataset from spec §43)
  passes at parity or better vs. the swarm_v2 baseline captured before
  Sprint 1.
- Budget/execution-limit enforcement (currently disabled system-wide,
  `governance/budget.py`) is either re-enabled in the new runtime's bounded
  loop or explicitly re-approved as still-disabled — not silently left off.
- The eight `policy(blackboard, mission) -> bool` gates and five
  `config/*_policies.yaml` files have an explicit disposition — ported to
  tool preconditions or deliberately dropped. They are executable policy
  that demonstrably worked (`docs/BUG_SHEET.md` #8: *"every downstream
  specialist correctly skipped via policy gates for lack of data"*), and no
  profile owned them before 2026-09-18.
- `pydantic-ai` is an actual declared dependency and the frozen toolset
  signatures have been validated against its real API, not only against the
  spec text.
- `docs/refactor/TASK_SHEET.md` shows every sprint task as Done, with the
  same "verify before claiming done" discipline the existing
  `docs/TASK_SHEET.md` already uses in this repo.

## 9. Suggested improvements to `new.mmd` (not applied — user's call)

Flagging these rather than editing the diagram directly, per instruction.

1. `PROV["Provenance Composer"]` feeds `ARTIFACTS` but nothing in the
   diagram shows the Evidence Validator (`VALIDATE`) reading provenance
   before producing `OUTPUT` — worth an explicit edge
   `PROV -.-> VALIDATE` so the validator's "every number traces to
   evidence" check has a drawn dependency, not just an implied one.
2. `CACHE["Mission Query Cache"]` has no inbound/outbound edges at all —
   either wire it (`QP --> CACHE`, `CACHE --> QP`) or drop the node; an
   unconnected node in the diagram will confuse the next person who reads
   it as "already wired."
3. `KR["Doc retrieval — not metric truth"]` is a good guardrail as a label
   but isn't enforced anywhere structurally — worth a note that
   `KnowledgeToolset` should be schema-forbidden from returning a bare
   numeric metric value (only text/citations), so the guardrail survives
   refactors of the label itself.
4. No node represents the Evidence Validator's bounded retry
   (`max_validation_revisions = 1` from the spec) — worth adding a
   `VALIDATE -- REVISE (max 1) --> AGENT` self-loop edge so the bounded-loop
   rule is visible in the diagram, matching non-negotiable rule 11.
