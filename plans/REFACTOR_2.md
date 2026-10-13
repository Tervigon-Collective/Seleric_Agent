# Seleric refactor: one reasoning loop, deterministic Core analysis, one verification framework

## Context

**The problem.** Seleric gives shallow and sometimes irrelevant answers, while Claude and ChatGPT give deep ones from the *same* MCP server. The audit found why:

- Three brains answer questions:
  - **Brain A**, the v2 template pipeline, runs first by default (`*_v2_mode=enforce`).
  - **Brain B**, the v3 agent loop, only sees what A can't answer.
  - **Brain C**, the Core dashboard analyst.
- The business is defined twice: Agent-side `metric_registry.yaml`, `catalogue_index`, `_read_model` versus Core `catalogue_v2`.
- The model sees summarised `ToolResult.summary` text, not rows.
- The system prompt is 36 KB.
- Validation is fragmented: grounding is implemented three times, and some checks can never fire.
- Each analysis primitive is implemented 3–5 times, with conflicting sign conventions.
- **Root cause of the nonsensical reports:** the system validates against *its own interpretation* of the question, so a misreading passes every check.

**Decisions from the interview.**
- Internal team, one brand (20).
- Azure OpenAI only, and the design must stay model-agnostic (pydantic-ai).
- No cost cap for now, but cost is measured.
- Implementation is mostly AI-driven.
- Keep forecasting and voice. The dashboard analyst and ad actions aren't needed.
- Security is a separate track.
- Primitives go into **Core MCP**.
- Up to 5+ minutes for hard investigations, but that is not the default.
- Ambiguous questions: assume, state the assumption, proceed.
- Full but evidence-bounded investigation.
- Golden truth is **derived from source** (ClickHouse, independent of Cube). The harness lives in `Seleric_Agent/evals/`.

**Principles.**
1. Replace Brain A's *orchestration*, and reuse its verified capabilities as tools.
2. Old and new run side by side. **Orchestration** is deleted only after the gates pass. **Shared capabilities** are deleted only after every consumer has been migrated.
3. The user's request is recorded immutably and kept separate from the model's interpretation; both are verified.
4. The maths is certified against reviewed identities, not against legacy output.
5. Investigation stops when the evidence says so.
6. One verification *interface*, many independent checks.
7. No keyword routing, no hard-coded questions, no fixed metric packs.
8. Line count is **not** an acceptance criterion.

