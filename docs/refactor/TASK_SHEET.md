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
| `agent/agent.py`/`dependencies.py`/`output.py` skeletons | Not started | |
| `POST /v1/missions` behind flag, 0% traffic | Not started | |
| Mission/Artifact stores | Not started | |
| Evals harness scaffolding | Not started | |

### Profile B — Semantic toolset v0
| Task | Status | Evidence |
|---|---|---|
| `toolsets/semantic.py::query_metrics/drilldown` | Not started | |
| Characterization suite vs. new toolset (pre-consolidation baseline) | Not started | |

### Profile C — Analytics toolset v0
| Task | Status | Evidence |
|---|---|---|
| `compare_periods`, `detect_anomalies` ported | Not started | |
| Bug #14 regression test ported forward | Not started | |

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
