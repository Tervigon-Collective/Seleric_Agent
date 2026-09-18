# Seleric V3 Refactor — Task Sheet

Update this file the moment a task's status changes — don't batch updates
to end-of-session. Status values: `Not started`, `In progress`, `Blocked`,
`Done`. On `Done`, add one line of verification evidence (test name, trace
id, command run), same discipline as `docs/TASK_SHEET.md` already uses in
this repo — a checkbox alone is not verification.

Sprint definitions: `SPRINT_PLAN.md`. Profile briefs: `01_PROFILE_RUNTIME.md`,
`02_PROFILE_SEMANTIC_MCP.md`, `03_PROFILE_CAPABILITIES.md`.

## Sprint 0 — Contracts (joint)

| Task | Status | Evidence |
|---|---|---|
| Freeze `SelericDeps` shape | Done | `docs/refactor/CONTRACTS.md` §1, frozen 2026-09-18 |
| Freeze `ToolResult` envelope | Done | `docs/refactor/CONTRACTS.md` §2, frozen 2026-09-18 |
| Freeze `EvidenceArtifact`/`Finding`/`CausalArtifact`/`PredictionArtifact` schemas | Done | `docs/refactor/CONTRACTS.md` §3, frozen 2026-09-18 |
| Freeze seven toolset signatures | Done | `docs/refactor/CONTRACTS.md` §4, frozen 2026-09-18 |
| Spike: confirm seleric-mcp tool readiness (catalogue/metrics/actions) | Done | Live tool calls 2026-09-18: `catalogue_search_metrics`/`catalogue_get_metric`/`catalogue_list_brands` production-ready (rich live data — 30 real metrics for "revenue", 6 real brands). `actions_list_available`/`actions_status` live (correct empty-list/unknown-id behavior). `metrics_query` initially returned `ConnectError` (transient — matched a prior finding in `docs/TASK_SHEET.md` 2026-09-17); retried later same session and connection was live: `total_sales` returned real Cube data end-to-end (₹13,638 yesterday, brand 20, full provenance/freshness block) — confirms the numeric path is genuinely production-ready, not a stub. Separate finding, not blocking: `net_sales_all_channels` (view `canonical_pnl`) fails with `CubeError: Unknown table expression identifier 'serve.amazon_attribution_overview'` — a real Cube schema/model gap on the data-platform side, unrelated to this repo or seleric-mcp's readiness; worth reporting upstream before Profile B relies on that specific metric. |
| Capture pre-migration baseline (test pass counts, 2 live-trace repros) | Done | Full suite (`./.venv/Scripts/python.exe -m pytest -q`, `tests/integration/test_minio_blob_store_integration.py` excluded — needs a live MinIO server not part of this deployment's actual storage path, see `docs/refactor/TASK_SHEET.md` Log): **731 passed, 1 failed, 4 skipped** (2026-09-18). Sole failure: `tests/unit/test_api_scenario_matrix.py::test_health_combo_never_returns_running` (asserts `body["artifacts"]["hypothesis"]` non-empty; DoWhy fell back to template due to no causal observations for `metric.discounts` in the fixture window) — pre-existing, unrelated to this migration, this is the parity bar every profile's exit criteria compares against. Two live-trace repros already on record (`docs/TASK_SHEET.md` "Not done yet" section): bug #8's query → mission `MS-83c8033ee8`, 5 Evidence + 5 Anomaly artifacts, no dimension misclassification; bug #14's phrasing → mission in `mission2.json`, 10 Evidence + 10 Anomaly artifacts (2 metrics × 5 days), confirming per-day rows not a window sum. Both completed with skeptic verdict PASS. |
| Supersede pointer added to `46_ARCHITECTURE_CONSOLIDATION_PLAN.md` | Done | Added 2026-09-17, see that file's header |

## Sprint 1

### Profile A — Runtime scaffolding
| Task | Status | Evidence |
|---|---|---|
| `agent/agent.py`/`dependencies.py`/`output.py` skeletons | Done | `src/seleric_swarm/agent/{agent,dependencies,output,artifacts,instructions}.py`; `SelericAgent = Agent[SelericDeps, MissionResult]` built and run end-to-end with a fixed-output `TestModel` stub (no real toolsets) — verified via `agent.run()` smoke test and `tests/unit/test_v3_missions_stub.py`. Added `pydantic-ai-slim>=2.45` to `pyproject.toml` (slim variant chosen deliberately — full `pydantic-ai` pulls ~38 packages incl. mcp/anthropic/google-genai/logfire SDKs unrelated to this repo's own LLM port; slim adds only 5: `pydantic-ai-slim`, `pydantic-graph`, `griffelib`, `logfire-api`, `genai-prices`). `MissionResult`'s exact shape is a draft, not frozen — see `agent/output.py` module docstring: spec §36's text isn't captured anywhere in this repo, only `CONTRACTS.md`'s four Sprint-0 contracts are frozen. |
| `POST /v1/missions` behind flag, 0% traffic | Done | `src/seleric_swarm/api/missions.py` — a standalone `APIRouter`, deliberately **not** `include_router`-ed into `main.py`'s live app (that endpoint is large and already serves 100% of production traffic per the strangler-fig rule; replacing it in place would violate that rule, not honor it). `settings.v3_agent_enabled` (new, default `False`) gates it with a 501 for when it does get mounted. Tested in isolation: `tests/unit/test_v3_missions_stub.py` (2 cases: disabled-by-default 501, enabled stub-agent 200). |
| Mission/Artifact stores | Done | `src/seleric_swarm/state/{missions,artifacts}.py` — `InMemoryMissionStore`/`InMemoryArtifactStore`, deliberately separate from swarm_v2's `persistence/memory.py` (must not share mutable state with the pipeline still carrying 100% of traffic). `ArtifactStore.put` enforces immutability (rule 8) and calls `Artifact.require_provenance()`. Verified: `tests/unit/test_v3_state_stores.py`. |
| Evals harness scaffolding | Done (loader only — see evidence) | `src/seleric_swarm/evals/golden_dataset.py::load_golden_dataset()` loads `eval/datasets/lookup_commerce.jsonl` into typed `EvalCase`s. Verified: `tests/unit/test_v3_golden_dataset.py`. Explicitly NOT done: extracting `tests/replay/`'s pytest-embedded cases into this shape, and wiring an actual scorer (`pydantic_evals` or hand-rolled) — deferred honestly, see `evals/__init__.py` docstring, because Sprint 1's agent is a fixed-output stub and scoring against it would only prove the stub returns its own hardcoded text. |

**Environment finding (read before running this repo's tests going forward):** this sandbox has two independent Python environments — a project `.venv` (3.12, uv-managed) and a bare `python` on PATH resolving to a machine-wide Python 3.13 that also has unrelated tools installed (`mage-ai`, `meltano`, `langchain-openai`). Bare `python -m pytest` runs against the latter. Installing `pydantic-ai-slim` there via `pip install` cascaded an upgrade of `opentelemetry-api` 1.27.0→1.44.0, breaking `opentelemetry-sdk`'s pin (a real, if narrowly caught, cross-project regression) — reverted immediately, and the package removed from that environment entirely. All dependency work for this migration should go through `.venv` only (`uv sync` + `./.venv/Scripts/python.exe -m pytest`), matching this file's own Sprint 0 baseline command — not bare `python`.

### Profile B — Semantic toolset v0
| Task | Status | Evidence |
|---|---|---|
| `toolsets/semantic.py::query_metrics/drilldown` | Done | `src/seleric_swarm/toolsets/semantic.py` (`search_semantics`, `get_metric_definition`, `query_metrics`, `drilldown`), plus `src/seleric_swarm/agent/contracts.py` (SelericDeps/ToolResult/artifact schemas materialized from `docs/refactor/CONTRACTS.md`) and `src/seleric_swarm/state/artifacts.py` (minimal ArtifactStore, both needed for the toolset to be callable/testable). Thin wrappers over the already-live `MCPGateway`/`services/mcp_query.py` — no `MetricRegistry`/`resolve_measure` heuristic anywhere in the call path (rule 1). 10/10 passing: `tests/unit/test_semantic_toolset.py` (contract test on `ToolResult` envelope invariants + unit tests with a fake MCP client, `./.venv/Scripts/python.exe -m pytest -q tests/unit/test_semantic_toolset.py`). |
| Characterization suite vs. new toolset (pre-consolidation baseline) | Done | `tests/replay/test_semantic_toolset_characterization.py::test_semantic_toolset_query_metrics_matches_hybrid_provider_fetch` — live run against production seleric-mcp, 2026-09-18: `SemanticToolset.query_metrics("units_sold", ...)` matches `HybridMcpDataProvider.fetch("metric.units_sold", ...)` exactly for 2026-08-01 (`pytest -q tests/replay/test_semantic_toolset_characterization.py`, 1 passed). Note: had to point the toolset's MCPGateway `agent_id` at the existing `observer_agent` identity (already unions every domain agent's seleric capabilities) rather than adding a new entry to `config/agent_registry.yaml` — that registry is itself retired by this migration (Profile A's "Retires" list), so it isn't the right place to grow permissions for the new single-agent loop; revisit once Profile A's real toolset-registration replacement lands. |

### Profile C — Analytics toolset v0
| Task | Status | Evidence |
|---|---|---|
| `compare_periods`, `detect_anomalies` ported | Not started | |
| Bug #14 regression test ported forward | Not started | |

## Sprint 2

### Profile A — Validator + execution limits
| Task | Status | Evidence |
|---|---|---|
| `EvidenceValidator` orchestration slot (bounded retry) | Done (orchestration only, as scoped) | `src/seleric_swarm/agent/validation.py` — `EvidenceValidator.validate()` (structural checks only: unresolved artifact ids, empty `final_response` on a "completed" mission) + `run_validated_mission()` (bounded 1-revision retry via `ExecutionBudgetTracker`, no infinite loop). Content checks (evidence-classification vocabulary) intentionally deferred to Profile C's handoff per the Sprint 2 gate. Verified: `tests/unit/test_v3_validation.py` (5 cases incl. exhausting the bounded retry and confirming it fails closed with `INSUFFICIENT_EVIDENCE`, not looping). |
| Real execution-limit enforcement | Done | `src/seleric_swarm/agent/limits.py::ExecutionBudgetTracker` — `consume()` actually rejects once a counter (`tool_calls`/`cube_queries`/`causal_queries`/`prediction_calls`/`validation_revisions`) would cross its `ExecutionLimits` bound, and a rejected `consume()` does not mutate the counter (checked explicitly in tests, not just the return value). `check_runtime()` covers `max_runtime_seconds` separately (a clock, not a counter). This is the first budget concept in this system that isn't `governance/budget.py`'s permanent no-op. |
| Synthetic over-budget rejection test | Done | `tests/unit/test_v3_execution_limits.py` (5 cases: consume ok/rejected/counter-unchanged-on-reject/unknown-counter/runtime elapsed vs. within budget). |

### Profile B — Consolidate 3 fetch paths
| Task | Status | Evidence |
|---|---|---|
| Line-by-line diff of the 3 implementations | Not started | |
| Route all call sites through `SemanticToolset` | Not started | |
| Characterization suite passes 3 separate days | Not started | |
| Delete `HybridMcpDataProvider`, `business_state/series.py::fetch_series`, `_query_windows` | Not started | |

### Profile C — Causal toolset v0 + evidence classes
| Task | Status | Evidence |
|---|---|---|
| `toolsets/causal.py` + `causal/service.py` | Not started | |
| Evidence classification vocabulary finalized, handed to A | Not started | |
| Bug #6 regression (remediation widening) | Not started | |
| Bug #7 status (confirm still intermittent) | Not started | |
| Bug #13 explicit disposition | Not started | |

## Sprint 3

### Profile A — Mission Service
| Task | Status | Evidence |
|---|---|---|
| Temporal necessity decision (data-backed) | Not started | |
| `MissionQueryCache` wired in | Not started | |
| One trace per mission (OTel/Logfire) | Not started | |

### Profile B — Heuristic retirement
| Task | Status | Evidence |
|---|---|---|
| Delete `catalogue_grounding.py` heuristics | Not started | |
| Retire `MetricRegistry`/`MetricSemanticsRegistry` as standalone | Not started | |
| `ActionToolset` wired to propose/confirm/commit | Not started | |

### Profile C — Model/Skeptic port
| Task | Status | Evidence |
|---|---|---|
| `toolsets/models.py` + registry wiring confirmed | Not started | |
| `EvidenceValidator` content checks (trust/verdict) | Not started | |
| Bug #12 STRONG+REVISE reproduced | Not started | |

## Sprint 4

### Profile A — Cutover gate
| Task | Status | Evidence |
|---|---|---|
| Replay set run through new loop, cost/latency recorded | Not started | |
| Execution limits tuned to acceptable cost/latency | Not started | |
| Figures documented | Not started | |
| Canary flag flipped | Not started | |

### Profile B — Cleanup
| Task | Status | Evidence |
|---|---|---|
| `ProviderRegistry` deleted | Not started | |
| Whole-repo grep confirms no dangling callers | Not started | |

### Profile C — Additive capability
| Task | Status | Evidence |
|---|---|---|
| `toolsets/knowledge.py` | Not started | |
| `toolsets/experiments.py` | Not started | |

## Sprint 5 — Old pipeline deletion

| Task | Status | Evidence |
|---|---|---|
| Delete `coordinator/graph.py` | Not started | |
| Delete Blackboard | Not started | |
| Delete LeadershipManager/Controller | Not started | |
| Delete AgentRegistry + registry.yaml flags | Not started | |
| Delete `swarm/domain/*` (8 agents) | Not started | |
| Delete `swarm/specialists/*` | Not started | |
| Delete `agents/diagnostic/*` | Not started | |
| Delete `agents/prediction/*` | Not started | |
| Delete `agents/strategy/*` | Not started | |
| Delete `agents/skeptic/*` | Not started | |
| Delete `route_for`/`lookup_fast_path` (if cost assumption held) | Not started | |
| `current_architecture.mmd` updated/retired | Not started | |

## Log

- 2026-09-17: Planning pass complete. `docs/refactor/` created with
  overview, 3 profile briefs, sprint plan, this task sheet. Superseded
  pointer added to `docs/46_ARCHITECTURE_CONSOLIDATION_PLAN.md`. No
  execution started.
- 2026-09-18: Sprint 0 executed and gate cleared. `docs/refactor/CONTRACTS.md`
  added (all four frozen contracts + seven toolset signatures). seleric-mcp
  readiness spike run live; baseline captured (731 passed/1 failed/4 skipped,
  full detail in the table above). Also found and trimmed three unused
  top-level dependencies from `pyproject.toml` (`mcp[cli]`, `scikit-learn`,
  `statsmodels` — zero imports anywhere in `src/`/`tests/`, the latter two
  already pulled in transitively by `dowhy`); confirmed via `uv sync --extra
  dev` + full re-run, identical pass/fail counts before and after. This
  repo's actual attachment/blob storage in production is Postgres, not
  `MinioBlobStore`, despite that class existing in code (per user,
  2026-09-18) — the MinIO integration test was excluded from the baseline
  run on that basis, not treated as a blocking failure.
- 2026-09-18: Sprint 1 Profile A executed (all four tasks — see table above
  for evidence). Added `pydantic-ai-slim` dependency. Full suite via the
  project `.venv` (the correct environment, not the machine-wide `python` —
  see the environment finding above): 770 passed, 1 failed (the same
  pre-existing `test_health_combo_never_returns_running`), 5 skipped —
  parity with the Sprint 0 baseline preserved, no regressions from the new
  code (which isn't wired into any live traffic path). Profile B/C Sprint 1
  tasks not started.
- 2026-09-18: Sprint 2 Profile A executed (both tasks — see table above for
  evidence). Also re-linted/type-checked every V3 file added so far
  (`ruff check` + `mypy`, both clean) and the earlier dispatch.py
  conversational-reply/caching change from this same session, since the
  user asked for the profile work to be bug-free, not just present. Full
  suite via `.venv`: 780 passed, 1 failed (the same pre-existing
  `test_health_combo_never_returns_running`), 5 skipped. Profile B/C
  Sprint 1/2 tasks not started.
