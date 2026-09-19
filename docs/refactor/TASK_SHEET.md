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
| A/B/C sign-off on `CONTRACTS.md` Amendment A1 (A1.1 unblocks C Sprint 2) | Done | ACCEPTED 2026-09-18. Applied into frozen §2 (named error codes) and §4 (`search_breadth` on `estimate_effect`; Analytics grain rule). Change log + joint decisions in `CONTRACTS.md`. |
| Spike: add `pydantic-ai`, validate frozen §4 signatures against real `RunContext` API (A1.6) | Done | `pydantic-ai-slim>=2.45` in `pyproject.toml`; stub agent + `RunContext[SelericDeps]` validated (Profile A Sprint 1). Closed with A1 acceptance. |
| Owner assigned for `CAUSALLY_SUPPORTED_UNDER_ASSUMPTIONS` → `CAUSALLY_SUPPORTED` migration incl. `config/diagnostic_policies.yaml:24` (A1.4) | Done | Owner = Profile C. Migration executes during Causal Sprint 2 (not part of A1 landing). Mapping table accepted in `CONTRACTS.md` A1.4. |

### Profile A — Runtime scaffolding
| Task | Status | Evidence |
|---|---|---|
| `agent/agent.py`/`dependencies.py`/`output.py` skeletons | Done | `src/seleric_swarm/agent/{agent,dependencies,output,artifacts,instructions}.py`; `SelericAgent = Agent[SelericDeps, MissionResult]` built and run end-to-end with a fixed-output `TestModel` stub (no real toolsets) — verified via `agent.run()` smoke test and `tests/unit/test_v3_missions_stub.py`. Added `pydantic-ai-slim>=2.45` to `pyproject.toml` (slim variant chosen deliberately — full `pydantic-ai` pulls ~38 packages incl. mcp/anthropic/google-genai/logfire SDKs unrelated to this repo's own LLM port; slim adds only 5: `pydantic-ai-slim`, `pydantic-graph`, `griffelib`, `logfire-api`, `genai-prices`). `MissionResult`'s exact shape is a draft, not frozen — see `agent/output.py` module docstring: spec §36's text isn't captured anywhere in this repo, only `CONTRACTS.md`'s four Sprint-0 contracts are frozen. |
| `POST /v1/missions` behind flag, 0% traffic | Done (local flag only — not a production canary %) | `src/seleric_swarm/api/missions.py` remains a standalone unmounted stub (strangler-fig: do not replace the live endpoint in place). Live traffic is gated in `main.py` + `api/async_missions.py`: when `settings.v3_agent_enabled` is True, conversations and `POST /v1/missions` call `agent/runner.py::run_v3_mission` instead of swarm_v2. Default False; `tests/conftest.py` forces it off so the suite stays on swarm_v2. Local `.env` may set `V3_AGENT_ENABLED=true` for a developer canary. Office list/snapshot reuse `v3_adapter.v3_raw_snapshot`. Evidence: `tests/unit/test_v3_ui_connect.py`. |
| Mission/Artifact stores | Done | `src/seleric_swarm/state/{missions,artifacts}.py` — `InMemoryMissionStore`/`InMemoryArtifactStore`, deliberately separate from swarm_v2's `persistence/memory.py` (must not share mutable state with the pipeline still carrying 100% of traffic). `ArtifactStore.put` enforces immutability (rule 8) and calls `Artifact.require_provenance()`. Verified: `tests/unit/test_v3_state_stores.py`. |
| Evals harness scaffolding | Done (loader only — see evidence) | `src/seleric_swarm/evals/golden_dataset.py::load_golden_dataset()` loads `eval/datasets/lookup_commerce.jsonl` into typed `EvalCase`s. Verified: `tests/unit/test_v3_golden_dataset.py`. Explicitly NOT done: extracting `tests/replay/`'s pytest-embedded cases into this shape, and wiring an actual scorer (`pydantic_evals` or hand-rolled) — deferred honestly, see `evals/__init__.py` docstring, because Sprint 1's agent is a fixed-output stub and scoring against it would only prove the stub returns its own hardcoded text. |

**Environment finding (read before running this repo's tests going forward):** this sandbox has two independent Python environments — a project `.venv` (3.12, uv-managed) and a bare `python` on PATH resolving to a machine-wide Python 3.13 that also has unrelated tools installed (`mage-ai`, `meltano`, `langchain-openai`). Bare `python -m pytest` runs against the latter. Installing `pydantic-ai-slim` there via `pip install` cascaded an upgrade of `opentelemetry-api` 1.27.0→1.44.0, breaking `opentelemetry-sdk`'s pin (a real, if narrowly caught, cross-project regression) — reverted immediately, and the package removed from that environment entirely. All dependency work for this migration should go through `.venv` only (`uv sync` + `./.venv/Scripts/python.exe -m pytest`), matching this file's own Sprint 0 baseline command — not bare `python`.

### Profile B — Semantic toolset v0
| Task | Status | Evidence |
|---|---|---|
| `toolsets/semantic.py::query_metrics/drilldown` | Done | `src/seleric_swarm/toolsets/semantic.py` (`search_semantics`, `get_metric_definition`, `query_metrics`, `drilldown`), built against Profile A's canonical `agent/dependencies.py::SelericDeps`, `agent/output.py::ToolResult`, `agent/artifacts.py::EvidenceArtifact`, and `state/artifacts.py::InMemoryArtifactStore` (a same-day duplication of these — an orphaned `agent/contracts.py` — was found and deleted during Sprint 2, see that section below). Thin wrappers over the already-live `MCPGateway`/`services/mcp_query.py` — no `MetricRegistry`/`resolve_measure` heuristic anywhere in the call path (rule 1). 10/10 passing: `tests/unit/test_semantic_toolset.py` (contract test on `ToolResult` envelope invariants + unit tests with a fake MCP client, `./.venv/Scripts/python.exe -m pytest -q tests/unit/test_semantic_toolset.py`). |
| Characterization suite vs. new toolset (pre-consolidation baseline) | Done | `tests/replay/test_semantic_toolset_characterization.py::test_semantic_toolset_query_metrics_matches_hybrid_provider_fetch` — live run against production seleric-mcp, 2026-09-18: `SemanticToolset.query_metrics("units_sold", ...)` matches `HybridMcpDataProvider.fetch("metric.units_sold", ...)` exactly for 2026-08-01 (`pytest -q tests/replay/test_semantic_toolset_characterization.py`, 1 passed). Note: had to point the toolset's MCPGateway `agent_id` at the existing `observer_agent` identity (already unions every domain agent's seleric capabilities) rather than adding a new entry to `config/agent_registry.yaml` — that registry is itself retired by this migration (Profile A's "Retires" list), so it isn't the right place to grow permissions for the new single-agent loop; revisit once Profile A's real toolset-registration replacement lands. |

### Profile C — Analytics toolset v0 (re-scoped 2026-09-18)
| Task | Status | Evidence |
|---|---|---|
| Extract comparison/detection math in place (pure functions, existing suite green = proof of no drift) | Done | `src/seleric_swarm/analytics/comparison.py::period_deltas` — the pairing/subtraction lifted out of `observer.py::_post_comparison_deltas`, which keeps its signature and Blackboard writes and now delegates. **Drift proof**: the 27 offline tests covering the touched modules were not edited and stay green — `./.venv/Scripts/python.exe -m pytest tests/unit/test_domain_questions.py tests/unit/test_anomaly_specialist.py tests/unit/test_business_state_anomaly.py -q` → 27 passed. Two behaviors deliberately preserved and commented at the call site: deltas are `a - b` (a decline reads negative), and *both* periods are deduped by (metric, dimensions) before pairing — iterating period A directly would have turned a repeated key into N deltas instead of 1. Scope correction to the sprint plan: extraction was much smaller than assumed, because `detectors.py::robust_zscore`/`_rescore_against_expected` and `observer.py::_daily_windows` are **already pure functions** — nothing to extract, so they are reused as-is rather than moved. |
| Wrap as `compare_periods`, `detect_anomalies` (delegate to existing `detectors.py::robust_zscore`) | Done | `src/seleric_swarm/toolsets/analytics.py`. Design consequence worth recording: `detect_anomalies` **cannot** wrap `RobustZScoreDetector` — that class fetches its own history via `BusinessStateService.get_metric_state()` mid-detection, which rule 5 forbids. History arrives as evidence instead (the set is the series: sorted by `period_start`, last point = observation, rest = baseline), and only the pure `robust_zscore` is reused. `method="seasonal"` is in the frozen signature but has no implementation in `src/`, so it returns `error_code="METHOD_NOT_AVAILABLE"` rather than silently running a different detector; `"mad"` maps to `robust_zscore` because that function *is* the median/MAD estimator. Needs a small A1 addendum for the new error code. |
| A1.2 grain precondition + `EVIDENCE_GRAIN_MISMATCH`; do not port `anomaly.py`'s sum/normalize fallback | Done | `src/seleric_swarm/analytics/grain.py::validate_grain_set()` — three rules: one grain per call, each artifact's span matches its declared grain (day=1, week=7, month=28-31), all spans equal. Returns a reason string rather than raising, so a tool never raises across the agent boundary. `anomaly.py`'s sum/normalize branch is **not** ported. 11 passing: `tests/unit/test_analytics_grain.py`. |
| Bug #14 regression, both halves (un-normalized per-day reaches detector; mismatched set rejected) | Done | `tests/unit/test_analytics_toolset.py` — 16 passed. Half 1: `test_per_day_evidence_reaches_detector_unnormalized` (5 daily rows, observed stays the raw 900.0, baseline is the median 4090.0 of the 4 prior days). Half 2: `test_multi_day_aggregate_labelled_day_grain_is_rejected`, `test_aggregate_mixed_into_a_daily_series_is_rejected`, `test_mixed_grain_set_is_rejected` — all `success=False` / `EVIDENCE_GRAIN_MISMATCH`, never normalized. |
| Cross-profile seam test (B's evidence → C's analytics) | Done | `tests/unit/test_analytics_semantic_handoff.py` — 4 passed. Pins the B→C handoff end to end rather than assuming it: B emits one artifact per Cube bucket (each `period_start == period_end` for day grain), C scores the real drop against its own daily history, and a hand-built window-aggregate-labelled-day artifact is refused. Also pins the upstream hazard below. |
| Full suite vs. Sprint 0 baseline (no new failures) | Done | `./.venv/Scripts/python.exe -m pytest -q --ignore=tests/integration/test_minio_blob_store_integration.py` (2026-09-18) → **842 passed, 1 failed, 4 skipped** in 607s. Sole failure is the same pre-existing `tests/unit/test_api_scenario_matrix.py::test_health_combo_never_returns_running` recorded in the Sprint 0 baseline — no new failures. Ruff clean on all new/modified files. |

## Sprint 2

### Profile A — Validator + execution limits
| Task | Status | Evidence |
|---|---|---|
| `EvidenceValidator` orchestration slot (bounded retry) | Done (orchestration + minimal causal vocab) | `src/seleric_swarm/agent/validation.py` — structural checks + Sprint 2 C handoff: mission `CausalArtifact`s must carry a valid frozen `evidence_classification`. Full skeptic two-signal stays Sprint 3. Verified: `tests/unit/test_v3_validation.py`. |
| Real execution-limit enforcement | Done | `src/seleric_swarm/agent/limits.py::ExecutionBudgetTracker` — `consume()` actually rejects once a counter (`tool_calls`/`cube_queries`/`causal_queries`/`prediction_calls`/`validation_revisions`) would cross its `ExecutionLimits` bound, and a rejected `consume()` does not mutate the counter (checked explicitly in tests, not just the return value). `check_runtime()` covers `max_runtime_seconds` separately (a clock, not a counter). This is the first budget concept in this system that isn't `governance/budget.py`'s permanent no-op. |
| Synthetic over-budget rejection test | Done | `tests/unit/test_v3_execution_limits.py` (5 cases: consume ok/rejected/counter-unchanged-on-reject/unknown-counter/runtime elapsed vs. within budget). |

### Profile B — Consolidate 3 fetch paths
| Task | Status | Evidence |
|---|---|---|
| Line-by-line diff of the 3 implementations | Done | Read `HybridMcpDataProvider.fetch()`/`fetch_series()` (`swarm/providers/mcp_data.py`), `business_state/series.py::fetch_series()`, `business_state/facade.py::get_metric_state()`, `agents/intelligence/observer.py::_query_windows`. Confirmed the already-documented divergence (`facade.py:128,136` picks `series[-1]` — last day's point — as `actual`, always, regardless of window length: deliberate "what is it right now" semantics, not a bug). **New finding, previously undocumented**: the two functions both named `fetch_series` behave differently on out-of-range windows — `HybridMcpDataProvider.fetch_series()` (DoWhy path) silently returns `None` for the whole request if the window is `<8` or `>60` days; `business_state/series.py::fetch_series()` silently **truncates** the start date to fit `max_lookback_days=90` instead of refusing. Same name, same rough purpose, opposite failure behavior — worth a decision (which behavior the unified fetcher keeps) before any merge, not just "port one of them." `_query_windows` is pure date-range shaping (comparison vs. single-window), not itself an MCP call — becomes dead code once callers move to explicit per-day `query_metrics()` calls. |
| Route all call sites through `SemanticToolset` | Done — per explicit user direction to proceed despite the risk flagged below | Initially deferred (see git history / this file's prior revision) after finding `agents/diagnostic/swarm_bridge.py`'s live DoWhy causal specialist depends on `HybridMcpDataProvider.fetch_series()`, which `SemanticToolset` v0 had no equivalent for. User explicitly overrode: "we need to do this, since it is a refactor, we can build it, even if it is breaking for now." Executed: extracted one shared no-heuristic primitive, `toolsets/semantic.py::raw_query_metric()` (thin wrapper over `build_metrics_query_args`/`call_metrics_query`, `agent_id` stays caller-supplied so existing `MCPGateway` module-scoping is preserved) — both the new `query_metrics()` tool and every legacy fetch path now call this one function. `HybridMcpDataProvider.fetch()`/`.fetch_series()` (`swarm/providers/mcp_data.py`) and `business_state/series.py::fetch_series()` had their `_resolve_measure()`/`resolve_measure()` calls replaced with a direct `definition.catalogue_metric` field read (no keyword-search fallback, no stale-id substitution — that heuristic, bug #8's root cause, is now gone from every fetch path, not just the new one). `services/measure.py::resolve_measure()`/`measure_keywords_overlap()` deleted entirely — zero remaining callers confirmed via whole-repo grep (not src-only, per this repo's own past mistake on Item 1a). `lookup_fast_path.py` needed no direct edit — both its call sites (`.fetch()`, `get_metric_state()`) route through the rewritten classes transitively. Also extended `query_metrics()` itself to emit one `EvidenceArtifact` per row for day/week/month grain (was rows[0]-only), needed for the per-day series case. **Known accepted regression** (the "breaking for now" the user accepted): a stale/missing `catalogue_metric` in `metric_registry.yaml` now surfaces as a live Cube query error instead of being silently auto-healed via keyword search — this is the intended behavior change (rule 1: no local heuristic resolves a metric), not an oversight. |
| Delete `HybridMcpDataProvider`, `business_state/series.py::fetch_series`, `_query_windows` | Done (extract-wrap-delete) | `HybridMcpDataProvider` → `McpDataProvider` (temporary DomainAgent adapter). Series body extracted to `toolsets/semantic.py::query_metric_series`; provider `fetch_series` is a thin wrapper. BSS `fetch_series` retained as brand-scoped adapter over `raw_query_metric` (still needed by facade). `_query_windows` renamed `_observation_windows` (pure date shaping, not a fetch path). Grep: zero `HybridMcpDataProvider` / `build_hybrid_bundle` in `src/`/`tests/`. |

### Profile C — Causal toolset v0 + evidence classes *(Done — Sprint 2 close 2026-09-18)*
| Task | Status | Evidence |
|---|---|---|
| `toolsets/causal.py` + `causal/service.py` (DoWhy only — no EconML in this repo; `search_breadth` per frozen §4) | Done | `src/seleric_swarm/toolsets/causal.py` (`estimate_effect`/`refute_estimate`), `src/seleric_swarm/causal/service.py` (`widening_for`, frame build, classify). Verified: `tests/unit/test_causal_toolset.py` (7 passed). |
| Evidence classification vocabulary handed to A + A1.4 migration applied across `src/` and `config/diagnostic_policies.yaml` (owner = C) | Done | Grep-clean: no `CAUSALLY_SUPPORTED_UNDER_ASSUMPTIONS` / `ASSOCIATION_ONLY` in `src/`/`config/`/`tests/`. YAML `retain_at_or_above: CAUSALLY_SUPPORTED`. Validator handoff: `agent/validation.py` + `tests/unit/test_v3_validation.py`. |
| Bug #6 regression against `search_breadth` escalation (A1.1) | Done | `test_search_breadth_widens_history_and_candidate_cap` + `test_estimate_effect_records_widened_caps_in_query` — breadth 1 > breadth 0 for both `history_days` and `candidate_cap`. |
| Bug #7 status (still intermittent) + confirm its #6-widening mitigation survives via `search_breadth` under revisions cap = 1 | Done (disposition) | Still intermittent (LLM primary_metric variance → thin history), not newly deterministic. Mitigation is now caller-chosen `search_breadth` on `estimate_effect`, independent of `max_validation_revisions=1` — widening does not consume validation revisions. Thin history refuses with `INSUFFICIENT_EVIDENCE` + `policy:thin_history` so a revision can escalate breadth instead of looping the same estimate. |
| Bug #13 explicit disposition | **Done — FIXED, disposition corrected 2026-09-18 (Sprint 3)** | Superseded the Sprint 2 "tracked ticket (not fixed)" entry, which was stale when written. Re-checked at Sprint 3 open: `tests/swarm/test_bridge_idempotency.py::test_diagnostic_bridge_is_idempotent` run **20×, 20 passed / 0 failed** — consistently green, so not bug #7's intermittent pattern. Mechanism confirmed by reading the code, not inferred from the green: commit `79e88cc` ("Refactor causal discovery logic…") added the `fallback_to_unfiltered_candidates` block at `agents/diagnostic/causal_discovery.py:224-233` — when every graph ancestor gets filtered for lacking observed movement, the graph's own ranking is re-emitted unfiltered, so a structurally-connected outcome never reports zero candidates. That addresses `docs/BUG_SHEET.md` #13's root cause (the `skipped_unobserved_no_frame` check at :192,:211-221 requiring an observations frame, which the `causal_truth` template path never populates) and does it *better* than #13's own suggested next step: the filter became a deprioritizer rather than a hard gate, so no `causal_truth` special-case was needed. The gate itself is still live and still correctly deprioritizes noise when a real signal exists. |
| Policy-gate port: 8 `policy()` conditions → tool preconditions returning `INSUFFICIENT_EVIDENCE` (A1.3) | Done | Causal toolset: no evidence / missing treatment·outcome series / thin rows / thin history → `INSUFFICIENT_EVIDENCE` + named `policy:*` warnings (`toolsets/causal.py`, `toolsets/policy_config.py`). Analytics already refuses grain/empty via same error code (Sprint 1). Intent-match gates (diagnostic wants anomaly; strategy wants mechanism) remain agent-level — no blackboard in V3. |
| Disposition of the five `config/*_policies.yaml` files (migrate into toolset config; keep YAML until port) | Done | Thresholds ported to `toolsets/policy_config.py`. YAML files retained for swarm_v2 `DiagnosticPolicies`/`SkepticPolicies`/… loaders until those callers retire. |

### Joint (A + C) — bounded-loop decisions
| Task | Status | Evidence |
|---|---|---|
| `max_validation_revisions = 1` confirmed or changed, incl. STRONG-trust + REVISE behavior | Done | Confirmed = 1 (A1 acceptance 2026-09-18). Causal escalation is `search_breadth`, not validation revisions. STRONG+REVISE with revisions exhausted → fail closed `INSUFFICIENT_EVIDENCE` (`agent/validation.py`). |
| Skeptic → validator recorded as a decision (cross-agent challenge → in-context self-review) | Done | Recorded in `CONTRACTS.md` A1 joint decisions + this Log; change in kind per `03_PROFILE_CAPABILITIES.md` §4. |

## Sprint 3

### Profile A — Mission Service
| Task | Status | Evidence |
|---|---|---|
| Temporal necessity decision (data-backed) | Done — deferred | Timed two real missions against **live** `seleric-mcp`/Cube with the fake LLM adapter (zero LLM cost, real MCP latency): a lookup mission completed in 1.45s; a full diagnostic mission (real DoWhy causal estimation, skeptic REVISE + remediation round) completed in 7.25s. Both comfortably inside `mission_timeout_s`/`ExecutionLimits.max_runtime_seconds` (120s) even after adding realistic LLM latency back in. No current mission shape needs durability beyond a synchronous request — Temporal deferred, synchronous-only for now, per the decision rule in `01_PROFILE_RUNTIME.md`. |
| `MissionQueryCache` wired in | Done (mechanism only — no real toolset call site to wire into yet) | `src/seleric_swarm/state/cache.py::MissionQueryCache` — plain per-mission memoization (`get_or_fetch`), no TTL/eviction (a mission run is bounded and short-lived, unlike `utils/ttl_cache.py`). Verified: `tests/unit/test_v3_mission_query_cache.py` (3 passed — roundtrip/hit-miss counters, dedup on repeated key, distinct keys both fetch). Not yet called from `toolsets/semantic.py::query_metrics()` — that wiring is real Sprint-2/3 Profile B work landing in parallel; this ships the cache Profile A owns, ready for that call site. |
| One trace per mission (OTel/Logfire) | Done | `src/seleric_swarm/observability/traces.py::mission_trace()` — one OTel span per mission run, reusing the existing `configure_opentelemetry()`/`TracerProvider` wiring in `observability/tracing.py` (no duplicate setup). Safe as a no-op when `otel_enabled=False` (default). Verified: `tests/unit/test_v3_mission_trace.py` (4 passed — attributes recorded, non-primitive values stringified, exception recorded + re-raised + span status set to ERROR, safe regardless of provider). |

### Profile B — Heuristic retirement
| Task | Status | Evidence |
|---|---|---|
| Delete `catalogue_grounding.py` heuristics | Done | `hints_from_catalogue`/`apply_catalogue_grain`/`ground_live_grain`/`constrain_hints_to_grain`/`dimensions_in_query` removed from `coordinator/catalogue_grounding.py`; `agents/coordinator.py` now trusts the LLM classifier's `metric_hints` directly. |
| Retire `MetricRegistry`/`MetricSemanticsRegistry` as standalone | **Resolved — the underlying problem no longer exists; class kept by explicit decision** | Two-part finding, 2026-09-18. (1) Dependency graph query (codebase-memory `query_graph`) categorized the 83 raw call-graph edges: real, non-test callers span the live swarm_v2 classifier (`coordinator/intake/llm_classifier.py::classify_query_via_llm` — 100% of production mission traffic), the diagnostic causal pipeline, skeptic, and domain configs — this is load-bearing infrastructure for the still-live pipeline, not an incidental dependency of a heuristic layer. (2) Direct read of `services/metrics.py::MetricRegistry` shows the actual "standalone duplicate registry" problem the brief worried about is **already resolved in practice**: `MetricRegistry` documents and implements itself as catalogue-first — `bind_catalogue()` makes every read method (`get()`, `all()`, `catalog_prompt()`, `ids_for_domain()`) prefer live-catalogue-derived definitions once warm; `config/metric_registry.yaml` is only a cold-start/exception overlay (`direction_bad`/`seleric_module`/`aliases`), never a competing metric list. This is exactly the "thin typed wrapper for prompt-building, not a parallel registry" the Profile B brief asked for — it already exists. **Decision (confirmed with user, 2026-09-18, given a choice between documenting-as-resolved vs. rewriting ~15 live call sites with no isolated test harness for that scale of change): keep the `MetricRegistry` class/abstraction as-is.** No further Sprint 3 or Sprint 5 action item — this is not carried forward as an open task. |
| `ActionToolset` wired to propose/confirm/commit | Done | `src/seleric_swarm/toolsets/actions.py` implements the frozen `propose_action`/`validate`/`preview`/`commit_action` surface over `actions_propose/status/commit`; confirmation is the user turn before commit. Added the four remote MCP tools and a dedicated `v3_agent` allowlist so legacy observer/domain agents do not acquire writes. Confirmation tokens are process-local, stripped from `ToolResult` provenance, and fail closed after restart/expiry; caller idempotency keys prevent duplicate commits. `tests/unit/test_action_toolset.py`: 10 passed (6 original + 4 added 2026-09-18). Full focused Profile B transport/toolset set: 28+ passed. **Scope decision, 2026-09-19: Google Ads action execution is not part of this program's requirements.** The upstream `seleric-mcp` action catalogue only ever needed to cover Meta (`pause_meta_ad`, the one pattern the Sprint 0 spike found live) — there is no Google Ads action contract to build against, and none is needed. Previously carried as "Partial, blocked on upstream Google coverage"; that framing is retired, not the class of action itself. |
| ActionToolset expiry + local-state-safety test coverage | Done | `tests/unit/test_action_toolset.py` — 4 new tests: `test_validate_reports_expired_proposal_as_not_eligible`, `test_commit_after_token_expiry_fails_closed_and_does_not_retry_forever` (pins the existing `terminal_failure`/idempotent-retry branches, `toolsets/actions.py` lines ~148-157, ~217-229, to an actual runtime path — previously unexercised by any test), `test_failed_commit_clears_local_confirmation_token_preventing_replay`, `test_exception_during_commit_releases_idempotency_key_for_retry` (pins the `_COMMIT_KEYS.pop(key, None)` cleanup on the exception path, line ~248, previously unasserted). **Scoping note**: "rollback" in these two tests means process-local confirmation-token/idempotency-key cleanup on commit failure — the frozen `ActionToolset` contract has no `rollback_action` tool and `seleric-mcp` exposes no rollback endpoint; do not read this as a claim that remote execution-reversal exists. |
| Bug #8 regression test against new toolset path | Done | New `tests/unit/test_semantic_toolset_bug_regressions.py` — `test_dimensions_are_passed_through_verbatim_no_keyword_matching`, `test_unsupported_dimension_surfaces_mcp_rejection_not_silent_empty_success`, `test_per_day_phrasing_resolves_to_day_grain_not_a_dimension`. Closes Profile B Exit Criterion 2 (`02_PROFILE_SEMANTIC_MCP.md`): the original repro ("get per day data" → `session_day_of_week` dimension via `catalogue_grounding.py::dimensions_in_query()`'s keyword match) cannot reproduce through `toolsets/semantic.py::query_metrics()` because grain and dimensions are structurally separate parameters with no token-matching step between them. The old-path heuristic and its test coverage are removed with the rest of `catalogue_grounding.py`'s deleted functions. |
| Bug #2 regression test against new toolset path | Done | Same new file — `test_two_spellings_of_same_metric_each_produce_their_own_artifact_no_silent_remap`, `test_metric_id_never_resolved_through_metric_registry`. Closes Exit Criterion 3: two spellings of the same metric (`cac` vs `metric.cac`) each reach `metrics_query` unchanged and produce independently-attributed `EvidenceArtifact`s — no `MetricRegistry`-style canonicalization step exists to get wrong. The old-path test (`test_skeptic_agent.py::test_03b_alias_spellings_of_one_metric_are_still_compared`) stays as-is — still valid for `SkepticDeps`/`MetricRegistry`, not retired until Sprint 5. |
| `semantic/cube_client.py` / `semantic/discovery.py` (brief's "Builds" list) | Not needed — redundant with existing | `toolsets/semantic.py::search_semantics()` (server-side `catalogue_search_metrics` embedding search) and `get_metric_definition()` already provide what these two files were asked to build. A local `discovery.py` vector index would duplicate server-side search — exactly the "check before building a second vector index" the brief's own `discovery.py` bullet warns against. Not built, and not planned. |
| `query_metric_series()` / `fetch_series()` Sprint 3 gap (brief: "no `SemanticToolset` equivalent yet") | Done | Already closed as of the Sprint 2 extract-wrap-delete work — `toolsets/semantic.py::query_metric_series()` (lines 97-169) is the DoWhy multi-metric DataFrame path; `swarm/providers/mcp_data.py::McpDataProvider.fetch_series()` (~281-314) delegates to it directly; `agents/diagnostic/swarm_bridge.py`'s DoWhy causal specialist reaches it transitively through that delegation. No further code change needed — this row corrects `02_PROFILE_SEMANTIC_MCP.md`'s "Builds" section, which still described this as an open Sprint 3 gap. |
| `BusinessStateService.get_metric_state()` folding into `query_metrics()` | Deferred — explicit decision, not a silent skip | Evaluated 2026-09-18 for Sprint 3 inclusion and deferred. `get_metric_state()` is called on `lookup_fast_path.py`'s live ungrained-lookup fast path and anomaly-history lookups — a proven-nonempty, proven-live caller graph, unlike the Sprint 2 `resolve_measure()` deletion (safe specifically because a whole-repo grep proved *zero* remaining callers first). Its "last-day-point, not period-sum" semantics is already characterized as an intentional difference (`tests/replay/test_data_access_characterization.py::test_characterize_multi_day_window_last_point_vs_period_total`). Folding this now would be a live-call-site behavior swap with no characterized drop-in replacement — the exact strangler-fig-discipline violation this repo's rules exist to prevent, and unlike Sprint 2's fetch-path override, there's no proven-empty caller graph to justify it. No Profile B exit criterion requires this in Sprint 3. Revisit once a `query_metrics(as_of=..., grain="day")`-equivalent has its own characterization test proving last-day-point parity. |
| **Gaps left by the `catalogue_grounding.py` heuristic deletion** | **Done** | Closed part (1): `agents/coordinator.py::resolve_grain_for_metrics()` replaces the hardcoded `resolved_dimensions: list[str] = []` — grain is resolved via the live `seleric-mcp` dimension resolver, corroborated against the canonical metric's own `supported_dimensions`, no local keyword table. **Root cause of the 2026-09-19 test failure found and fixed**: it was not a missing per-token fallback — it was that `bootstrap.get(cat_id)` (the corroboration step) silently returns `None`/empty on an unwarmed `CatalogueBootstrap` cache, and nothing on the `classify()` call path had warmed it (only a real metric *fetch* does, elsewhere). Every live-resolved dimension was failing corroboration for that reason alone, independent of whole-sentence-vs-per-token resolution. Fixed with one line — `await bootstrap.refresh_if_stale()` before the resolve, same warm-before-read `_resolve_one_term()` used to do. Added the per-token fallback (`query_tokens_for_grain()` + a retry loop in `resolve_grain_for_metrics()`) anyway as defense-in-depth for sentences the live resolver can't parse whole, mirroring the deleted `_resolve_metric_term()`. Test coverage: `tests/unit/test_coordinator_classify.py`'s three tests (gold set + both grain tests) now pass live against `seleric-mcp`. `tests/unit/test_catalogue_grounding.py` was rewritten (585 lines of deleted-function tests removed, 9 kept for the surviving surface) rather than left stale. Part (3) closed the same day: `tests/replay/` run live in full (40/40 passed, including `test_lookup_v1.py`'s two full-mission runs through the exact `lookup_v1` `classify()` path this deletion touched, and the original bug #8/#14 `test_data_access_characterization.py` 7/7) — recorded as the fresh characterization evidence `CHARACTERIZATION_LEDGER.md`'s retirement left open. Full suite after the fix: 903 passed, 1 failed (the same pre-existing `test_health_combo_never_returns_running`), 4 skipped. |

### Profile C — Model/Skeptic port
| Task | Status | Evidence |
|---|---|---|
| Registry wiring question (consolidation-plan Item 3) | Done | Closed by source read 2026-09-18: `agents/prediction/swarm_bridge.py:61-62` builds an `InMemoryModelRegistry` from `scenario["forecast_truth"]` in `_fixture_deps()` whenever `self._deps` is None; the YAML path resolves `config/model_registry.yaml`, which does not exist — only `config/model_registry.example.yaml` (573 B, both entries `status: candidate`). Answer: neither a YAML-seeded registry nor an empty one in practice — it is fixture-seeded, and there are no approved production models. |
| `toolsets/models.py` (greenfield, not a port — priority confirmed with user 2026-09-18: **full build incl. a real forecaster**) | Done | `src/seleric_swarm/models/service.py` (exponential smoothing via `statsmodels.tsa.holtwinters` — Holt linear trend at ≥10 points, simple exponential smoothing below that; deterministic, so a forecast is reproducible from stored provenance), `src/seleric_swarm/models/evaluation.py` (prediction→actual: MAE/MAPE/interval coverage), `src/seleric_swarm/toolsets/models.py`, `config/model_registry.yaml` (new; 4 approved daily forecast models against real `config/metric_registry.yaml` ids). Added `statsmodels>=0.14` to `pyproject.toml` — it was installed transitively via `dowhy` but Sprint 0 deliberately trimmed it as unused, so re-adding is a recorded decision. **Scope honesty:** `forecast` is real; `predict_ltv`/`predict_propensity` refuse with `INSUFFICIENT_EVIDENCE` + `policy:no_approved_model` because this deployment has no per-customer labels and no feature store. Those are not stubs — the registry gate is the control, and inventing an approved entry is what would *start* the fabrication. Interval is mandatory (a point forecast with no stated uncertainty is a gap per `forecast_validator.py`); a collapsed interval warns rather than implying certainty. Verified: `tests/unit/test_models_toolset.py` (22 passed). |
| `EvidenceValidator` content checks, two signals preserved (not merged) | Done | `agent/validation.py` became the package `agent/validation/` (`signals.py`, `trust.py`, `verdict.py`, `__init__.py`), public names unchanged. `score_trust`/`decide_verdict` are **faithful ports** of `agents/skeptic/scoring/*` — min-merge of duplicate signals, the `_alt_elimination` synthetic signal, dimension-weight renormalization when a feeder is absent, the 0.3 blocking cap, the three REVISE conditions, and `REVISE_CATEGORIES` copied exactly (it deliberately omits evidence/provenance/alternative_hypothesis/strategy — widening it turns PASSes into REVISEs). What *feeds* them is V3-native (`signals.py` over `ArtifactStore` artifacts) because 9 of swarm_v2's 11 validators depend on plumbing V3 lacks. `ValidationOutcome` widened from binary `ok`/`reason` to carry `verdict` + `trust_score`/`trust_label`/`trust_components` separately — Sprint 2's shape had nowhere to put two signals. Thresholds (incl. two numbers hard-coded in `verdict_engine.py:54,78`) moved into `toolsets/policy_config.py`; `config/skeptic_policies.yaml` stays on disk for `agents/skeptic/*`. **Non-regression:** `tests/skeptic/` 34 passed — the ported source is untouched and still green. |
| Bug #12 STRONG+REVISE reproduced + loop behavior under the revisions cap recorded | Done | `tests/unit/test_v3_validation_signals.py` (19 passed). All three routes to the #12 shape pinned separately: unresolved alternative (`priority >= 6`), blocking evidence gap, and a `source_conflict` warning — each asserting `trust_label == "STRONG"` **and** `verdict == "REVISE"` on the same outcome as distinct fields. `test_the_two_signals_are_structurally_independent` asserts by signature inspection that `score_trust` cannot see a verdict and `decide_verdict` sees trust only as a float. `test_thresholds_leave_room_for_strong_plus_revise` pins the mechanism itself (STRONG 0.72 > revise_below 0.55) — if those ever crossed, #12's shape would become silently unreachable. **Loop behavior decision (new, recorded):** REVISE consumes a revision and re-prompts; **REJECT fails closed immediately without consuming one**, since re-prompting a claim whose evidence contradicts it buys the same answer for budget. Matches swarm_v2, where REJECT ended the mission and only REVISE triggered remediation. Exhaustion mid-REVISE → `status="failed"`, `error_code="INSUFFICIENT_EVIDENCE"`, per the A1 joint decision. |

### Profile C — Behavioral parity harness
| Task | Status | Evidence |
|---|---|---|
| Full suite vs. Sprint 1 bar (no new failures) | Done | `./.venv/Scripts/python.exe -m pytest -q --ignore=tests/integration/test_minio_blob_store_integration.py` (2026-09-18) → **911 passed, 1 failed, 4 skipped** in 522s. Sole failure is the same pre-existing `tests/unit/test_api_scenario_matrix.py::test_health_combo_never_returns_running` carried since the Sprint 0 baseline — no new failures. Up from 842 passed at Sprint 1 close. Ruff clean on all new/modified files. |
| Replay missions diffed old specialists vs. new toolsets (same anomalies/hypotheses/classification, or written explanation per divergence) | Done | `src/seleric_swarm/evals/parity.py` + `tests/replay/test_v3_parity.py` (3 passed). Bar is **structural equivalence** (user decision 2026-09-18): the set of `(metric_id, direction)` anomalies, evidence classifications, hypothesis statements. Floats are reported, not asserted — exact numeric parity would fail on intentional changes, chiefly that `detect_anomalies` now takes its baseline from evidence rather than `BusinessStateService`, which is rule 5 working as designed. Both paths are driven **directly with the same evidence**, not through an LLM loop, so a divergence is attributable to the capability rather than to tool selection (that is Sprint 4's eval gate). Real `AnomalyAgent`+`Blackboard` on one side, real `detect_anomalies` on the other; both flag `metric.net_sales` moving `down` on an identical 8-day series. `EXPECTED_DIVERGENCES` registers known-intentional differences with reasons, so the report separates "changed on purpose, here's why" from "don't know why this moved" — and `test_parity_report_flags_an_unexplained_divergence` proves the harness can actually fail, which a gate that only ever passes cannot. |

## Sprint 4

### Profile A — Cutover gate
| Task | Status | Evidence |
|---|---|---|
| Toolset wiring (prerequisite for the row below — not itself a Sprint 4 checklist line, but blocking) | Done | `agent/agent.py` registers all 15 tools with a real implementation (`toolsets/{semantic,analytics,causal,models,actions}.py`) on `SelericAgent` — previously zero tools were registered. Found and fixed a real, repo-wide latent bug while wiring: all 5 toolset modules imported `SelericDeps` only under `if TYPE_CHECKING:`, so `RunContext[SelericDeps]` annotations resolved fine for a human/mypy reading the file but raised `NameError: name 'SelericDeps' is not defined` the moment pydantic_ai's real tool-registration path called `get_type_hints()` on them at runtime — nothing had exercised that path before this wiring existed. Promoted the import to a real top-level import in all 5 files (no circular-import risk — `agent/dependencies.py` doesn't import `toolsets/*`). Also fixed 2 pre-existing `mypy` errors surfaced along the way (`agent/validation/signals.py`'s `_payload()` needed `type[BaseModel]` not bare `type`; `toolsets/analytics.py::detect_anomalies` needed an explicit `cast` where a list comprehension's `value is not None` filter doesn't survive tuple unpack/slice narrowing) and added `statsmodels.*` to `pyproject.toml`'s mypy override list (same treatment as `dowhy`/`pandas`/`numpy` — untyped third-party lib). The 0%-traffic stub path (`TestModel(call_tools=[])`) is unaffected — tools are registered but never invoked by it, verified explicitly. New tests: `tests/unit/test_v3_agent_wiring.py` (3 cases: all 15 tools present, all 15 register cleanly, stub model still calls zero of them). `ruff check src` + `mypy src` both clean repo-wide (341 source files) after all fixes. |
| Replay set run through new loop, cost/latency recorded | Partial — 3 real queries run, 2 real bugs found | Flagged the LLM-spend decision to the user first (real cost, real `seleric-mcp` calls) rather than substituting a zero-cost scripted model and calling it cost data; user confirmed live testing is intentional (`V3_AGENT_ENABLED=true` in the real `.env`). Ran 3 queries through `agent/runner.py::run_v3_mission()` against the real Azure-backed model + live `seleric-mcp` (not a mock): "What were net sales yesterday?" (10.64s, `INSUFFICIENT_EVIDENCE` — see finding 1 below), "What were gross sales yesterday?" (**144.73s**, `status=completed` with an **empty `final_response`** — see finding 2), "Why has CAC increased over the last three days?" (56.62s, completed, coherent response citing the anomaly detector). **Finding 1 (correctness):** the "net sales yesterday" query's own error text says `2024-09-16`, not `2026-09-16` — the model appears to be resolving "yesterday" from its own training-era sense of the date rather than the `as_of` passed into `SelericDeps`, because `agent/instructions.py`'s system prompt never states the current date/`as_of` as text; nothing in `SelericDeps` is currently surfaced to the model as "today is X." Real, not a fixture quirk. **Finding 2 (validation gap):** a `status="completed"` mission with an empty `final_response` should be structurally impossible — `agent/validation.py::EvidenceValidator.validate()` (Sprint 2 Profile A) explicitly checks for exactly this — but `agent/runner.py::run_v3_mission()` calls `agent.run()` directly, **not** `run_validated_mission()`, so the validator never runs on the live path. Same gap applies to `agent/limits.py::ExecutionBudgetTracker` — the 144.73s run exceeded `ExecutionLimits.max_runtime_seconds` (120.0 default) with nothing enforcing it, because nothing calls `check_runtime()` on this path either. Both are real, live-observed gaps, not theoretical — reported to the user rather than silently patched into someone else's in-progress `runner.py`, since it's actively being built by a concurrent session. |
| Execution limits tuned to acceptable cost/latency | Not started | Can't tune what isn't enforced yet on the live path (see finding 2 above — `ExecutionBudgetTracker` isn't wired into `runner.py`). Wiring it is the prerequisite, and it's not this session's file to edit unilaterally mid-build by another session. |
| Figures documented | Partial | The three real timings above are the first real figures against the live path (10.64s / 144.73s / 56.62s) — token/cost figures were not captured (`agent.run()`'s `usage()` wasn't read back in this pass); a follow-up run should capture it explicitly now that the harness exists. |
| Canary flag flipped | Clarified, not a Sprint-4 "flip" in the plan's sense | `V3_AGENT_ENABLED=true` in the real `.env` is confirmed intentional by the user — but it is a **local on/off flag** (`main.py`'s `v3_enabled = runtime.settings.v3_agent_enabled` routes 100% of `/v1/missions` traffic when true), not the percentage-based canary `SPRINT_PLAN.md`'s Sprint 4 gate describes. Left as the user's explicit call; not edited by this session. `00_OVERVIEW.md` already carries the same clarification ("local flag, not a production canary %"). |

### Profile B — Cleanup
| Task | Status | Evidence |
|---|---|---|
| `ProviderRegistry` deleted | Done | `src/seleric_swarm/registry/provider_registry.py` + `config/provider_registry.yaml` deleted. Sole consumer `swarm/providers/provider_selection.py::ConfiguredAnomalyDetector` (built by `swarm/providers/mcp_data.py::build_mcp_bundle()`) now hardcodes the shipped config's only two real overrides (`_ROBUST_ZSCORE_DOMAINS={"commerce"}`, `_ROBUST_ZSCORE_METRICS={"metric.spend","metric.net_profit"}`) instead of reading YAML through a swappable registry class. `force_robust_zscore` and the sparse-history→template fallback in `detect()` untouched. Deleted deliberately without waiting on swarm_v2's Sprint 5 retirement even though `ConfiguredAnomalyDetector` is still `AnomalyAgent`'s live 100%-traffic detector — explicit user call: this repo has nothing in production yet. Updated `tests/unit/test_provider_selection.py` (removed `ProviderRegistry`/`_FakeRegistry`; two assertions that had assumed `metric.net_sales` resolves to `"template"` were actually wrong under the real shipped config — it's domain `commerce`, so it always resolved to `robust_zscore`, the isolated fake registry in the old test just never exposed that — swapped those cases to `metric.units_sold`, product domain) and `tests/unit/test_business_state_live.py`. `./.venv/Scripts/python.exe -m pytest -q tests/unit/test_provider_selection.py tests/unit/test_business_state_live.py tests/unit/test_business_state_mission_integration.py` → 14 passed. |
| Whole-repo grep confirms no dangling callers | Done | `grep -rn "ProviderRegistry\|provider_registry"` — zero hits in `src`/`tests`/`config`. Remaining hits are `provider_selection.py`'s own docstring (explains what was removed), and historical/doc mentions (`docs/refactor/*`, `diagrams/*.mmd`, `docs/features/business-state-service/*`) not in scope for this deletion. Two stale code comments referencing the deleted YAML (`swarm/specialists/anomaly.py:105`, `tests/unit/test_business_state_mission_integration.py:58`) updated to name the new hardcoded set. Full suite after deletion: **901 passed, 1 failed (pre-existing, unrelated — `test_health_combo_never_returns_running`, a live-data-dependent flake, same failure recorded since the Sprint 0 baseline), 4 skipped.** |

### Profile C — Greenfield capability (additive, non-blocking)
| Task | Status | Evidence |
|---|---|---|
| `contribution_analysis`, `segment_decomposition`, `funnel_decomposition`, `cohort_analysis` (moved from Sprint 1 — no source to port) | Done | Math in `analytics/{breakdown,funnel,cohort}.py` (pure, unit-testable without a `RunContext`); wrappers in `toolsets/analytics.py`. **A1.2 extended to all six** analytics functions — filed as amendment A1.9 rather than widened silently. Three decisions worth knowing: (1) `contribution_analysis`'s denominator is the sum of supplied segments, because `semantic.py::drilldown` computes a parent total and discards it and skips null rows — so shares are of the *observed parts* and a warning says so; (2) funnel step order is an explicit `policy_config.FUNNEL_STEPS` constant, and the five chosen metrics all share the `sessions` denominator (verified `config/metric_registry.yaml:148-215`), which is what makes conversion `rate[i+1]/rate[i]` rather than a cross-axis division the catalogue warns against; (3) `cohort_analysis` does not assume a time series — `repeat_rate` is a `windowed_point` with no daily grain. `segment_decomposition` refuses pooled `query_metrics` breakdown rows (they all carry `dimensions={}` and are indistinguishable) rather than averaging them. Verified: `tests/unit/test_analytics_breakdowns.py` (28 passed). |
| `toolsets/knowledge.py` | Done | `knowledge/{corpus,search}.py` + `toolsets/knowledge.py`; corpus at `knowledge_corpus/` with a README documenting ingestion. **File-backed, not the existing hybrid search** — `conversations/phase7.py` is real and wired, but it is synchronous (tools are async), has no `artifact_type` filter, and searches a Postgres `artifacts` table that `ctx.deps.artifact_store` does not write to, so it would never surface anything this agent produced and answers "what did we discuss" rather than "what does the SOP say". **Rule 12 is enforced structurally**: `search_knowledge` writes zero artifacts, so a document can never be cited as evidence for a numeric claim no matter what the model does with the text — pinned by `test_knowledge_never_writes_an_artifact`. **The corpus ships empty** and says so (`success=True` + `policy:empty_knowledge_corpus`), kept distinguishable from "documents exist, none match". Verified: `tests/unit/test_knowledge_toolset.py` (17 passed). |
| `toolsets/experiments.py` | Done | `experiments/{registry,stats}.py` + `toolsets/experiments.py` + `config/experiment_registry.yaml` (mirrors the Sprint 3 model-registry pattern; no migration for a table nothing writes). `estimate_sample_size` is **real power analysis** via `statsmodels.stats.power.NormalIndPower` + `proportion_effectsize` — zero new dependencies, and pinned against a known figure (2.0%→2.5% at 80% power ≈ 13.8k per variant). **`mde` is absolute, and the summary says so**, because relative-vs-absolute is how that number ends up silently several times wrong. **The registry is a control**: `evaluate_experiment` refuses an unregistered id rather than scoring supplied evidence, since measuring lift against an undeclared control is how a system asserts a result nobody designed — which is also why the shipped registry is empty. **Significance is never asserted**: evidence carries point values with no interval or sample count, so it stays unknown rather than reporting "not significant". Verified: `tests/unit/test_experiments_toolset.py` (23 passed). |
| Register all 23 frozen functions on `SelericAgent` (Profile A file, touched by C) | Done | `agent/agent.py::TOOLS` 15 → 23 plus `knowledge`/`experiments` imports; `tests/unit/test_v3_agent_wiring.py` updated (count, name set, and the test name itself, which said "fifteen"). All 23 register without a schema error — the trap that guards is `RunContext`/`SelericDeps` imported only under `TYPE_CHECKING`, which resolves fine for mypy and raises `NameError` at real tool-registration time. Every new module imports them at runtime for that reason. |
| Full suite vs. post-merge bar | Done | `./.venv/Scripts/python.exe -m pytest -q --ignore=tests/integration/test_minio_blob_store_integration.py` (2026-09-19) → **971 passed, 1 failed, 4 skipped** in 504s. Up exactly 68 from the 903 post-merge bar — the 68 new tests, no collateral. Sole failure remains the pre-existing `tests/unit/test_api_scenario_matrix.py::test_health_combo_never_returns_running` carried since the Sprint 0 baseline. `ruff check src tests` clean repo-wide, including two pre-existing F401s inherited from earlier sprints. |
| Frozen-surface audit 23/23 | Done | `CONTRACTS.md` §4 declares 23 functions; all 23 now import and exist (`semantic` 4/4, `actions` 4/4, `analytics` 6/6, `causal` 2/2, `models` 3/3, `knowledge` 1/1, `experiments` 3/3). This is the one check a green suite cannot make — it catches a *missing* deliverable rather than a broken one. Also corrected a figure: earlier passes recorded "13/20, 7 gaps", which was my arithmetic error; the gap list was right, the totals were not. Fixed in `SPRINT_PLAN.md` and `00_OVERVIEW.md`. |

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
    Filed as amendment A1.1; blocked C's Sprint 2 until A1 acceptance
    (accepted 2026-09-18 — see Log entry below).
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
- 2026-09-18: Sprint 1 Profile A executed (all four tasks — see table above
  for evidence). Added `pydantic-ai-slim` dependency. Full suite via the
  project `.venv` (the correct environment, not the machine-wide `python` —
  see the environment finding above): 770 passed, 1 failed (the same
  pre-existing `test_health_combo_never_returns_running`), 5 skipped —
  parity with the Sprint 0 baseline preserved, no regressions from the new
  code (which isn't wired into any live traffic path).
- 2026-09-18: Sprint 2 Profile A executed (both tasks — see table above for
  evidence). Also re-linted/type-checked every V3 file added so far
  (`ruff check` + `mypy`, both clean) and the earlier dispatch.py
  conversational-reply/caching change from this same session, since the
  user asked for the profile work to be bug-free, not just present. Full
  suite via `.venv`: 780 passed, 1 failed (the same pre-existing
  `test_health_combo_never_returns_running`), 5 skipped.
- 2026-09-18: Checked Sprint 1/2 for UI/UX connectivity per user request.
  Found and fixed a real bug in `orchestration/dispatch.py`: both fast-path
  completions (`_complete_conversational_mission`,
  `_complete_overview_mission`) built a `MissionResult` with a populated
  `trace` field but never wrote `trace` into the raw dict persisted to the
  store — `api/office/gateway.py`'s Office UI snapshot reads `trace`
  straight off that raw dict (`api/office/normalize.py::build_office_snapshot`),
  so every greeting/business-overview mission showed no request/session
  correlation in the UI. Fixed both call sites; added regression
  assertions to `tests/unit/test_dispatch_routing.py` (would have failed
  before the fix, pass now). Also found and documented (not fixed — not
  actionable yet, no V3 toolsets exist to produce a real mission) a bigger
  gap: `office-ui/` and `build_office_snapshot` are entirely swarm_v2-shaped
  (`mission_lead`/`leadership_epoch`/`handoff_history`/typed artifact
  buckets) and have zero mapping to the V3 `MissionResult`/`ArtifactStore`
  shape — noted in `01_PROFILE_RUNTIME.md` Key risks + added as exit
  criterion 5, since nothing in this folder had planned for the existing
  frontend before. Full suite after the fix: unchanged pass/fail counts
  (still only the one pre-existing live-data failure).
- 2026-09-18: Built the V3-adapter fix for the Office UI gap above, rather
  than leaving it only documented. Added `api/v3_state.py` (shared V3
  Mission/Artifact store singletons — `api/missions.py` previously built a
  throwaway `InMemoryArtifactStore()` per request and never persisted the
  `Mission` at all, so a mission was unretrievable the instant the response
  was sent). Added `api/office/v3_adapter.py::v3_raw_snapshot()`, translating
  a V3 `Mission`+`Artifact`s into the swarm_v2-shaped raw dict
  `build_office_snapshot` already renders (reuses that logic instead of
  forking it; V3's one agent maps to the `"coordinator"` office-ui node as
  a stand-in, not a real mapping — still tracked as exit criterion 5).
  Wired into `api/office/gateway.py::_raw()` as a fallback when a mission
  id isn't in the swarm_v2 store. Found two more real bugs while wiring
  this: (1) the stub agent's fixed `TestModel` output carried a literal
  `mission_id="stub"`/empty `query` that leaked into the API response
  instead of the real request's values — fixed in `api/missions.py` by
  correcting those fields post-run; (2) naming the new `MissionStore`
  method `complete()` collided with
  `tests/unit/test_numeric_audit_coverage.py`'s textual regex for detecting
  new unaudited LLM-prose call sites (`\.complete\(` matches `llm.complete(`
  AND `mission_store.complete(` — a false positive, not a real audit gap) —
  renamed to `finish()` rather than diluting that allowlist with a
  non-LLM entry. 9 new/updated tests, all passing. Full suite: 806 passed,
  1 failed (the same pre-existing live-data issue), 5 skipped — lint/mypy
  clean on every touched file.
- 2026-09-18: Sprint 2 Profile B executed. Found and fixed a real
  duplication bug first: parallel Profile A work had built canonical
  `SelericDeps`/`ToolResult`/artifact schemas in `agent/dependencies.py`,
  `agent/output.py`, `agent/artifacts.py` while my earlier `agent/contracts.py`
  sat orphaned and silently mismatched — `toolsets/semantic.py` was using
  the wrong copy. Deleted `agent/contracts.py`, repointed everything to the
  canonical modules, added `ToolResult`'s missing envelope-invariant
  validator to `agent/output.py`. Diffed the three legacy fetch paths (found
  a new, previously-undocumented divergence: two same-named `fetch_series`
  functions disagree on out-of-range-window handling — truncate vs. `None`).
  Initially deferred routing call sites through `SemanticToolset` after
  finding the live DoWhy causal specialist depends on a `fetch_series`
  capability the new toolset didn't have — user explicitly overrode that
  caution ("we need to do this... even if it is breaking for now"). Executed:
  extracted `toolsets/semantic.py::raw_query_metric()` as the one shared
  no-heuristic MCP-call primitive; rewired `HybridMcpDataProvider.fetch()`/
  `.fetch_series()` and `business_state/series.py::fetch_series()` to use it
  directly off `MetricDefinition.catalogue_metric`, deleting
  `services/measure.py::resolve_measure()`/`measure_keywords_overlap()`
  entirely (zero remaining callers, confirmed via whole-repo grep). Extended
  `query_metrics()` to emit one artifact per row for day/week/month grain
  (was rows[0]-only). Found and fixed one real collateral regression from
  the full suite catching it: `test_ontology_service.py`'s fixture relied on
  the now-deleted keyword-search fallback — updated the fixture to set
  `catalogue_metric` directly, matching the new no-fallback contract. Final
  full suite via `.venv`: **811 passed, 1 failed (the same pre-existing
  `test_health_combo_never_returns_running`), 4 skipped** — net gain vs. the
  780/1/5 Sprint 2 Profile A baseline, no new unresolved failures. Live
  characterization suite (`tests/replay/`) re-run clean against production
  data after all changes: 45/45 passed.

- 2026-09-18: **Sprint 1 / Profile C executed** — Analytics toolset v0.
  New: `analytics/{__init__,comparison,grain}.py`,
  `toolsets/analytics.py`, `tests/unit/test_analytics_{grain,toolset,semantic_handoff}.py`
  (31 tests, all passing). Modified, behavior-preserving:
  `swarm/specialists/observer.py::_post_comparison_deltas` now delegates its
  pairing/subtraction to `analytics.comparison.period_deltas`. Full suite
  **842 passed / 1 failed / 4 skipped**, the failure being the same
  pre-existing `test_health_combo_never_returns_running`. Nothing wired into
  `orchestration/dispatch.py` — swarm_v2 keeps 100% of traffic.

  **Correction to two defects this profile reported during its 2026-09-18
  design review.** Both were real when written and both are now fixed; the
  review's text predates the fixes, not the other way round:
  - *"Profile B's `query_metrics` collapses a multi-row series to
    `rows[0]`"* — **fixed by B**. It now emits one `EvidenceArtifact` per
    Cube bucket with `period_start == period_end` for a real grain.
    Verified by `test_day_grain_query_emits_one_artifact_per_day_not_one_per_window`.
  - *"Profile A ships duplicate, divergent frozen contracts"* — **fixed by
    A**, which deleted the orphaned `agent/contracts.py` during Sprint 2.
    `agent/{dependencies,output,artifacts}.py` are canonical; Profile C
    imports those.

  **New finding, routed to Profile B (latent, not currently firing).**
  `services/mcp_query.py::row_date` identifies a Cube bucket by matching a
  `.day`-suffixed column name. When it returns `None` for a day-grain
  result — a renamed view, a different granularity suffix, an aggregate row
  mixed into the series — `toolsets/semantic.py::query_metrics` falls back
  to `period_start`/`period_end` for *every* row, emitting N artifacts that
  each declare `grain="day"` while spanning the whole window. That is
  `docs/BUG_SHEET.md` #14's shape multiplied by N, and B's own path reports
  `success=True`. Not hypothetical: it is what the first draft of C's
  fixture hit. C's A1.2 precondition catches it before it can reach a
  Finding (`test_unparseable_date_column_degrades_to_window_labels_and_is_caught`),
  so this is a defence-in-depth note rather than a live bug — but the
  heuristic is a single string match standing between a renamed Cube view
  and silently wrong evidence labels.

  **Needs an A1 addendum:** `detect_anomalies` returns a new error code,
  `METHOD_NOT_AVAILABLE`, for the frozen-but-unimplemented
  `method="seasonal"`. Refusing is the honest option — running
  `robust_zscore` instead would answer a different question than the agent
  asked — but the code is not in `CONTRACTS.md` yet.
- 2026-09-18: Merged conflicted `SPRINT_PLAN.md` / `TASK_SHEET.md` after
  parallel Profile B + Profile C work. Sprint plan checkboxes brought in
  line with this sheet: Sprint 1 A/B/C Done; Sprint 2 A Done; Sprint 2 B
  Done except class deletion;
  Sprint 2 C was still blocked on A1.1 at that point.
- 2026-09-18: **Doc-drift fix, no code changes.** That merge above dropped
  Sprint 3 Profile A's three completed tasks back to "Not started" — the
  code (`agent/limits.py`, `agent/validation.py`'s bounded retry,
  `state/cache.py`, `observability/traces.py`, `api/v3_state.py`,
  `api/office/v3_adapter.py`) was never touched or lost, only this sheet's
  record of it. Restored the Sprint 3 Profile A table above with full
  evidence. Re-ran the full suite after restoring the docs (no code changed)
  to reconfirm nothing regressed across the parallel B/C work + this
  session's earlier A work landing together. Repo-wide `ruff check src` and
  `mypy src` both clean (330 source files). Full suite via `.venv`:
  **847 passed, 1 failed, 5 skipped** — the 1 failure is still the same
  pre-existing live-data issue (`test_health_combo_never_returns_running`),
  unrelated to any of this. Also refreshed `00_OVERVIEW.md`'s status line
  (was still Sprint-1-only) to summarize actual progress through Sprint 3
  A / Sprint 2 B / Sprint 1 C — same "don't assume this line" caveat kept,
  it's a pointer, not a status of record.
- 2026-09-18: **Amendment A1 ACCEPTED** (three-profile sign-off). Applied into
  frozen `CONTRACTS.md` §2/§4: `search_breadth` on `estimate_effect`;
  Analytics grain precondition; named error codes
  (`EVIDENCE_GRAIN_MISMATCH`, `INSUFFICIENT_EVIDENCE`,
  `EXECUTION_LIMIT_EXCEEDED`); A1.4 owner = Profile C; A1.6 closed on
  `pydantic-ai-slim`. Joint decisions: keep `max_validation_revisions = 1`
  (causal ladder is `search_breadth`); skeptic→validator recorded as a
  change in kind. Profile C Sprint 2 Causal toolset is **unblocked**. Next
  executable task: Causal toolset v0 extract-wrap-delete from
  `agents/diagnostic/*`.
- 2026-09-18: **Sprint 2 Completeness closed** (Profile C Causal + A1.4 +
  policy/bug dispositions + thin validator vocab handoff). Added
  `toolsets/causal.py`, `causal/service.py`, `toolsets/policy_config.py`;
  migrated `CAUSALLY_SUPPORTED_UNDER_ASSUMPTIONS`→`CAUSALLY_SUPPORTED` and
  `ASSOCIATION_ONLY`→`ASSOCIATION` across `src/`/`config/`/`tests/`;
  `EvidenceValidator` checks mission causal classifications. Bug #6 covered
  by `search_breadth` tests; #7 disposition (still intermittent; widening
  via `search_breadth` independent of revisions=1); #13 tracked ticket
  (fixture/template gap — not silently fixed). Profile B residuals:
  characterization Partial (day-1 ledger = 2026-09-18); `HybridMcp*`
  deletion Deferred to Sprint 3 (live path). Focused suite: causal +
  validation + analytics + A1.4 claim/diagnostic/skeptic tests green.
- 2026-09-18: **Sprint 2 flow fix + Profile B residuals.** Fixed inverted
  `search_breadth` history gate (`MIN_HISTORY_DAYS` floor only). Cleaned
  dead causal/policy/validator residue.
  Hybrid extract-wrap-delete: `semantic.query_metric_series`;
  `HybridMcpDataProvider`→`McpDataProvider`; `build_hybrid_bundle`→
  `build_mcp_bundle`; `_query_windows`→`_observation_windows`.
- 2026-09-18: **Merge conflict resolved (`tripti-seleric-agent` → `gaurav`,
  commit `197f799`) and full-tree Sprint 0–2 completeness check.** The
  merge landed with 4 conflicted files after Profile B (this session) and
  Profile C (parallel session) both touched `toolsets/semantic.py` and
  `agent/contracts.py` the same day. Resolved: kept `agent/contracts.py`
  deleted (both sides had independently converged on it being the orphaned
  duplicate; Profile A's canonical `agent/dependencies.py`/`agent/output.py`/
  `agent/artifacts.py` win); kept HEAD's multi-row `query_metrics()` in
  `semantic.py` over the older single-row version; repointed the Analytics
  toolset's own stale `agent.contracts` imports (`toolsets/analytics.py`,
  `analytics/grain.py`, `tests/unit/test_analytics_toolset.py`,
  `tests/unit/test_analytics_grain.py`) to the same canonical modules.
  Confirmed zero remaining `agent.contracts`/conflict-marker references
  repo-wide (whole-repo grep). Full suite post-merge: **863 passed, 1 failed
  (the same pre-existing `test_health_combo_never_returns_running`), 4
  skipped**. `ruff check` clean on every file touched during resolution.
  **Completeness check, Sprint 0 through Sprint 2, all three profiles: no
  gaps found beyond what this sheet already discloses as Partial/Blocked**
  (`ActionToolset`'s Google Ads action pending upstream `seleric-mcp`
  support). See Sprint 3
  table above for what's next.
- 2026-09-18: **Sprint 3 Profile B — `MetricRegistry` retirement scope
  corrected.** Rather than accept "73 inbound dependencies" as an opaque
  blocker, ran the actual dependency graph query (codebase-memory
  `query_graph`) and categorized all real (non-`__file__`, non-test) callers.
  Finding: `coordinator/intake/llm_classifier.py::classify_query_via_llm` —
  swarm_v2's live classifier, 100% of production mission traffic — depends
  on `MetricRegistry` directly, alongside the diagnostic causal pipeline
  (`causal_discovery.py`, `causal_graph_builder.py`, `ontology.py`,
  `synthesis.py`) and `agents/skeptic/context.py`. This was never a Sprint-3
  incidental cleanup; it's load-bearing infrastructure for the pipeline that
  doesn't retire until Sprint 5. Moved the item from Sprint 3 to Sprint 5's
  old-pipeline-deletion list in `SPRINT_PLAN.md`, with the same evidence.
  `ActionToolset`'s Google Ads gap remains an external (upstream
  `seleric-mcp`) blocker, not something further local work resolves.
- 2026-09-18: **Sprint 3 Profile B — real unblocked work closed** (plan
  reviewed and approved by user first). Closed Profile B's own Exit
  Criteria 2 and 3: new `tests/unit/test_semantic_toolset_bug_regressions.py`
  proves bug #8 (dimension misclassification) and bug #2 (metric-ID
  canonicalization) cannot reproduce through the new consolidated
  `toolsets/semantic.py` path — both bugs previously only had regression
  tests against the old swarm_v2 path. Added 4 tests to
  `tests/unit/test_action_toolset.py` closing real, previously-unexercised
  code paths (confirmation-token expiry, local-state cleanup on failed
  commit — explicitly scoped as process-local safety, not a new remote
  rollback capability, since the frozen contract has no such tool).
  Corrected two items that were carried as open in
  `02_PROFILE_SEMANTIC_MCP.md`'s "Builds"/gap list but were already
  resolved or never needed: `semantic/cube_client.py`/`discovery.py`
  (redundant with existing `search_semantics()`/`get_metric_definition()`)
  and the `fetch_series` "Sprint 3 gap" (already closed via
  `query_metric_series()` since Sprint 2's extract-wrap-delete). Made an
  explicit deferral decision (not a silent skip) on folding
  `BusinessStateService.get_metric_state()` into `query_metrics()` —
  correctly out of scope given its live, proven-nonempty caller graph.
  9 new tests total, all passing:
  `./.venv/Scripts/python.exe -m pytest -q tests/unit/test_semantic_toolset_bug_regressions.py tests/unit/test_action_toolset.py -v`
  → 5 + 10 = 15 passed (10 in `test_action_toolset.py` = 6 original + 4
  new). The three known-blocked items (`catalogue_grounding.py` deletion,
  `MetricRegistry` retirement, Google Ads execution) were confirmed with
  the user to stay tracked-with-a-clearance-path rather than built around
  — no automation, no speculative code against an unpublished upstream
  contract, no incremental `MetricRegistry` migration ahead of Sprint 5.
- 2026-09-18: **Sprint 3 / Profile C executed** — Model/Skeptic port + parity harness.
  New: `agent/validation/` (package; `signals.py`/`trust.py`/`verdict.py`/`__init__.py`,
  replacing the single `agent/validation.py`), `models/{service,evaluation}.py`,
  `toolsets/models.py`, `config/model_registry.yaml`, `evals/parity.py`, and
  `tests/{unit/test_v3_validation_signals,unit/test_models_toolset,replay/test_v3_parity}.py`
  (44 new tests). Modified: `toolsets/policy_config.py` (skeptic threshold block),
  `pyproject.toml` (`statsmodels>=0.14`), `tests/unit/test_v3_validation.py`.
  Nothing wired into `orchestration/dispatch.py` — swarm_v2 keeps 100% of traffic.

  **Bug #13's disposition was corrected, not re-stated.** Sprint 2 recorded it as
  a tracked ticket, "not fixed". Re-checked at Sprint 3 open: 20 consecutive
  passes, and the mechanism found in source (commit `79e88cc`'s
  `fallback_to_unfiltered_candidates` block). Row updated above. Worth noting the
  general lesson: that row would have stayed wrong indefinitely, because nothing
  re-runs a disposition once it is written down.

  **A behavior change the content checks introduced, stated plainly.** Three
  Sprint 2 validator tests asserted `ok` for missions with essentially no
  artifacts; they passed only because validation was structural. Two are now
  correctly `NOT_APPLICABLE` (a mission that made no numerical claim owes no
  evidence — rule 6 binds claims, not missions). The third stored a causal
  artifact citing `evidence_ids=["ev-1"]` with no such artifact in the store; that
  is a dangling provenance reference and the validator now flags it. The test was
  updated to supply the evidence its claim says it has, preserving its intent —
  not relaxed to keep it green.

  **Two contract amendments filed:** A1.7 (`METHOD_NOT_AVAILABLE`, applied — §2's
  table is explicitly open) and A1.8 (A1.4's scope gap: swarm_v2's four-tier
  ladder `PLAUSIBLE_CAUSAL`/`STRONGLY_SUPPORTED`/`REJECTED` is still live in 13
  files including `config/diagnostic_policies.yaml:26`; mapping recorded, **not
  applied**, because those sites are only reachable through swarm_v2 paths Sprint 5
  deletes and V3's own vocabulary is already correct — rewriting them now is
  regression risk for a subsystem with a deletion date). Both need A/B sign-off.

  **Open, flagged rather than absorbed:**
  - `config/model_registry.yaml` needs a real owner. Profile C seeded it because
    the toolset needs an approved-model gate to refuse against, but an entry there
    is a claim that a model was built and validated, and it is the only thing
    between `forecast()` and a fabricated number. Added to `00_OVERVIEW.md` §8.
  - `models/evaluation.py` measures forecast accuracy but does not feed back into
    model selection or registry promotion — `last_validated_at` is still set by
    hand. That needs a scheduled job and a registry writer, neither of which
    exists. The measurement half only; saying so beats implying the loop is closed.
  - The confidence vocabulary has three swarm_v2 implementations plus V3's. Sprint
    3 consumed V3's rather than adding a fifth, but the duplication stands.
- 2026-09-18: **Sprint 3 Profile B — `catalogue_grounding.py` heuristic
  deletion executed** (per explicit user override of the 3-calendar-day
  characterization gate: "delete it, we are almost rebuilding this").
  Deleted `dimensions_in_query`, `apply_catalogue_grain`, `hints_from_catalogue`,
  `ground_live_grain`, `constrain_hints_to_grain`, `preferred_grain`,
  `resolve_grain_texts`, `_supported_for_hint`, `_alias_hits`,
  `_resolve_one_term`, `_resolve_metric_term`, and their now-dead constants
  (`_GENERIC_DIM_TOKENS`, `_TIME_SUFFIXES`, `_DIM_QUERY_SYNONYMS`,
  `_MIN_MULTI_TOKEN_OVERLAP`, `_RESOLVED_TERM_KINDS`) from
  `coordinator/catalogue_grounding.py` — 671 lines down to 322. Kept
  everything the live path actually needs and that isn't the bug #8
  mechanism: `collapse_assigned_metrics`/`validate_dimensions_for_metric`
  (catalogue-metadata-based, used by the live `llm_classifier.py`),
  `resolve_catalogue_dimension`/`evidence_covers_grain` (live-resolver
  calls / structural checks, not keyword matching), and
  `breakdown_from_query`/`pick_grain`/`query_has_grain_intent` (still used
  by `agents/intelligence/observer.py`'s own grain-resolution path, layered
  on the live resolver — a different, non-heuristic mechanism, disposition
  tracked separately per `SPRINT_PLAN.md` Sprint 5). Fixed the one real
  caller of the deleted functions, `agents/coordinator.py` (legacy
  lookup_v1's classifier): now trusts `classification.metric_hints`
  directly with no catalogue-search/grain-heuristic step, degrading
  gracefully to no dimension breakdown (that classifier's own
  `CoordinatorClassificationV1` contract has no LLM-provided dimensions
  field to use instead). Rewrote `tests/unit/test_catalogue_grounding.py`
  to drop every test for a deleted function, keeping 9 tests for the
  surviving surface. Verified: whole-repo import chain check
  (`agents/coordinator.py`, `agents/intelligence/observer.py`,
  `orchestration/graph.py`, `coordinator/intake/llm_classifier.py` all
  import cleanly), `ruff check` clean, full suite **898 passed, 1 failed
  (the same pre-existing `test_health_combo_never_returns_running`), 4
  skipped** — no regressions.
- 2026-09-18: **Sprint 3 Profile B — `MetricRegistry` retirement
  investigated and resolved without deletion.** Before acting on the
  user's request to also retire `MetricRegistry`, read
  `services/metrics.py` directly rather than proceeding on the dependency
  count alone. Found the standalone-registry problem the brief worried
  about is already fixed: `bind_catalogue()` already makes the live
  catalogue authoritative across every read method, YAML is only a
  legitimate cold-start/exception overlay. Presented this finding to the
  user with the real tradeoff (document as resolved vs. rewrite ~15 live
  call sites with no isolated test harness) — user chose to document and
  keep the class. `SPRINT_PLAN.md` Sprint 3 and Sprint 5 both updated to
  match; the Sprint 5 deletion line item added in an earlier pass this
  session is removed, replaced with an explicit "not on this list" note.
- 2026-09-19: **Doc-vs-code reconciliation pass** (`docs/refactor/` audited
  against current `src/`/`tests/` state, not just prior doc text). Found the
  Sprint 3 "Gaps left by the `catalogue_grounding.py` heuristic deletion" row
  was stale in the other direction from usual drift: code had moved ahead of
  the doc, not behind it. `agents/coordinator.py::resolve_grain_for_metrics()`
  now exists (uncommitted working-tree change) and is wired into `classify()`,
  closing gap (1) from that row. But its own regression test,
  `tests/unit/test_coordinator_classify.py::test_classify_resolves_grain_via_live_catalogue_not_left_empty`,
  **fails** when actually run against live `seleric-mcp` (reproduced twice,
  not a flake): the fix only resolves grain from the full query sentence or
  from LLM-classified `entities`, and this test env's fake LLM provider
  never populates `entities`, so `"units sold by product"` resolves zero
  live dimensions and the assertion fails. Row downgraded from `Open` to
  `Partial` rather than `Done` — running the suite before updating status
  is what caught this; the row would have read `Done` on the diff alone.
  Full relevant-file suite otherwise green:
  `./.venv/Scripts/python.exe -m pytest -q tests/unit/test_catalogue_grounding.py
  tests/unit/test_coordinator_classify.py tests/unit/test_semantic_toolset_bug_regressions.py`
  → 16 passed, 1 failed (the new grain test above). Also re-ran the full
  suite (`--ignore=tests/integration/test_minio_blob_store_integration.py`):
  **902 passed, 2 failed, 4 skipped** in 418s — the two failures are the
  pre-existing `test_health_combo_never_returns_running` live-data flake and
  the one new grain-resolution failure above; no other regressions found.
  No code changed this pass — documentation only, per the task's scope.
- 2026-09-19: **Fixed the grain-resolution regression** found in the pass
  above, and closed the remaining `catalogue_grounding.py`-deletion gaps.
  Root cause was misdiagnosed in the prior entry: the failing test wasn't
  missing a per-token fallback for whole-sentence resolve — live tracing
  showed `resolve_catalogue_dimension("units sold by product", ...)`
  already returned real candidates (`product_id`, `product_title`,
  `product_type`) live from `seleric-mcp`. The actual bug was one line
  downstream: `resolve_grain_for_metrics()`'s corroboration step reads
  `bootstrap.get(cat_id).supported_dimensions`, and `CatalogueBootstrap`'s
  cache is empty until `refresh_if_stale()`/`warm()` has run at least
  once — nothing on the `classify()` call path warms it (only an actual
  metric *fetch* does, elsewhere), so every live dimension silently failed
  corroboration regardless of what the resolver returned. Fixed with
  `await bootstrap.refresh_if_stale()` added at the top of
  `resolve_grain_for_metrics()` (`agents/coordinator.py`) — the same
  warm-before-read the deleted `_resolve_one_term()` used to do. Also added
  a per-token fallback (`catalogue_grounding.py::query_tokens_for_grain()` +
  a retry loop) as defense-in-depth for sentences the live resolver can't
  parse whole — not what fixed this specific case, but a real gap the
  deleted `_resolve_metric_term()` covered and the replacement didn't.
  `tests/unit/test_coordinator_classify.py` (all 3), `test_catalogue_grounding.py`,
  `test_semantic_toolset_bug_regressions.py`, and `tests/coordinator/` all
  pass (90 passed, 1 skipped). Ran `tests/replay/` live in full as the fresh
  characterization evidence the deletion still owed: **40/40 passed**,
  including `test_lookup_v1.py`'s two full-mission runs through the exact
  `lookup_v1`/`classify()` path this deletion touched, and the original bug
  #8/#14 `test_data_access_characterization.py` (7/7). Full suite: **903
  passed, 1 failed (the same pre-existing live-data flake), 4 skipped**.
  Row above updated to `Done`.
- 2026-09-19: **Scope decision — Google Ads action execution is not part of
  this program's requirements.** Previously tracked as an open/blocked item
  ("`ActionToolset` Partial pending upstream Google Ads contract"); per
  explicit user direction, removed from scope entirely rather than carried
  as a gap. `ActionToolset`'s job was always to cover whatever the live
  `seleric-mcp` action catalogue actually has, which is Meta
  (`pause_meta_ad`) only — there was never a Google Ads contract to build
  against, and none is needed. `00_OVERVIEW.md`, `02_PROFILE_SEMANTIC_MCP.md`,
  and `SPRINT_PLAN.md` updated to state this as a scope boundary, not a
  pending dependency; the `ActionToolset` row above moved from `Partial` to
  `Done`. No code changed — `toolsets/actions.py` already only ever wired
  the generic `actions_propose/commit/status` surface, nothing Google-Ads-
  specific existed to remove.
- 2026-09-19: **Cross-profile verification of Sprints 0–3 — GREEN.** Run before
  opening Sprint 4, on request. Full figures in `SPRINT_PLAN.md` "Verification
  checkpoint": full suite **911 passed / 1 failed / 4 skipped**, Profile A 24,
  Profile B 17 + 10 live-MCP replay, Profile C 83 + 55 non-regression,
  swarm_v2 legacy 126 + 1 skipped. The one failure is the pre-existing
  `test_health_combo_never_returns_running`, identical to Sprint 3 close —
  nothing drifted between sprints. Merge of the Profile B Sprint 3 branch
  afterwards closed the three boxes this run still treated as open (see
  checkpoint note): heuristic deletion + `MetricRegistry` keep +
  characterization gate retired.
  - **Frozen-surface audit added as a check:** 13/20 of `CONTRACTS.md` §4's
    functions import and exist. Gaps are exactly Sprint 4 C's scope (4 analytics
    + `knowledge` + 3 experiments). Worth keeping as a standing check — it is
    the one test that catches a *missing* deliverable, which a passing test
    suite cannot. (Totals later corrected to **15/23 with 8 gaps** — see Sprint 4
    Profile C entry below; the gap *list* was right both times.)
  - **Program-level assumption corrected:** "nothing V3 is wired in" is only
    half true. The agent loop genuinely is not (verified: zero references from
    `dispatch.py`/`main.py`/`coordinator/graph.py`; `api/missions.py` unmounted),
    but `toolsets/semantic.py` is imported by `swarm/providers/mcp_data.py:37`
    and `services/business_state/series.py:11`, both live paths — by the explicit
    user decision recorded in Sprint 2 B. Not a rule violation; it does mean a
    defect in `toolsets/semantic.py` reaches production today rather than at
    canary. Clarified in `00_OVERVIEW.md` §5 rule 1 so nobody plans against the
    wrong model.
  - **Two pre-existing `F401` lint errors** found, neither introduced by Sprint 3:
    `swarm/providers/mcp_data.py:19` (`row_date`, Profile B) and
    `tests/unit/test_causal_toolset.py:10` (`typing.Any`, Profile C Sprint 2).
    Queued as the first task of Sprint 4 C.

- 2026-09-19: **Sprint 4 Profile A — toolset wiring.** Registered all 15
  real tool functions (`toolsets/{semantic,analytics,causal,models,
  actions}.py`) on `SelericAgent` in `agent/agent.py` — previously zero
  tools were registered, so the loop had contracts and stores but nothing
  to actually call. Found and fixed a real, repo-wide bug this surfaced:
  all 5 toolset modules imported `SelericDeps` only under
  `if TYPE_CHECKING:`, which type-checks fine (mypy sees the string
  annotation) but raises `NameError: name 'SelericDeps' is not defined`
  the instant pydantic_ai's real tool-registration path calls
  `get_type_hints()` on the function at runtime — nothing had exercised
  that path before this wiring existed, so it sat latent. Promoted to a
  real top-level import in all 5 files (confirmed no circular-import risk
  first). Also fixed 2 pre-existing `mypy` errors caught while re-checking
  (`agent/validation/signals.py::_payload()`'s `model: type` →
  `type[BaseModel]`; a `cast` needed in `toolsets/analytics.py::
  detect_anomalies` where a comprehension's `is not None` filter doesn't
  survive a later tuple unpack) and added `statsmodels.*` to
  `pyproject.toml`'s mypy override list (same untyped-third-party-lib
  treatment as `dowhy`/`pandas`/`numpy`). Confirmed the 0%-traffic stub
  path is unaffected: `TestModel(call_tools=[])` still returns its fixed
  output and invokes zero of the 15 real tools, asserted explicitly (not
  just "should still work"). New: `tests/unit/test_v3_agent_wiring.py` (3
  cases). Repo-wide `ruff check src` and `mypy src` both clean (341 source
  files). Full suite: **923 passed, 1 failed (the same pre-existing
  `test_health_combo_never_returns_running`), 5 skipped**.

  **Stopped deliberately before the rest of Sprint 4's checklist and asked
  the user, rather than guessing or spending money unasked:** the
  remaining three items (replay set run for real cost/latency, execution
  limits tuned against that data, canary flag flipped) all need either
  real LLM spend against live `seleric-mcp` or a production-traffic
  decision. A scripted zero-cost `FunctionModel` run would prove the tool
  → artifact → answer path works end-to-end, but would not produce
  genuine per-mission cost/token/latency figures (there's no real model
  making decisions) — substituting one for the other and calling it "cost
  data" would misrepresent what was measured. Flipping any percentage of
  canary traffic is explicitly named a production-risk call in
  `SPRINT_PLAN.md`'s own Sprint 4 gate, not an engineering one.
- 2026-09-19: **Local UI connect (not a production canary).** Wired
  conversations, live `POST /v1/missions`, and Office list/snapshot through
  `run_v3_mission` when `V3_AGENT_ENABLED=true`. Persists V3 results into
  the swarm_v2 store + V3 stores so existing office-ui / chat surfaces keep
  working. Follow-up pass the same day: timezone-local `as_of` injected into
  the agent prompt (live chat had resolved "yesterday" against a stale
  training date); 429/timeout bodies sanitized before they reach
  `final_response`; `run.queued`/`run.started`/`run.completed` carry
  `route: v3` so Activity no longer falls back to "Swarm"; Office mission
  list retries at 0.8s/2.5s so first paint is not empty. Tests:
  `tests/unit/test_v3_ui_connect.py`, office-ui `conversationStore.test.ts`.
  Production canary % remains unflipped (settings default still False).

- 2026-09-19: **Sprint 4 / Profile C executed** — the last 8 frozen functions.
  New: `analytics/{breakdown,funnel,cohort}.py`, `knowledge/{__init__,corpus,search}.py`,
  `toolsets/{knowledge,experiments}.py`, `experiments/{__init__,registry,stats}.py`,
  `config/experiment_registry.yaml`, `knowledge_corpus/README.md`, and three test files
  (68 new tests). Modified: `toolsets/analytics.py` (+4 functions),
  `toolsets/policy_config.py` (Sprint 4 block), `agent/agent.py` (TOOLS 15→23),
  `tests/unit/test_v3_agent_wiring.py`. **Frozen surface is now 23/23.** Nothing wired
  into `orchestration/dispatch.py`.

  **A figure I reported earlier was wrong.** The verification checkpoint said the
  frozen surface was "13/20, 7 gaps". `CONTRACTS.md` §4 declares **23** functions and
  **15** were live, so it was **15/23 with 8 gaps**. The gap *list* was right both
  times — only the totals were wrong, and Sprint 4's scope never actually changed.
  Corrected in `SPRINT_PLAN.md` and `00_OVERVIEW.md`.

  **Three design decisions that a reader should not have to infer:**
  - `contribution_analysis`'s denominator is the **sum of the supplied segments**, not
    the metric's true total. `semantic.py::drilldown` computes a parent total and
    throws it away, and skips null-valued rows, so a server-side residual bucket never
    reaches us. Shares are therefore shares *of the observed parts*, and a warning says
    so rather than letting them read as exact.
  - **Funnel order is declared, not derived.** `policy_config.FUNNEL_STEPS` lists five
    metrics that all share the `sessions` denominator (verified against
    `config/metric_registry.yaml:148-215`), which is exactly what makes step conversion
    `rate[i+1]/rate[i]` instead of the cross-axis division the live catalogue rejects
    with `CROSS_AXIS_RATIO_UNSUPPORTED`. Inferring order by parsing `formula` strings
    would have rebuilt the heuristic layer Profile B deleted in Sprint 3.
  - **`search_knowledge` is file-backed, not the existing hybrid search.**
    `conversations/phase7.py` is real and wired, and reusing it was the first instinct.
    It does not fit: `search()` is synchronous (tools are async), it has no
    `artifact_type` filter, and it searches a Postgres `artifacts` table that
    `ctx.deps.artifact_store` never writes to — so it would surface nothing this agent
    produced, and it answers "what did we discuss" rather than "what does the SOP say".

  **Two empty files that are load-bearing.** `knowledge_corpus/` and
  `config/experiment_registry.yaml` both ship empty on purpose, for the same reason
  `config/model_registry.yaml` has no LTV entry: `evaluate_experiment` scores evidence
  against whatever is declared, so a plausible-looking fake entry would produce a
  real-looking lift finding for a test that never ran. The emptiness *is* the safety
  property. Both report it as `success=True` with a named warning, kept distinguishable
  from "records exist, none match".

  **Rule 12 is now structural rather than instructional.** `search_knowledge` writes
  zero artifacts, so a document cannot be cited as evidence for a numeric claim
  whatever the model does with the text.
  `test_knowledge_never_writes_an_artifact` is the guard — if someone later makes it
  return a Finding, rule 12 quietly stops holding and only that test fails.

  **Amendment A1.9 filed**, not applied silently: A1.2's grain precondition was written
  for the two analytics functions that existed at the time and now binds all six. It
  also records a deliberate limitation — the equal-spans rule refuses August (31 days)
  against September (30) for `cohort_analysis`. Weakening it for cohorts would remove
  the guard for raw counts too, since the tool cannot tell a normalized rate from a
  count, so the refusal stands and callers fetch equal windows. Pinned as a test so the
  tradeoff is visible rather than rediscovered as a bug.

  **A trap worth recording for anyone adding a toolset:** `RunContext`/`SelericDeps`
  must be imported at **runtime**, not under `TYPE_CHECKING`. pydantic_ai resolves
  annotations at tool-registration time, so a `TYPE_CHECKING`-only import passes mypy
  and raises `NameError` when the tool actually registers. Profile A hit this in
  `dbdc3c3` and reversed the convention across all five existing toolsets;
  `test_v3_agent_wiring.py` is the guard, and it now covers all 23.
