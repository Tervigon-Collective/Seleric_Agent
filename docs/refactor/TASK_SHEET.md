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
| Freeze `SelericDeps` shape | Not started | |
| Freeze `ToolResult` envelope | Not started | |
| Freeze `EvidenceArtifact`/`Finding`/`CausalArtifact`/`PredictionArtifact` schemas | Not started | |
| Freeze seven toolset signatures | Not started | |
| Spike: confirm seleric-mcp tool readiness (catalogue/metrics/actions) | Not started | |
| Capture pre-migration baseline (test pass counts, 2 live-trace repros) | Not started | |
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