(The `claude-code-leaked` repo was not used: its README says it is "100% derived from the public leak". The design follows Anthropic's *public* agent-engineering guidance.)

---

## Target architecture

```
User ─► API/conversations (unchanged) ─► MissionEngine [MISSION_ENGINE=legacy|shadow|loop]
  LOOP:
   0. RequestRecord (immutable, written before any model call)
   1. RequirementsExtractor (independent LLM call, no tools, never sees the contract) ─► Requirements[]
   2. frame_question (loop model) ─► AnalyticalContract + mapping {requirement_id → contract element | "assumption: …"}
      └─ Verifier gate V1: contract vs RequestRecord + Requirements  (mismatch → re-frame once, else disclose/ask)
   3. load_playbook → tool loop (catalogue / query / drilldown / analyze / diagnose / sql / python / forecast)
      Investigation ledger: requirement status, hypotheses (open/confirmed/refuted/untestable), findings
   4. submit_report ─► Report (typed parts referencing evidence/finding ids)
   5. Verifier gate V2: plan, evidence, report vs BOTH RequestRecord+Requirements AND AnalyticalContract
      (bounded revise/replan ≤2, then partial with explicit limitations)
   6. Deterministic renderer: KPI_GRID / TABLE / CHART / TEXT from evidence only
Core MCP: catalogue_v2 + Cube + query_planner + certified analysis tools
```

### 1. Intent fidelity: the user's request and the model's interpretation are kept separate

- **`RequestRecord`** (frozen, persisted on the run; it extends `conversations` Run/TurnRecord) holds:
  - the verbatim question;
  - the thread context it depended on (the prior question and prior answer, by id);
  - `as_of`, timezone and brand scope;
  - **literal anchors** extracted deterministically. These are *verification inputs, never routing*:
    - entity and value mentions resolved by Core `catalogue_resolve_values` (`gateway/server.py:742`);
    - metric terms resolved by `catalogue_resolve_term`;
    - explicit date or period expressions parsed by the existing `services/time_range.window_from_query`;
    - explicit numbers, limits ("top 5") and comparison words, each kept with its source text span.
- **`Requirements[]`** come from a separate LLM call with its own prompt. It sees only the RequestRecord (not the tools, not the contract) and lists what a correct answer must contain: subject, measures, populations, windows, comparisons, breakdowns, the deliverable type (number, explanation, recommendation, forecast), and any explicit constraints. Each requirement cites the source text span it came from. A requirement without a span is marked inferred.
- **`AnalyticalContract`** is the model's resolved plan. It reuses the fields of `agent/query_spec.py` QuerySpec and `agent/scope.py` RequiredScope. It must map **every** requirement to a contract element, or to a declared assumption.
- **Verification is two-sided.** Checks run against both the RequestRecord and Requirements *and* the contract. Any requirement or anchor not covered by the final answer fails, even when the contract is satisfied. Any element in the contract with no corresponding requirement or anchor is flagged as an unrequested substitution: a different metric, dimension, population or window.
- **The interpretation is shown to the user.** The report starts with "I read this as …", plus the assumptions, so a misreading is visible at once.
- **Eval:** the scorer compares Requirements and contract against the golden case spec separately. This catches the requirements extractor being wrong, not only the loop.

### 2. Loop tools (in `Seleric_Agent/src/seleric_swarm/toolsets/`)

These are thin wrappers over Core or existing code.

| Tool | Backed by (reuse) |
|---|---|
| `catalogue(action=search\|define\|dimensions\|brand\|ontology)` | Core catalogue tools via `services/catalogue_bootstrap.py` |
| `query` | `toolsets/semantic.py` `query_metrics` (evidence via `_put_evidence` :622). Returns a compact table (≤50 rows plus totals/top/bottom); the full rows go into the evidence store |
| `drilldown` | Core `metrics_drilldown` (`query_planner.drilldown` :1164, `hierarchy_targets` :1122) |
| `analyze(method=compare\|contribution\|mix_shift\|funnel\|rank\|anomalies\|decompose)` | Certified Core tools (Phase 1) |
| `diagnose` | `causal/diagnosis.py` engine (`diagnose()` :1832, identities :355/:500, `_analyse_*_dimension`), returning a structured `DiagnosisReport` rather than prose |
| `semantic_sql`, `run_python`, `forecast` | existing (`forecast_metrics`; sandbox isolation is on the security track) |
| control: `frame_question`, `load_playbook`, `submit_report` | new, small |

- **Prompt:** a base prompt of about 1–1.5k tokens plus the tool docstrings plus the loaded playbook.
- **Playbooks:** `config/playbooks/*.md` (lookup, compare, diagnose_change, funnel, profitability_journey, forecast, explore). These are *methods* the model chooses, not routes.
- **Context:** the ledger lives in the scratchpad, and `agent/context.py` `compact_history` keeps the ledger plus short summaries of older results.
- **Models:** `config/llm_roles.yaml` roles are `requirements`, `loop`, `report`. Reuse `resolve_role_model` and `RoleSplitModel` (`agent/model.py:188,266`) and `PatientModel`.

### 3. Evidence-driven stopping rule

The ledger tracks, for each requirement, `answered | unavailable(reason) | open`, and, for each hypothesis, `open | confirmed | refuted | untestable(reason)`.

The loop may stop when **all** of these hold:
1. no requirement is `open`;
2. no hypothesis that could change the conclusion is `open`. A hypothesis is material when the evidence about it so far could reverse or reorder the top findings;
3. the last *k* investigation steps (default 3) added no material finding, meaning no new driver entered the top contributors and no conclusion changed.

`explained_share` is used **only** when the decomposition is *certified*: the identity was verified on the data by the Core tool (`identities_verified=true`) and the components are compatible in grain and unit. In that case it narrows which drivers to examine. It is never a general confidence score.

Budgets (by contract type: lookup about 8 calls, diagnosis up to about 60 calls or 5 minutes) are a safety ceiling, not the stopping reason. Hitting one produces a partial answer that lists what was still open.

---

## Phases

Each phase is a series of small PRs on `claude/cool-franklin-riy9zl`, each green on CI.

### Phase 0: Measurement baseline (blocks everything else)

1. **Make CI runnable.** Set `testpaths=["tests"]`, and move the root scratch scripts and logs out of the collection path.
2. **Cost and tokens for every path.** Export pydantic-ai `RunUsage` and `llm/metering.py` `MeteredLLMPort.usage_for()` into `trace["usage"]`. Add `config/llm_prices.yaml` and a per-mission `cost_usd`.
3. **Source-derived golden set**, built with a new `scripts/build_golden_truth.py`:
   - Take the 40 SEM-18 questions (`Core/scripts/certify_qa.py`) and the 23 Agent cases (`eval/golden/*.yaml`), plus about 30 new ones: diagnosis, funnel, profitability, follow-ups, ambiguous questions, incomplete evidence, paraphrase pairs, and **deliberate misreading traps**.
   - Expected values come from direct ClickHouse SQL, stored in `ValueTruth.value` with a tolerance (`evals/_case.py:25`).
   - Each case also stores its expected Requirements.
   - "Why" cases store reviewed expected drivers.
4. **Scorer upgrades** (`evals/_scorer.py`):
   - Score `must_mention` and `forbid`.
   - Make `numbers_grounded` real.
   - Add `requirements_fidelity`, `contract_fidelity`, `answer_covers_request`, `driver_recall@3`, `completeness`, `cost_usd`, `p50/p95 latency`.
   - Add an optional LLM judge for usefulness (reported, not gating).
5. **Baseline.** Live runs of legacy A→B, B-only, and the Core analyst (through a benchmark-only adapter, to decide "keep the better brain" on data). Store the results in `eval/baselines/`.

### Phase 1: Certified Core analysis primitives (Seleric_Agent_Core)

1. **Identity specification first:** `doc/analysis/IDENTITIES.md`, **reviewed and signed off by you** before code. For each primitive it states the formula, sign convention (current − baseline), preconditions (window equality, compatible grain and unit, non-zero base) and the identity it must satisfy:
   - contributions sum to Δ, with an "other" bucket and an explicit residual;
   - for a rate, within + mix + interaction = Δ rate, with weights from `ratio_components`;
   - the product of funnel stage ratios equals the overall ratio;
   - Shapley efficiency (the parts sum to Δ) for multiplicative chains;
   - ratio totals are Σnumerator/Σdenominator (`insight_engine.metric_total` :46);
   - anomaly reference definitions (same weekday, robust median/MAD).
2. **Certification tests:**
   - Property-based tests (`hypothesis`) assert each identity on random valid inputs and assert refusal on invalid ones.
   - Fixed fixtures use hand-computed values from the spec.
   - Live certification compares each primitive on brand 20 against independent ClickHouse SQL.
   - Legacy-vs-new comparison is **diagnostic only**. Each divergence is triaged: if legacy is wrong, the divergence is recorded as a legacy defect; if new is wrong, it is fixed. Parity with legacy is never the pass condition.
3. **Implementation:** `src/seleric_mcp/app/analysis.py` (pure functions). Port and correct from:
   - `insight_engine.compute_totals` and `compute_top_movers`;
   - `derive/_ops.py` (`_change`, `mix_shift_decomposition` :370, `_op_funnel_stages` :402, `_op_rank`, `_op_reconcile`);
   - `causal/diagnosis.py` (`shapley` :575, the identity checks);
   - `toolsets/diagnosis._funnel_ladder` (:964);
   - `business_state/detectors.robust_zscore`.
4. **MCP tools in `gateway/server.py`.** They take a `query_id` from `ResultStore` (like `insights_explain` :1138), reuse the scope, module and stale guards (:946–966), and return `{table, identities_verified, preconditions, explained_share?, residual, provenance}`.
5. **Catalogue additions:** `direction_bad` (polarity) in `catalogue_v2/metrics/*.yaml`, migrated from `metric_registry.yaml`. Ordered funnel definitions in `catalogue_v2/concepts/`, served through `catalogue_get_ontology`.

### Phase 2: The new loop behind `MISSION_ENGINE` (Seleric_Agent)

1. `agent/loop/` contains the RequestRecord, RequirementsExtractor, `frame_question` and the AnalyticalContract with its requirement mapping. It reuses `build_seleric_agent`, `RepeatCallGuard` (identical *failing* calls only) and `PrepareTools` (no intent-based withdrawals).
2. Playbooks and the base prompt. `query` returns compact data (changing `semantic.py` around :2616).
3. `submit_report` produces a `Report`. The renderer reuses `answer/_render.py` (KPI_GRID :191, TABLE, CHART) and `analytics/visualization.py` `generate_visualization_spec` (:656), with the model never writing chart data. `answer/_render.py` is **moved, not copied**: legacy imports it from the new location.
4. Wire it in at the `runner.py` branch (around :1793). In `shadow` mode the loop runs in the background, and the comparison is recorded in the trace (generalising `pipeline/_shadow.py` `summarise`).
5. Voice and forecasting call `MissionEngine` (each consumer is migrated in Phase 5 only after its own tests pass on the loop).

### Phase 3: One verification framework (`agent/verify/`)

The interface is `verify(stage, request_record, requirements, contract, evidence, report?) -> VerificationResult{checks[], verdict, revision_requests[]}`. Each check is a pure function with an id, a family, a severity, and the stage(s) it runs at: V1 is after framing, V2 is after the report.

| Family | Checks |
|---|---|
| 1 Intent fidelity (two-sided) | every Requirement and anchor maps to a contract element or a declared assumption (V1); every Requirement and anchor is covered in the final answer (V2); no contract element without a corresponding requirement or anchor (no silent substitution of metric, dimension, population or window); interpretation and assumptions are disclosed; doesn't end in an offer instead of an answer (reuse `_verify._coverage`, `check_scope_coverage`, `ends_in_offer`) |
| 2 Semantic correctness | metric ids exist in Core; grain, date basis and attribution contract match; ratios recomputed from components (`coherence.ratio_incoherence`); attribution not stated as causation; causal schema check (one copy) |
| 3 Query safety and authorization | brand/tenant scope on every evidence row (scope injection as in `analyst.py:136`); only supported dimensions |
| 4 Numerical correctness | totals reconcile (`total_mismatch`); no change computed across unequal windows (`_unequal_window_change`); certified identities hold (Δ = Σparts + residual); unit and role checks (`_verify`); contradictions across evidence (`check_contradiction`) |
| 5 Evidence completeness | each Requirement is `answered` with evidence or `unavailable` with its reason (judged against the request, not just the queries run); missing values are null, never zero; failed queries are disclosed as failures, not as "no data"; staleness is a warning |
| 6 Answer grounding | one number scanner (merging `grounding.scan_numbers`, `answer_audit._figures`, `__init__._figures`) checks every figure, chart row and recommendation against evidence or derivation values within print tolerance |
| 7 Recovery | concrete `revision_requests`; at most 2 replans or re-frames; then a partial with explicit limitations |

**Check migration rule.** Each existing check, listed in the explorer inventory (`agent/validation/*`, `answer/_verify.py`, `signals.run_checks`), is (a) ported as is, (b) merged with its duplicate, or (c) removed as dead, with a stated reason. Examples of (c): the never-supplied `alternatives` input, and trust dimensions with no signal. Every check keeps or gains a negative-fixture test before the old one is removed. Formatting repairs move into the renderer.

### Phase 4: Benchmark, shadow and gates

Run the full golden set live: `legacy` vs `loop`, ×3 for variance. Also run production shadow on internal traffic for at least one week.

The loop must be ≥ legacy on every gate, and absolute floors apply:
- numerical correctness ≥ 98%;
- material-number grounding = 100%;
- requirements fidelity ≥ 95% and answer-covers-request ≥ 95%, **including misreading traps**;
- driver_recall@3 ≥ legacy + 15 points;
- completeness ≥ 90%;
- zero scope violations;
- lookup p50 latency ≤ legacy;
- cost reported.

### Phase 5: Controlled cutover and removal

1. Set `MISSION_ENGINE=loop` by default; keep the rollback flag for at least 2 weeks.
2. **Remove orchestration (after the gates):** the v2 routing in `runner.py` (`_start_v2_shadow`, `_v2_enforce`, `*_v2_mode`), the `understand()` pre-stage, `plan_from_slots`/`execute_plan` prefetch, per-intent tool withdrawal (`agent.py` :191–226), and the legacy `instructions.py`.
3. **Remove shared capabilities only through a consumer audit.** For each candidate module (e.g. `pipeline/`, `spec/`, `plan/`, `understand/`, `answer/`, `derive/`, `analytics/*`, `semantic/_read_model.py`, `toolsets/catalogue_index.py`, `config/metric_registry.yaml`, `agent/validation/*`, `persistence/`, `checkpointing.py`/langgraph):
   - **(a)** list every importer and runtime consumer: grep the imports and lazy imports; check forecasting, voice, business_state/report paths, domain_health scheduler, office API, scripts and tests;
   - **(b)** migrate each consumer to the replacement, with that consumer's tests passing;
   - **(c)** only then delete, in a PR that records the audit.

   Anything still consumed stays.
4. **Core:** retire `gateway/analyst.py` and `llm/` only if the benchmark shows the loop is better, and after porting anything better found there.
5. Fix the 5 broken import-linter contracts as a side effect of the removals, not by relaxing them.

---

## Critical files

- **Agent:**
  - `agent/runner.py` (around :1793, v2 :994–1135)
  - `agent/agent.py`
  - `agent/model.py`
  - `agent/context.py`
  - `agent/query_spec.py`
  - `agent/scope.py`
  - `toolsets/semantic.py` (:622, :2616)
  - `toolsets/diagnosis.py`
  - `causal/diagnosis.py`
  - `services/time_range.py`
  - `agent/validation/*`
  - `answer/_render.py`
  - `answer/_verify.py`
  - `analytics/visualization.py`
  - `conversations/contracts.py` (RequestRecord on the Run)
  - `evals/{_case,_runner,_scorer}.py`
  - `scripts/eval_run.py`
  - `config/llm_roles.yaml`
  - `pyproject.toml`
- **Core:**
  - new `doc/analysis/IDENTITIES.md`
  - new `app/analysis.py`
  - `app/insight_engine.py`
  - `app/query_planner.py`
  - `gateway/server.py`
  - `catalogue_v2/metrics/*.yaml`
  - `catalogue_v2/concepts/`
  - `catalogue_service/service.py`

## Verification

- Per PR:
  - Agent: `uv run pytest tests -q`, `uv run ruff check .`, `uv run lint-imports`.
  - Core: `uv run pytest -m "not live"`.
- Phase 1:
  - The property-based identity tests and spec fixtures pass.
  - Live certification: each Core tool on brand 20 matches an independent ClickHouse SQL value within tolerance.
  - The divergence triage is written up.
- Phase 2/3:
  - Positive and negative tests for each verifier check, including fixtures from real past misread missions in the trace store. Example: a misread contract must fail V1/V2 even though the answer satisfies the contract.
  - `MISSION_ENGINE=loop uv run python scripts/eval_run.py --suite golden --mode live`.
- Phase 4: the gate table in `eval/reports/loop_vs_legacy/report.md`, per tag, must pass before Phase 5.
- Phase 5: each removal PR includes its consumer audit, and the full suite plus the forecasting and voice tests stay green.