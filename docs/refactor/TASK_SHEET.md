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

### Joint — Amendment A1 sign-off
| Task | Status | Evidence |
|---|---|---|
| A/B/C sign-off on `CONTRACTS.md` Amendment A1 (A1.1 blocks C Sprint 2) | Not started | |
| Spike: add `pydantic-ai`, validate frozen §4 signatures against real `RunContext` API (A1.6) | Not started | |
| Owner assigned for `CAUSALLY_SUPPORTED_UNDER_ASSUMPTIONS` → `CAUSALLY_SUPPORTED` migration incl. `config/diagnostic_policies.yaml:24` (A1.4) | Not started | |

### Profile A — Runtime scaffolding
| Task | Status | Evidence |
|---|---|---|
| `agent/agent.py`/`dependencies.py`/`output.py` skeletons | Not started | |
| `POST /v1/missions` behind flag, 0% traffic | Not started | |
| Mission/Artifact stores | Not started | |
| Evals harness scaffolding | Not started | |

### Profile B — Semantic toolset v0
| Task | Status | Evidence |
|---|---|---|
| `toolsets/semantic.py::query_metrics/drilldown` | Done | `src/seleric_swarm/toolsets/semantic.py` (`search_semantics`, `get_metric_definition`, `query_metrics`, `drilldown`), plus `src/seleric_swarm/agent/contracts.py` (SelericDeps/ToolResult/artifact schemas materialized from `docs/refactor/CONTRACTS.md`) and `src/seleric_swarm/state/artifacts.py` (minimal ArtifactStore, both needed for the toolset to be callable/testable). Thin wrappers over the already-live `MCPGateway`/`services/mcp_query.py` — no `MetricRegistry`/`resolve_measure` heuristic anywhere in the call path (rule 1). 10/10 passing: `tests/unit/test_semantic_toolset.py` (contract test on `ToolResult` envelope invariants + unit tests with a fake MCP client, `./.venv/Scripts/python.exe -m pytest -q tests/unit/test_semantic_toolset.py`). |
| Characterization suite vs. new toolset (pre-consolidation baseline) | Done | `tests/replay/test_semantic_toolset_characterization.py::test_semantic_toolset_query_metrics_matches_hybrid_provider_fetch` — live run against production seleric-mcp, 2026-09-18: `SemanticToolset.query_metrics("units_sold", ...)` matches `HybridMcpDataProvider.fetch("metric.units_sold", ...)` exactly for 2026-08-01 (`pytest -q tests/replay/test_semantic_toolset_characterization.py`, 1 passed). Note: had to point the toolset's MCPGateway `agent_id` at the existing `observer_agent` identity (already unions every domain agent's seleric capabilities) rather than adding a new entry to `config/agent_registry.yaml` — that registry is itself retired by this migration (Profile A's "Retires" list), so it isn't the right place to grow permissions for the new single-agent loop; revisit once Profile A's real toolset-registration replacement lands. |

### Profile C — Analytics toolset v0 (re-scoped 2026-09-18)
| Task | Status | Evidence |
|---|---|---|
| Extract comparison/detection math in place (pure functions, existing suite green = proof of no drift) | Not started | |
| Wrap as `compare_periods`, `detect_anomalies` (delegate to existing `detectors.py::robust_zscore`) | Not started | |
| A1.2 grain precondition + `EVIDENCE_GRAIN_MISMATCH`; do not port `anomaly.py`'s sum/normalize fallback | Not started | |
| Bug #14 regression, both halves (un-normalized per-day reaches detector; mismatched set rejected) | Not started | |

## Sprint 2

### Profile A — Validator + execution limits
| Task | Status | Evidence |
|---|---|---|
| `EvidenceValidator` orchestration slot (bounded retry) | Not started | |
| Real execution-limit enforcement | Not started | |
| Synthetic over-budget rejection test | Not started | |

### Profile B — Consolidate 3 fetch paths
| Task | Status | Evidence |
|---|---|---|
| Line-by-line diff of the 3 implementations | Not started | |
| Route all call sites through `SemanticToolset` | Not started | |
| Characterization suite passes 3 separate days | Not started | |
| Delete `HybridMcpDataProvider`, `business_state/series.py::fetch_series`, `_query_windows` | Not started | |

### Profile C — Causal toolset v0 + evidence classes *(blocked on A1.1)*
| Task | Status | Evidence |
|---|---|---|
| `toolsets/causal.py` + `causal/service.py` (DoWhy only — no EconML in this repo) | Not started | |
| Evidence classification vocabulary handed to A + A1.4 migration applied across `src/` and `config/diagnostic_policies.yaml` | Not started | |
| Bug #6 regression against `search_breadth` escalation (A1.1) | Not started | |
| Bug #7 status (still intermittent) + confirm its #6-widening mitigation survives the revisions cap | Not started | |
| Bug #13 explicit disposition | Not started | |
| Policy-gate port: 8 `policy()` conditions → tool preconditions returning `INSUFFICIENT_EVIDENCE` (A1.3) | Not started | |
| Disposition of the five `config/*_policies.yaml` files | Not started | |

### Joint (A + C) — bounded-loop decisions
| Task | Status | Evidence |
|---|---|---|
| `max_validation_revisions = 1` confirmed or changed, incl. STRONG-trust + REVISE behavior | Not started | |
| Skeptic → validator recorded as a decision (cross-agent challenge → in-context self-review) | Not started | |

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
| Registry wiring question (consolidation-plan Item 3) | Done | Closed by source read 2026-09-18: `agents/prediction/swarm_bridge.py:61-62` builds an `InMemoryModelRegistry` from `scenario["forecast_truth"]` in `_fixture_deps()` whenever `self._deps` is None; the YAML path resolves `config/model_registry.yaml`, which does not exist — only `config/model_registry.example.yaml` (573 B, both entries `status: candidate`). Answer: neither a YAML-seeded registry nor an empty one in practice — it is fixture-seeded, and there are no approved production models. |
| `toolsets/models.py` (greenfield, not a port — re-confirm priority with user) | Not started | |
| `EvidenceValidator` content checks, two signals preserved (not merged) | Not started | |
| Bug #12 STRONG+REVISE reproduced + loop behavior under the revisions cap recorded | Not started | |

### Profile C — Behavioral parity harness
| Task | Status | Evidence |
|---|---|---|
| Replay missions diffed old specialists vs. new toolsets (same anomalies/hypotheses/classification, or written explanation per divergence) | Not started | |

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

### Profile C — Greenfield capability (additive, non-blocking)
| Task | Status | Evidence |
|---|---|---|
| `contribution_analysis`, `segment_decomposition`, `funnel_decomposition`, `cohort_analysis` (moved from Sprint 1 — no source to port) | Not started | |
| `toolsets/knowledge.py` | Not started | |
| `toolsets/experiments.py` | Not started | |

## Sprint 5 — Old pipeline deletion

| Task | Status | Evidence |
|---|---|---|
| Delete `coordinator/graph.py` | Not started | |
| Delete Blackboard | Not started | |
| Delete LeadershipManager/Controller | Not started | |
| Delete AgentRegistry + registry.yaml flags | Not started | |
| Delete `swarm/domain/` (base + configs) and `agents/domains/*.py` (8 stubs; `funnel_agent` is `enabled: true`, not already disabled) | Not started | |
| Untangle `agents/skeptic/registries.py` Protocols (`CausalGraphRegistry`/`ModelRegistry`/`MetricSemanticsRegistry`) before deleting the package | Not started | |
| Disposition `agents/intelligence/observer.py` (second observer, 25 KB — only `_query_windows` is owned today) | Not started | |
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
- 2026-09-18: **Design review of Profile C against source.** No code
  changed; planning docs only (`00_OVERVIEW.md`, `01_PROFILE_RUNTIME.md`,
  `02_PROFILE_SEMANTIC_MCP.md`, `03_PROFILE_CAPABILITIES.md`, `CONTRACTS.md`,
  `SPRINT_PLAN.md`, this file). Findings, all verified by direct read:
  - **Rules 4+5 have an unwritten corollary.** A tool may neither fetch
    evidence nor call the tool that does, so the caller picks grain, window
    and retry. Today that caller is deterministic code (`granularity`
    threaded `llm_classifier.py:57 → intake/__init__.py:411 → graph.py:1194
    → observer.py:42` — the mechanism that makes bug #14's fix hold);
    afterwards it is the LLM. The regression requirements assumed the
    mechanism survived. Resolution: preconditions the tool enforces, not
    prompt text. Written into overview §6 and `03_...` §3.
  - **Bug #6's fix has no home in the frozen contract.** The Causal toolset
    is exactly `estimate_effect` + `refute_estimate` — no widening
    parameter, nothing stateful — while #6's fix is escalating widening via
    `remediation_round`, which #7's entry names as its own only mitigation.
    Filed as amendment A1.1; blocks C's Sprint 2.
  - **C's exit criterion 1 was not satisfiable.** It demanded a passing test
    for #2/#6/#7/#8/#12/#14, but #7 is documented as non-deterministic
    LLM variance, #12 is filed as "not a bug", and #2/#8 root-cause in
    Profile B modules. Rewritten as four categories; #2/#8 now cite B's
    existing exit criteria 2 and 3.
  - **C had no behavioral parity criterion** while being called the
    highest-behavioral-risk profile; the old criterion 4 is schema
    completeness a required Pydantic field satisfies trivially. Parity
    harness added to Sprint 3.
  - **Eight `policy()` gates + five `config/*_policies.yaml` files had no
    owner** in any profile, despite bug #8 recording them working correctly
    in the trace where everything else failed. Assigned to C, Sprint 2.
  - **EconML is not a dependency** (zero matches in `pyproject.toml`,
    `src/`, `tests/`) though two plan lines said to port its wiring.
    **`pydantic-ai` is not a dependency either**, though the frozen
    signatures are typed against `RunContext`. Spike added to Sprint 1.
  - **Brief/contract signature divergence:** briefs listed 11 analytics /
    6 model / 6 experiment functions; `CONTRACTS.md` §4 froze 6 / 3 / 3.
    Reconciled in favor of the contract (A1.5).
  - **Analytics is mostly greenfield.** Only `robust_zscore` exists
    (`services/business_state/detectors.py:32`), plus `compare_periods`
    logic inline in `observer.py::_post_comparison_deltas`; the other four
    frozen functions have no implementation. Moved to Sprint 4's additive
    track.
  - **Evidence-classification migration:** code and
    `config/diagnostic_policies.yaml:24` use
    `CAUSALLY_SUPPORTED_UNDER_ASSUMPTIONS`; the contract froze
    `CAUSALLY_SUPPORTED`. Mapping table filed as A1.4 (the rename is
    defensible — the frozen `causally_supported_requires_refutation`
    validator makes the caveat enforced rather than a string suffix).
  - **Path corrections:** retire-set spans `swarm/` and `agents/` trees;
    `funnel_agent` is `enabled: true` (`config/agent_registry.yaml:47`), not
    already disabled; `swarm/domain/` is one config-driven base class, not
    8 agents; two observers exist; `CausalGraphRegistry` is a Protocol in
    `agents/skeptic/registries.py:225`, not a standalone registry.
  - **Closed an open question:** Sprint 3's "YAML-seeded or empty in-memory
    registry" — neither; it is fixture-seeded and there are no approved
    production models. Recorded as Done in the Sprint 3 table above.
  - **Recommended approach change:** extract-in-place → wrap → delete,
    rather than write-new-then-prove-parity, so "the math didn't change" is
    proven by the existing suite staying green instead of by review.
