# V3 Cleanup & Anomaly Audit — 2026-09-21

Full-codebase pass removing leftover swarm_v2 (old LangGraph multi-agent
architecture) code and docs that survived Sprint 5's bulk deletion, fixing
three evidence-adjacent silent-exception spots, and consolidating the docs
tree. See `docs/CURRENT_ARCHITECTURE.md` for the resulting as-built
reference.

## Live V3 call graph (confirmed, untouched by this pass)

`main.py`, `bootstrap.py`, `runtime.py`, `agent/*`, `toolsets/*`,
`contracts/lookup.py`, `state/*`, `persistence/*`,
`api/{async_missions,conversations,phase7,office/*,v3_state}.py`, `causal/*`,
`coordinator/observability/{__init__,events}.py`, `swarm/__init__.py`,
`swarm/providers/{__init__,base}.py`, `docs/refactor/*`.

Confirmed via a full read of `main.py`'s FastAPI routes, `bootstrap.py`'s
`build_runtime`, and `agent/runner.py::run_v3_mission`'s import chain, plus a
whole-repo (not just `src/`) grep for every deleted module's dotted path.

## Source deleted this pass

Old coordinator classify/decompose/intake pipeline — dead in production
(zero live importers), kept "not dead" only by its own unit tests:

- `coordinator/{agent,contracts,models,overview,policies,state,catalogue_grounding}.py`
- `coordinator/{decomposition,intake,planning}/` (whole dirs)
- `agents/{base,coordinator,__init__}.py`, `agents/intelligence/__init__.py`
  (and the empty `agents/{diagnostic,domains,prediction,skeptic,strategy}/`
  — no `.py` source remained, only stale `__pycache__`); `agents/` itself
  removed once empty.
- `orchestration/{state,__init__}.py` (whole dir removed — only stale
  `__pycache__` remnants of already-Sprint-5-deleted `graph.py`/`dispatch.py`/
  `runner.py`/`synthesize.py` otherwise).
- `swarm/{artifacts,transport,envelope,mission}.py`
- `swarm/providers/{provider_selection,template}.py`
- `protocols/a2a/envelope.py` (and the dir, once its `__init__.py` was
  confirmed empty).

`coordinator/__init__.py` was edited, not deleted — stripped its re-exports
of the now-gone `coordinator.agent`/`contracts`/`models`, kept minimal so
`coordinator.observability.*` (live) still imports cleanly.

## Already deleted (Sprint 5, pre-existing — for continuity)

`orchestration/{dispatch,graph,runner}.py`, `swarm/{blackboard,orchestrator}.py`,
`coordinator/{governance/skeptic_gate,execution/dispatcher,routing/dispatchability,lookup_fast_path}.py`,
`agents/{diagnostic,prediction,strategy,skeptic}/swarm_bridge.py`,
`agents/intelligence/skeptic.py`, `coordinator/leadership/manager.py` — see
`docs/refactor/TASK_SHEET.md`'s Sprint 5 table.

## Tests deleted / trimmed this pass

Deleted (fully old-pipeline-only, confirmed by reading each file):
`test_catalogue_grounding.py`, `test_conversation_context.py`,
`test_overview.py`, `test_swarm_llm_classifier.py`,
`test_provider_selection.py`, `test_relative_effect_anomaly.py` (every test
in the file exercised the deleted `RelativeEffectAnomalyDetector`, not the
still-live `swarm/providers/base.py` types it also imported).

Trimmed (mixed live/dead content, read fully before editing):
- `tests/unit/test_phase6_memory_context.py` — removed one function
  (`test_followup_submit_includes_prior_user_turn_in_context_bundle`) that
  imported the deleted `coordinator/intake/conversation_context.py`; the
  rest of the file (live `conversations/` memory platform tests) is
  untouched. See "Open items" in `docs/CURRENT_ARCHITECTURE.md` §11 — this
  function tested functionality that was never actually wired into the live
  submission path, so deleting the test isn't a regression, but it does mean
  follow-up context inheritance has no live implementation today.
- `tests/contract/test_mcp_and_envelope.py` → renamed
  `test_mcp_gateway.py` — removed `test_envelope_contract` (tested the
  deleted `protocols/a2a/envelope.py`), kept the live MCP gateway test.
- `tests/unit/test_phase0_2_backend_gaps.py` — removed
  `test_a2a_schema_and_validation_share_the_python_contract` (tested the
  deleted `swarm/envelope.py`), kept the live `mission_access` test.
- `src/seleric_swarm/llm/adapters/fake.py` — removed the
  `"coordinator.classify_swarm"`/`"coordinator.conversational_reply"`/
  `"coordinator.decompose_mission"` prompt_id branches and their backing
  functions (`classify_swarm_query`, `conversational_reply_query`,
  `decompose_mission_query`, plus now-unused greeting/thanks/identity
  regexes) after confirming no remaining test sends those prompt_ids.

All 411 remaining unit tests pass; 1 pre-existing failure
(`test_rate_limit_error_is_not_dumped_to_the_user`) confirmed unrelated —
reproduces identically on the pre-cleanup tree.

## Settings removed

`config/settings.py`: `max_coordinator_iterations`, `max_leadership_transfers`,
`max_agent_calls`, `require_skeptic_for_causal`, `require_provenance_for_numeric`,
`completion_review_threshold`, `workflow_name` (a `Settings` field distinct
from the unrelated `workflow_name` parameter/dataclass-field used elsewhere
for LLM tracing metadata — verified no code reads `settings.workflow_name`
before removing). All verified zero non-`settings.py` readers.

**Found but left as-is** (out of this pass's approved scope, flagged for a
follow-up settings audit): `swarm_workflow: Literal["swarm_v2"]`,
`coordinator_policies_path`, `max_remediation_rounds` — same dead-pipeline
shape, zero live readers found, but not part of the original approved
removal list.

## Anomalies fixed (behavior-preserving — added visibility only)

1. `causal/dowhy_service.py::_drop_collinear_common_causes` — bare
   `except Exception: kept.append(cause); continue` when computing a
   collinearity correlation. Now logs a warning (`_log.warning(
   "collinearity_check_failed", exc_info=True, extra={"cause": cause})`)
   before falling back to the same conservative "keep the cause" default.
2. `services/ontology.py::OntologyService._call` — `except Exception: return
   {}` on any MCP call failure. Now logs a warning with `capability`/
   `agent_id` before returning the same empty-dict fallback (ontology
   context must not fail a mission — behavior unchanged).
3. `agent/runner.py::_context_bundle` — `except Exception: return
   ContextBundle()` on a malformed context bundle. Now logs a warning with
   `exc_info=True` before returning the same empty bundle.

## Anomalies found but left as-is (documented only)

- `main.py` has 7 function-local imports concentrated in a handful of
  functions (`api.office.gateway`, `config.settings`, `persistence.memory`,
  `api.office.v3_adapter`, `observability.flow`, `uvicorn`) — a standing
  circular-import-avoidance smell at the top of the dependency graph. Not
  fixed this pass; a real fix would need `main.py`'s import structure
  untangled, out of scope for a dead-code cleanup.
- `.env.example` sets `LLM_PROVIDER=fake` while `Settings.llm_provider`
  defaults to `"azure_openai_compatible"` in code — likely an intentional
  local-dev-vs-prod difference, not a bug, but worth a sanity check with
  whoever owns `.env.example`.
- Two plain-text (non-hyperlink) citations of the now-deleted
  `02_SELERIC_AGENT_INTEGRATION.md` remain in
  `docs/features/business-state-service/05_SPRINT_PLAN.md` (prose
  references to exit-criteria/step numbers in the deleted doc) — not fixed,
  since resolving them requires knowing what those specific exit criteria
  became, which this pass didn't have context for.
- `docs/refactor/{00_OVERVIEW,02_PROFILE_SEMANTIC_MCP,SPRINT_PLAN,TASK_SHEET}.md`
  reference `docs/46_ARCHITECTURE_CONSOLIDATION_PLAN.md`,
  `docs/features/lookup-v1-retirement.md`, and `diagrams/current_architecture.mmd`
  — all deleted this pass. `docs/refactor/` was left untouched per this
  repo's standing rule (different owners, reconciled at merge time) — these
  are now dangling citations inside that folder specifically, left for
  whoever next edits `docs/refactor/` to clean up.

## Docs deleted / archived / trimmed this pass

- **Deleted** (~94 files): root `docs/` swarm_v2-specific numbered docs (27),
  stale planning trackers (7, including repo-root
  `SELERIC_SWARM_PROJECT_TASK_SHEET.md`), `docs/coordinator/` (22),
  `docs/diagnostic/` (6), `docs/prediction/` (5), `docs/skeptic/` (12),
  `docs/features/business-state-service/02_SELERIC_AGENT_INTEGRATION.md`,
  11 swarm-roster-tied `docs/office-ui/*` files, 6 old diagrams
  (`diagrams/{current_architecture,final_architecture,leadership_handoff,
  mission_lifecycle.mmd,mission_lifecycle.svg}`,
  `docs/diagrams/two_axis_swarm_architecture.mmd` — `docs/diagrams/` removed
  once empty). Also removed `MANIFEST.md` (repo root) — a pre-swarm_v2-era
  file listing already so stale it named files deleted before this session
  even started (`agents/domains/commerce.py`, `leadership/manager.py`,
  `ml/model_router.py`, `registry/agent_registry.py`).
- **Archived, not deleted**: `docs/BUG_SHEET.md` (header note added, kept
  for incident-history value), `docs/ADR/ADR-001*`/`ADR-002*` (superseded-by
  note added, decision record kept).
- **Trimmed**: `.cursor/rules/{00-project-architecture,02-agent-contracts}.mdc`
  (dropped the swarm_v2-labeled rule sections, kept only V3 rules);
  `docs/office-ui/00_OVERVIEW.md` (dropped links to deleted files);
  `docs/office-ui/15_TESTING.md` (added a staleness note on swarm_v2 event
  vocabulary in test descriptions); `README.md` (rewrote the swarm_v2
  architecture pitch as a V3 summary pointing to `docs/CURRENT_ARCHITECTURE.md`);
  `docs/features/business-state-service/README.md` (replaced the deleted
  doc's table row and diagram references with a short V3-integration note).
- **New**: `docs/CURRENT_ARCHITECTURE.md`, this audit doc.

## Left alone / out of scope

- `src/seleric_swarm/api/missions.py` — an unmounted V3 stub endpoint
  ("Sprint 1 scaffolding, 0% traffic"). Confirmed out of scope by explicit
  user decision — V3-era, not swarm_v2, may be intentional future
  scaffolding.
- `persistence/memory.py`'s `MissionStore` — confirmed live/shared store
  backend (`bootstrap.py::build_store` wires it as `runtime.store`, used by
  both the synchronous and durable-queue V3 mission paths), **not** a
  swarm_v2-only duplicate despite an older docstring in `state/missions.py`
  implying a "two pipelines, don't share state" caution that's since been
  resolved by the pipeline it was cautioning against no longer existing.
- `.cursor/plans/seleric_prototype_architecture_d08c1c32.plan.md` — an old
  Cursor planning-session transcript with stale doc links; left untouched
  as a historical tool artifact, not authored documentation.
