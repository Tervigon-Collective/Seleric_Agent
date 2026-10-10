# Seleric_Agent — modular refactor plan

**Agents: read [`plans/AGENT_CHAT.md`](AGENT_CHAT.md) before every session.** Post findings and talk there. Update the status board in this file when a module starts, blocks, or lands. Every commit is on `gaurav`.

## Context

**Why.** Seleric_Agent answers business questions with an LLM loop over Cube (via Seleric_Agent_Core MCP).
It works for many questions but fails unpredictably and regresses often:

- 66 of 182 live runs hit validation revision loops.
- Reconciliation questions fail even though the data was fetched.
- "It was fine yesterday" happens again and again.

The 2026-10-10 architecture review (`docs/ARCHITECTURE_REVIEW_2026-10-10.md`) and three code audits found the causes:

1. **Meaning is decided in many places.**
   - `QuerySpec` (`agent/query_spec.py`) is built, but **no module reads `deps.query_spec`**.
   - The raw question is re-parsed 5+ times (`window_from_query`, `_BREAKDOWN_RE`, `stated_grain`, `_outside_measures`, `promote_verbatim_values`).
   - `RequiredScope` is mutated 6+ times in `runner.py`.
2. **The LLM does arithmetic in prose; about 3,800 lines of regex validators then police it.** Roughly 70% of `answer_audit.py`, `grounding.py`, `coherence.py` and the first half of `validation/__init__.py` exist only for that.
3. **Diagnosis is shallow because of missing inputs, not missing maths.**
   - `calendar_in.yaml` and `data_incidents.yaml` are read only by forecasting.
   - `causal_graphs.example.yaml` and `diagnostic_ontology.yaml` have no loader.
   - There is no recursion and no premise or data-health stage.
4. **The semantic seam leaks.**
   - The agent reaches the catalogue through 13 MCP capabilities, a partly cached snapshot, and agent-side copies.
   - The copies are `config/metric_registry.yaml`, the hardcoded `_DELIVERY_METRICS` / `_SPEND_SLICE_COMPANIONS` and v1 view names in `services/catalogue_bootstrap.py:46-56,235-247`, `_MERGE_DERIVED` / `_PREFERRED_JOIN_KEYS` in `toolsets/analytics.py`, and brand constants in 7 files.
   - **Bug:** `bind_catalogue` runs before the catalogue warms (`bootstrap.py:77`), so production runs on the YAML.
   - Refresh is TTL-only, and `catalogue_version` is ignored.
5. **Process.**
   - CI runs only on `main`/PRs, so pushes to `gaurav` are never checked.
   - The 30-question golden regression lives outside the repo.
   - There are no LLM traces (failures could not be diagnosed after a log rotation).

**Outcome.** A modular pipeline with these properties:

- Every stage reads one typed object from the previous stage and never the raw question.
- Code does all arithmetic; answers are built from claims that reference evidence.
- Diagnosis runs as an investigation controller fed by context providers and a metric graph.
- The semantic layer is consumed through one port, so any catalogue change is picked up with no agent code change.
- Every module can be built and tested independently, ships behind a flag (`off|shadow|enforce`), and is gated by an in-repo eval suite.

**Out of scope.** Semantic-layer work in Core / Cube / mage-ai. The agent must *tolerate* catalogue evolution (see the port in M0.5).
The one Core change in scope is retiring the dashboard's second agent brain (`gateway/analyst.py`) in the last phase.

## Decisions (user, 2026-10-10)
- Core's `analyst.py` is consolidated in the **last phase**: the dashboard calls Seleric_Agent.
- Tracing: **OpenTelemetry GenAI spans, with Langfuse as the viewer** (hooks exist in `observability/tracing.py`).
- Rollout: every module has a flag `off | shadow | enforce`. Shadow runs beside the legacy path and logs disagreements. Enforce is only allowed after the eval gate passes. **Delete the legacy path after enforce.**
- 3–5 implementers in parallel, with interfaces frozen at the start of each phase.
- Eval gate: **replay in CI** (recorded MCP responses) on every PR, plus a **live suite on Jenkins** before each deploy.

---

## Part A — Engineering rules (every module MUST follow)

These are the "definition of a good codebase" applied here: ports and adapters (hexagonal), typed contracts,
enforced boundaries, tests per layer, explicit failure policy and observable stages.

### A1. Layers (enforced with `import-linter`, see M0.1)
```
seleric_swarm/
  contracts/pipeline/   L0  pure typed models (pydantic v2, frozen). Imports: stdlib, pydantic only.
  semantic/             L1  SemanticPort protocol + read model. Imports L0.
  understand/ spec/ plan/ derive/ answer/ investigate/ context/ graph/   L2 domain logic (pure; no I/O except via ports)
  adapters/             L3  MCP, LLM, Postgres, Langfuse implementations of ports
  pipeline/             L4  orchestration: wires stages, flags, spans (replaces most of agent/runner.py)
  api/, voice/          L5  entry points
```

**Rules:**
- A lower layer never imports a higher one.
- Domain modules (L2) never import `adapters`, `httpx`, `openai` or `pydantic_ai` directly. They receive ports through function arguments.
- Existing modules (`agent/`, `toolsets/`, `causal/`, `exploration/`, `analytics/`) keep working. New code goes in the new packages. Old code is moved or deleted only when a module reaches enforce.

### A2. No semantic literals, no intent regex
- **MUST NOT** put metric ids, dimension ids, view names, dimension values, channel words or concept words in `src/` string literals. The only exception is files listed in `scripts/semantic_literal_allowlist.txt`, which must shrink over time.
- **MUST NOT** use `re`, word lists or keyword sets to decide what the user *meant*.
  - Allowed: parsing machine formats (ISO dates, ids, JSON), done in `contracts/` or `adapters/` only.
  - Enforced by an import-linter forbidden contract (`re` cannot be imported by L2 modules), plus the M0.1 lint script.
- Catalogue facts (aggregation, unit, compositions, hierarchies, supported dimensions, families, twins) are read only through `SemanticPort`.

### A3. Typed contracts at every boundary
- Stage inputs and outputs are `contracts/pipeline` models. Passing `dict[str, Any]` across a stage boundary is not allowed.
- Every model has `schema_version: int`. Breaking a model means a new version plus an adapter, not an in-place edit, once the phase is frozen.

### A4. Failure policy (explicit per stage; implement exactly)

| Stage | On error | Rationale |
|---|---|---|
| Semantic port warm | serve last good snapshot; if none → refuse data questions with `CATALOGUE_UNAVAILABLE` | never guess the catalogue |
| Understand | **one retry**, then return `Clarification(reason="understanding_unavailable")` | never fall back to "every value word is a filter" (today's fail-closed bug) |
| Spec compile/validate | invalid → `Clarification` naming the problem | no silent partial specs |
| Plan | no template → route to agent loop (tools only) | open questions stay possible |
| Execute query step | record `StepError` on the ResultSet; continue other steps | partial answers state what is missing |
| Derivation | refuse op with typed error (`UNEQUAL_WINDOWS`, `NON_ADDITIVE`, …) | never compute a wrong number |
| Answer verify | structural fail → 1 revision; else ship `partial` with limitation | no infinite revision loops |

### A5. Tests per module
1. **Unit:** pure functions with an *invented* catalogue (`zz_*` ids, as in `tests/unit/test_diagnosis_tool.py`).
2. **Contract:** port implementations against recorded MCP responses.
3. **Replay:** the pipeline on cassettes (M0.4).
4. **Live:** Jenkins.

Every module's test file is `tests/unit/<module>/test_*.py`. A fix to a production failure adds a replay case, not only a unit test.

### A6. Observability
Each stage runs inside `stage_span(name, input_model, output_model)` (M0.3). No stage logs prompts or bodies outside spans. Redaction comes from the existing `observability/tracing.py:63-82`.

### A7. Definition of done (per module)
- Interface matches Part B and the A9 table exactly (nothing else in `__all__`); mypy and ruff clean; import-linter clean, including the `protected` contracts.
- Unit and contract tests pass. The replay suite is no worse than baseline on every metric. The flag is wired.
- The module README (`<package>/README.md`) lists purpose, interface, flag, failure policy and test command.
- A legacy-deletion PR is prepared, to merge after the module reaches enforce.

### A8. Branch and PR workflow (for parallel agents)
- Every change is committed on `gaurav`. If the work is on any other branch, move it onto `gaurav` and continue there. See the note in Part D.
- Each change touches only its module's files plus wiring behind its flag. If you must edit a shared file (`pipeline/`, `config/settings.py`), add, never rewrite.
- CI (M0.1) must pass on `gaurav`.

### A9. Deep modules (simple interface, rich internals)
A module earns its place by hiding complexity behind a small interface (Ousterhout, *A Philosophy of Software
Design*). A module that only renames, re-exports or passes calls through is shallow: it adds an interface to learn
and hides nothing. Rules:
- **Each package exposes one to three entry points**, and only through its `__init__.py` (`__all__`). Everything
  else lives in `_`-prefixed private modules (`spec/_windows.py`). The file lists in Part C are these internals;
  the public interface is the table below.
- **Callers import the package root only** (`from seleric_swarm.spec import compile_question_spec`), never a
  private submodule. This is enforced by an import-linter `protected` contract per package (M0.1).
- **No pass-through layers.** A function whose body is one call to another function with the same arguments is
  deleted and its callers point at the target. Re-export-only modules are not allowed outside a package's `__init__.py`.
- **No abstraction without a second user or a test double.** A `Protocol` is justified when a fake implements it in
  tests (the `SemanticPort` fake) or two real adapters exist. Otherwise use the concrete type.
- **Do not split for size alone.** Split a module when a part can be understood and tested on its own behind its own
  interface. A one-function module with a clean job (e.g. a pure builder) is fine; merging it into a larger class
  only to "reduce files" makes that class shallower.
- **Cross-cutting concerns are wrapped once.** Spans, scope checks, error envelopes and logging are applied by
  one decorator or context manager (`stage_span`, the tool-registration decorator). They are never repeated per call site.

| Package | Public interface (`__all__`) | Hides |
|---|---|---|
| `contracts.pipeline` | the Part B models, `dump`, `load`, `fingerprint` | serialisation details |
| `semantic` | `SemanticPort`, `SemanticReadModel`, `McpSemanticPort`, `FakeSemanticPort` | MCP capability names, tolerant parsing, version refresh, TTL, ranking |
| `observability` | `stage_span`, `configure_tracing` | OTel/Langfuse wiring, redaction, fingerprints |
| `evals` | `run_suite(cases, mode) -> Report` | cassettes, scoring, truth queries, baselines |
| `understand` | `understand(question, read_model, candidates, prior, exemplars, llm) -> QuestionDraft \| Clarification` | prompt building, retries, structured output |
| `understand.examples` | `ExampleStore.similar(question, k)` | embedding, Qdrant, stale-example re-validation |
| `spec` | `compile_question_spec(...) -> QuestionSpec \| Clarification`, `scope_from_spec(spec)` | windows, filters, exclusions, edits, validation, clarification triggers |
| `plan` | `plan(spec, read_model) -> AnalysisPlan`, `execute(plan, port) -> list[ResultSet]` | template registry, step ordering, parallelism, entity hand-off |
| `derive` | `derive(op, inputs, params, read_model) -> Derivation` | op registry, refusal rules, artifact writes |
| `answer` | `draft_answer(...) -> AnswerDraft`, `render(draft, results) -> AnswerDocument`, `verify(draft, spec, results) -> Verdict` | placeholders, number formatting, table rendering, structural checks |
| `context` | `events(window, scope, port) -> list[ContextEvent]`, `health(metric_ids, window, port) -> list[DataHealth]` | individual providers, provider config |
| `graph` | `metric_graph(read_model) -> MetricGraph` | identity discovery, YAML causal edges, node validation |
| `investigate` | `investigate(request, deps) -> InvestigationResult` | state machine, steps, recursion, LLM branch selection, persistence |
| `pipeline` | `run_mission(question, deps) -> AnswerDocument` | stage wiring, flags, shadow comparison |

---

## Part B — Frozen interfaces (M0.2 writes these first; everyone codes against them)

File: `src/seleric_swarm/contracts/pipeline/models.py` (split into submodules if over 400 lines). All `BaseModel`, `frozen=True`, `extra="forbid"`.

```python
# --- identity / refs
class Ref(BaseModel): kind: Literal["evidence","derivation","finding","result_set"]; id: str

# --- question
class WindowSpec(BaseModel): role: Literal["event","baseline","context"]; start: date; end: date
    source_span: str = ""; token: str = ""; through_hour: int | None = None
class FilterSpec(BaseModel): dimension: str; operator: Literal["in","not_in"]; values: tuple[str,...]
    source_span: str = ""; volume: int | None = None          # not_in = exclusions ("exclude exchanges")
class EntitySpec(BaseModel): dimension: str; values: tuple[str,...]; source_span: str = ""
class MeasureSpec(BaseModel): phrase: str; metric_id: str | None; axes: tuple[tuple[str,str],...] = ()
class QuestionSpec(BaseModel):
    schema_version: int = 1
    kind: Literal["conversation","analysis","overview","forecast","what_if","action","exploration"]
    shape: str                      # values come from plan template registry (M2.1), not hardcoded here
    measures: tuple[MeasureSpec,...]; filters: tuple[FilterSpec,...]; entities: tuple[EntitySpec,...]
    breakdowns: tuple[str,...]; windows: tuple[WindowSpec,...]; grain: str | None
    ranking: "RankingSpec | None"; direction: Literal["up","down","either"] = "either"
    follow_up_of: str | None = None # prior mission id when this is an edit
    assumptions: tuple[str,...] = (); catalogue_version: str
class RankingSpec(BaseModel): by_metric_id: str; order: Literal["asc","desc"]; limit: int
class Clarification(BaseModel): reason: str; question_to_user: str; options: tuple[str,...] = ()

# --- plan
class QueryStep(BaseModel): step_id: str; metric_id: str; breakdowns: tuple[str,...]; filters: tuple[FilterSpec,...]
    window_role: Literal["event","baseline","context"]; grain: str | None; ranking: RankingSpec | None = None
    entities_from_step: str | None = None; elapsed_only: bool = False
class EngineStep(BaseModel): step_id: str; engine: str; params: dict[str, JsonValue]; inputs: tuple[str,...] = ()
class DeriveStep(BaseModel): step_id: str; op: str; inputs: tuple[Ref | str,...]; params: dict[str, JsonValue] = {}
class AnalysisPlan(BaseModel): schema_version: int = 1; template_id: str; steps: tuple[QueryStep|EngineStep|DeriveStep,...]
    needs_agent: bool = False; trusted: bool = False     # trusted = built from a verified template/example (M1.3)

# --- results
class ResultRow(BaseModel): evidence_id: str; dimensions: dict[str,str]; value: float | None
class ResultSet(BaseModel): ref: Ref; step_id: str; metric_id: str; unit: str | None; window: WindowSpec
    breakdowns: tuple[str,...]; filters: tuple[FilterSpec,...]; rows: tuple[ResultRow,...]
    error: "StageError | None" = None
class Derivation(BaseModel): ref: Ref; op: str; inputs: tuple[Ref,...]; params: dict[str,JsonValue]
    value: float | None; unit: str | None; rows: tuple[ResultRow,...] = (); error: "StageError | None" = None

# --- answer
class Claim(BaseModel): claim_id: str; ref: Ref; row_key: dict[str,str] = {}; label: str; role: Literal["headline","support","change","share","total","context"]
class Paragraph(BaseModel): kind: Literal["paragraph"]; text: str            # contains {claim_id} placeholders only for numbers
class TableBlock(BaseModel): kind: Literal["table"]; result_set: Ref; columns: tuple[str,...]; caption: str = ""
class ChartBlock(BaseModel): kind: Literal["chart"]; refs: tuple[Ref,...]; chart_type: str; title: str
class AnswerDraft(BaseModel): status: Literal["completed","partial","failed"]; claims: tuple[Claim,...]
    blocks: tuple[Paragraph|TableBlock|ChartBlock,...]; limitations: tuple[str,...] = (); next_step: str = ""
class AnswerDocument(BaseModel): draft: AnswerDraft; rendered_markdown: str; evidence_ids: tuple[str,...]

# --- errors
class StageError(BaseModel): stage: str; code: str; message: str; retryable: bool = False
```

**Mapping to today.**
- `QuestionSpec` supersedes `agent/query_spec.py::QuerySpec`. Keep `QuerySpec` working; M1.2 adds a `to_question_spec()` adapter and later deletes `QuerySpec`.
- `AnswerClaim` (`query_spec.py:290`) is replaced by `Claim`.

---

## Part C — Modules by phase

Each module lists: **Goal · Depends · Files · Steps · Rules · Tests · Acceptance · Flag · Delete after enforce**.
Tracks (T1–T5) show what can run at the same time.

### Phase 0 — Foundations (all five in parallel; M0.2 must merge in the first 3 days)

#### M0.1 Guardrails (T1)
- **Goal:** make violations of Part A impossible to merge.
- **Depends:** none.
- **Files:** `.github/workflows/ci.yml`, `pyproject.toml` (`[tool.importlinter]`), `scripts/lint_semantic_literals.py`, `scripts/semantic_literal_allowlist.txt`, `.github/pull_request_template.md`, `docs/ADR/ADR-005-modular-pipeline.md`.
- **Steps:**
  1. In `ci.yml`, change `on.push.branches` to `["**"]` so every branch runs CI.
  2. Add the dev dependency `import-linter` and define these contracts:
     - `layers`: `contracts → semantic → (understand|spec|plan|derive|answer|investigate|context|graph) → adapters → pipeline → api`;
     - `forbidden`: L2 packages must not import `re`, `httpx`, `openai`, `pydantic_ai`, `seleric_swarm.adapters`;
     - `independence`: `derive`, `answer`, `context`, `graph`;
     - `protected`: for each new package, its `_`-prefixed submodules may be imported only from inside that package (A9).
  3. Write `lint_semantic_literals.py`:
     - load every metric and dimension id and every dimension value from `tests/fixtures/catalogue/snapshot.json` (M0.5 records it);
     - scan `src/**/*.py` string literals with the `ast` module (not regex);
     - fail on any match outside the allowlist;
     - print `file:line literal`.
  4. Seed the allowlist with the current offenders (from the audit): `services/catalogue_bootstrap.py`, `toolsets/analytics.py`, `toolsets/composition.py`, `services/mcp_query.py`, `forecasting/scope.py`, `business_state/*`, `domain_health/resolver.py`, `toolsets/semantic.py`, `agent/instructions.py`. Each line carries the owning module id that will remove it.
  5. Add CI steps `uv run lint-imports` and `uv run python scripts/lint_semantic_literals.py`.
- **Tests:** CI itself; add a test that a new literal outside the allowlist fails the script.
- **Acceptance:** a PR adding `"net_sales"` to a new file fails CI.

#### M0.2 Pipeline contracts (T2)
- **Goal:** Part B as code.
- **Depends:** none.
- **Files:** `src/seleric_swarm/contracts/pipeline/{__init__,_question,_plan,_results,_answer,_errors}.py`, `tests/unit/contracts/test_models.py`.
- **Steps:**
  1. Write the models exactly as in Part B.
  2. Add `.dump()` and `.load()` helpers (JSON round trip).
  3. Add a `fingerprint()` (sha256 of the canonical JSON) for spans.
- **Rules:** no imports beyond stdlib and pydantic. No business logic.
- **Tests:** round trip of every model; `extra="forbid"` rejects unknown fields; the frozen check.
- **Acceptance:** merged, then tagged `contracts-v1`. After that tag, a change needs an ADR.

#### M0.3 Observability (T3)
- **Goal:** every mission is traceable and replayable.
- **Depends:** M0.2 (soft: types for attributes).
- **Files:** `src/seleric_swarm/observability/_stages.py` (exported as `stage_span`), `adapters/langfuse_exporter.py`, `scripts/replay_mission.py`, `config/settings.py` (add `TRACE_BACKEND`, `LANGFUSE_*`).
- **Steps:**
  1. Write the context manager `stage_span(stage: str, *, mission_id, inp: BaseModel | None, out_setter)`. It creates an OTel span named `seleric.stage.<stage>` with attributes `seleric.mission_id`, `seleric.stage.input_fingerprint`, `seleric.stage.output_fingerprint`, and `gen_ai.*` attributes for LLM stages (`gen_ai.operation.name`, `gen_ai.request.model`, `gen_ai.response.model`, token usage) per the OTel GenAI semconv. Pin the semconv version in a constant.
  2. Persist full stage input/output JSON to the Langfuse observation (redacted), plus a compact copy in `trace["stages"][stage]` of the mission result.
  3. Wrap the pydantic-ai model calls through the existing `observability/tracing.py::operation_span`.
  4. `replay_mission.py <mission_id>` loads the stored stage inputs and re-runs one stage or the whole pipeline with a cassette MCP (M0.4).
- **Tests:** a span is emitted with fingerprints (in-memory OTel exporter); redaction test.
- **Acceptance:** a failed mission's understand input/output, plan, tool calls and validation verdict are viewable in Langfuse.

#### M0.4 Evaluation harness (T4)
- **Goal:** a behavioural gate in-repo, in replay (CI) and live (Jenkins) modes.
- **Depends:** M0.2 (spec comparison), M0.3 (optional).
- **Files:** `src/seleric_swarm/evals/{__init__,_case,_runner,_cassette,_scorer,_report}.py` (public: `run_suite`), `eval/golden/*.yaml`, `eval/cassettes/`, `scripts/eval_run.py`, `Jenkinsfile.eval` (or a Jenkins stage snippet in `docs/`).
- **Case schema** (`evals/case.py`):
  - `id, as_of, turns: [question], tags: [shape…]`
  - `expect: {spec: partial QuestionSpec, status, values: [{label, truth: MetricsQuery args, tolerance}], must_mention: [entity values], forbid: [phrases]}`
  - Truth is expressed as `metrics_query` arguments (catalogue ids), never SQL, so it follows the catalogue.
- **Steps:**
  1. Import the 30 golden questions and their truth (currently outside the repo; ask the owner for the harness or the `docs/OPEN_GAPS_*` list) into `eval/golden/`.
  2. `cassette.py`: a `RecordingMcpClient` (wraps the real client, writes request→response JSON keyed by canonical request hash) and a `ReplayMcpClient` (serves them, and fails on a miss).
  3. `runner.py`: runs `run_v3_mission` per case with a fake or real LLM (configurable), collecting the spec, result and trace.
  4. `scorer.py` metrics per case and per tag: spec accuracy (field-level), status, value correctness (claims or evidence versus truth within tolerance), revisions, latency, LLM calls and tokens.
  5. `report.py` writes JSON and markdown, and compares to `eval/baselines/golden.json`.
  6. CI job: replay mode with recorded LLM responses (so it is deterministic). Jenkins: live mode before deploy; block on regression versus baseline.
- **Tests:** a scorer unit test; a cassette miss raises.
- **Acceptance:** `uv run python scripts/eval_run.py --mode replay` reproduces the baseline; Jenkins blocks a deliberately broken build.

#### M0.5 Semantic port (agent side) (T5)
- **Goal:** one entry point for all catalogue knowledge; catalogue changes need zero agent code change.
- **Depends:** M0.2.
- **Files:** `src/seleric_swarm/semantic/{__init__,_port,_read_model,_mcp_port,_fake}.py` and `README.md`, modify `services/catalogue_bootstrap.py` (becomes a thin shim over the port), `services/metrics.py`, `tests/contract/test_semantic_port.py`, `tests/contract/test_semantic_pickup.py`, `tests/fixtures/catalogue/snapshot.json`.

**`SemanticPort` (Protocol):**
```python
version() -> str                                   # catalogue_version from Core; "" if absent
read_model() -> SemanticReadModel                  # immutable snapshot (below)
resolve_concept(text, axes) -> ConceptResolution   # wraps seleric.catalogue_resolve_concept
resolve_values(text, axes_only=False) -> ValueCandidates  # wraps catalogue_resolve_values
metric_definitions(ids) -> dict[str, MetricDefinition]    # catalogue_get_metrics (batched ≤10)
query(args: MetricsQueryArgs) -> QueryResponse     # metrics_query (keeps semantic._cached_metrics_query path)
drill(args) -> QueryResponse                       # metrics_drilldown
```

**`SemanticReadModel`:**
- metrics: id, label, view, unit, aggregation, supported_dimensions, date_basis, date_twin, grain_twins, volume_metric, composition, split_by, valid_for, excluded_dimensions, currency, and `extra: dict` for any unknown field.
- dimensions: id, is_time, family, family_rank, stable_key, hierarchy, allowed_values, and `extra`.
- hierarchies, views (freshness if present), brands (from `catalogue_list_brands`), and `version`.
- It carries the existing `CatalogueSnapshot` methods (`family_head`, `conformed_sibling`, `carries`, `grain_twins_for`, …), moved here unchanged.

**Steps:**
1. Implement `McpSemanticPort` with the existing MCP client.
2. Parse tolerantly: unknown fields go to `extra`, and missing optional fields become `None`.
3. **Refresh:**
   - On each mission start, call a cheap version read. Use `catalogue_bootstrap`'s `catalogue_version`; if Core lacks a version-only call, compare the version on any response.
   - Re-warm when the version changes; keep the TTL as a fallback.
   - Freeze one read model per mission (as today in `runner.py:358`).
4. **Remove the hardcoding:**
   - `_DELIVERY_METRICS`, `_SPEND_SLICE_COMPANIONS`, the view set and `traffic_slice` in `catalogue_bootstrap.py:46-56,235-247` become a generic rank in `metrics_supporting_dimension`: prefer metrics with the same `unit` and `aggregation` as the asked metric, then the most shared supported dimensions, then a catalogue-declared `extra.redirect_rank` if Core ever provides one.
   - Brand: the tenant dimension is `space.scope_dimensions(read_model)` (`exploration/space.py`); the default brand comes from `settings.default_brand_id`. Delete the `DEFAULT_BRAND_ID` / `_BRAND_DIM_KEYS` copies in the 7 files listed in the audit.
5. **Fix `bind_catalogue` timing:** call it after every successful warm (`bootstrap.py:77` → in `warm()` success path).
6. **`config/metric_registry.yaml`:**
   - Stop using it as a vocabulary: remove `aliases` use from `resolve_alias` / `resolve_hint`, since Core concepts own synonyms.
   - Keep only agent policy fields (`direction_bad`, owning domain) **keyed by catalogue id**; validate at warm that every key exists (warn and skip, never crash).
7. **Record a fixture:** `tests/fixtures/catalogue/snapshot.json`, via a script `scripts/record_catalogue_fixture.py`.

**Tests:**
- Contract tests against the recorded fixture.
- **Pickup test:** load the fixture, mutate it (rename a metric id and label, add a dimension to a metric, add a hierarchy level, add a new metric, remove a metric), and run 5 replay cases. Expect no exception, and that the agent uses the new id or dimension without any code change. A removed metric yields a typed refusal.

**Acceptance:** no file outside `semantic/` calls `seleric.catalogue_*` directly (grep in CI); the pickup test passes.

**Delete after enforce:** direct `mcp.call(capability="seleric.catalogue_…")` call sites in `toolsets/semantic.py`, `runner.py:273,757,843`, `services/ontology.py` (they go through the port).

**Requested Core read-model fields** (not a task here; the agent works without them and uses them when present): `valid_for`, `excluded_dimensions` with reasons, hierarchies with views, view freshness, `direction_bad`, a display hint (percent/share), and a structured date axis. The port surfaces any of them via `extra` the day Core adds them.

#### M0.6 Known-defect fixes (T1, after M0.1; small, independent)
- `validation/__init__.py`: `run_validated_mission` receives the **whole prompt** as `query`, so `_unbacked_figures` treats plan or prefetch numbers as user-supplied. Pass the original question separately (new kwarg `question`).
- `executor.py:268`: the `currency_default` read is dead (the field is never in bootstrap). Read the unit through the port.
- `config/forecast_policies.yaml`: `lt_platform` and `product_sku` are not v2 dimensions. Validate them at load through the port and drop them with a warning.
- Turn record: `conversations/contracts.py::TurnRecord` is unused. Delete it, or adopt it in M3.3 (choose adopt).
- Each fix comes with a unit test and a replay case.

### Phase 1 — Understanding and the spec (start after `contracts-v1`)

#### M1.1 Understand v2 (T1)
- **Goal:** one structured LLM call producing a `QuestionDraft` (phrases and expressions; no dates, no ids required).
- **Depends:** M0.2, M0.5.
- **Files:** `src/seleric_swarm/understand/{__init__,_draft,_prompt,_call}.py` (public: `understand`, `QuestionDraft`), reusing `agent/understand.py` (`Understanding` fields, `_prompt`, `WindowSlot`/`WindowExpr` from `query_spec.py:65-110`).
- **`QuestionDraft`** = today's `Understanding` fields plus `exclusion_phrases: list[str]`, `entity_phrases`, `needs_clarification: str | None`. It is a pydantic model used as `output_type`.
- **Steps:**
  1. Move the fields into `understand/draft.py`.
  2. Build the prompt only from the read model: concept labels, dimension labels, hierarchy level names from `SemanticReadModel`, and value-candidate words. **No metric or channel words in the prompt text.**
  3. Accept `exemplars: list[VerifiedExample]` from M1.3; empty until then.
  4. Apply the failure policy A4: one retry, then `Clarification`.
- **Tests:**
  - Prompt-builder unit tests with the invented catalogue: catalogue labels appear and no literal from the allowlist does.
  - The call returns a valid draft with a scripted `FunctionModel`.
- **Acceptance:** replay spec-accuracy is at or above the current shadow baseline.
- **Flag:** `UNDERSTAND_V2_MODE`.

#### M1.2 Spec compiler and validator (T2)
- **Goal:** `compile_question_spec(draft, candidates, read_model, prior: QuestionSpec | None, as_of) -> QuestionSpec | Clarification` (pure code).
- **Depends:** M0.2, M0.5, M1.1's draft type.
- **Files:** `src/seleric_swarm/spec/{__init__,_compile,_windows,_filters,_edit,_validate}.py` (public: `compile_question_spec`, `scope_from_spec`). Reuse and move these from `agent/query_spec.py`: `resolve_window_expr` (377), `resolve_window_slots` (458), `candidates_from_resolution` (336), `_pick_entities_and_filters` (519), `validate_query_spec` (634), `apply_spec_edit` (861).
- **Rules:**
  - Windows come only from draft expressions plus `as_of` date arithmetic.
  - Filters come only from value candidates that the draft confirms as named (not `ordinary_words`).
  - Several named values on the same dimension family become **one `FilterSpec(operator="in")`** plus a breakdown on that dimension when the shape compares them; never AND across them. This replaces `executor._with_named_values` grouping.
  - Exclusions become `FilterSpec(operator="not_in")`, resolved against value candidates the same way.
  - Follow-ups: `follow_up_of` is set and the prior spec is edited (only fields the new draft sets change).
  - Validation uses the read model only: ids exist, every filter and breakdown dimension is carried by every measure (`read_model.carries`) or has a conformed sibling, and windows are ordered.
- **Tests:**
  - Port the 7 cases in `tests/fixtures/query_specs/th383_and_regressions.jsonl`.
  - New cases: exclusion; multi-source reconcile (golden Q17 shape); follow-up keeping the product filter; ambiguity → `Clarification`.
- **Acceptance:** spec accuracy ≥ 95% on the golden set in replay.
- **Flag:** `QUERY_SPEC_MODE` (existing; reuse it).
- **Wiring (in `pipeline/`, not `runner.py`):** the spec builds `RequiredScope` once via a new pure function `scope_from_spec(spec) -> RequiredScope` (in `spec/`). That replaces the six `dataclasses.replace` mutations.
- **Delete after enforce:**
  - `agent/intent.py::stated_grain` and `_STATED_GRAIN`;
  - `scope.py::_BREAKDOWN_RE` / `build_required_scope` / `_STOPWORDS`;
  - `runner.py::_question_window`, `_resolved_window`, `_resolved_window_line`, `_outside_measures`, `_WORD_EDGE`, `_fragment_terms`;
  - `scope.py::promote_verbatim_values` (its regex);
  - the intent-deciding parts of `services/time_range.py` (keep only machine-format parsing used by adapters);
  - `query_spec.py::shadow_compare` and `legacy_windows_snapshot`.

#### M1.3 Verified examples store (T3)
- **Goal:** retrieve verified `(question → QuestionSpec → template_id)` examples as understand exemplars and mark trusted plans.
- **Depends:** M0.2, M0.4 (golden cases are the seed).
- **Files:** `src/seleric_swarm/understand/examples/{__init__,_store}.py` (public: `ExampleStore`), `adapters/examples_qdrant.py` (Qdrant is already a dependency; reuse `knowledge/search.py` patterns), `eval/verified/*.yaml`.
- **Steps:**
  1. A store schema with the fields `id, question, spec (QuestionSpec json), template_id, verified_by, verified_at, catalogue_version`.
  2. `similar(question, k=3) -> list[VerifiedExample]`.
  3. Re-validate each stored spec against the current read model at load. An example whose ids no longer exist is skipped and reported; this is the semantic pickup rule.
  4. **Keep eval cases and examples separate:** the eval runner fails if a case id is also in the examples store.
- **Tests:** retrieval returns the nearest; a stale example is skipped after a catalogue mutation.
- **Acceptance:** replay spec accuracy improves or stays the same.
- **Flag:** `VERIFIED_EXAMPLES_MODE`.

#### M1.4 Clarification (T1, after M1.2)
- **Goal:** ask instead of guessing.
- **Files:** `src/seleric_swarm/spec/_clarify.py` (called from `compile_question_spec`; no separate public entry point), `pipeline/` wiring, and the office-ui message part, if needed (reuse `MessagePart` TEXT).
- **Rules:**
  - Clarify when `resolve_concept` returns a disambiguation, a required axis is missing, or a named value matches several dimensions with comparable volume.
  - Ask at most one question per turn.
  - The user's reply is a follow-up that edits the spec.
- **Tests:** each trigger has a unit test; the clarification rate is reported by the eval.
- **Flag:** `CLARIFY_MODE`.

### Phase 2 — Plan, execute, derive (start after M1.2 interfaces merge; M2.x in parallel)

#### M2.1 Planner v2 (T1)
- **Goal:** `plan(spec, read_model) -> AnalysisPlan` from a template registry.
- **Files:** `src/seleric_swarm/plan/{__init__,_registry}.py`, `plan/_templates/*.py` (public: `plan`). Port the template logic from `agent/plan.py::plan_from_slots` (534) and the executor templates (`_execute`, `_execute_single`, `_execute_composition`, `_execute_named_drivers` in `agent/executor.py`).
- **Rules:**
  - A template is a class with `id`, `matches(spec) -> bool` and `build(spec, read_model) -> AnalysisPlan`.
  - Templates: lookup, breakdown, trend, period_comparison, entity_comparison (rank then compare), composition (bridge), funnel, why → `EngineStep(engine="investigate")`, exploration → `EngineStep(engine="explore")`.
  - Ranking and periods are typed fields (`RankingSpec`, `window_role`); **delete the free-text `PlanStep.period` / `ranking`** and the string parsing in `executor.py:419,612`.
  - Unknown shapes produce `needs_agent=True`.
- **Tests:** each template with the invented catalogue; golden specs produce the expected step lists (snapshot tests).
- **Flag:** `PLANNER_V2_MODE`.

#### M2.2 Executor v2 (T2)
- **Goal:** run `QueryStep`s and return `ResultSet`s.
- **Files:** `src/seleric_swarm/plan/_execute.py` (public: `execute`, exported from `plan`). Reuse `toolsets/semantic.py::query_metrics` internals (`_conform_dimensions`, `_cached_metrics_query`, `_put_evidence`) through the port.
- **Rules:**
  - Steps run in parallel (`asyncio.Semaphore(6)`, same as today).
  - Filters come **only** from the step (no `_with_named_values`).
  - `not_in` maps to Cube `notEquals`.
  - `entities_from_step` takes the entity values from the referenced step's rows.
  - `elapsed_only` follows `services/elapsed.py`.
  - Each ResultSet carries the evidence ids of its rows.
- **Tests:** a fake port; parallel and dependent steps; a partial failure records a `StageError` while the others continue.
- **Delete after enforce:** `agent/executor.py` (whole file), `PREFETCHED` tool narrowing in `agent/agent.py:188-216`.

#### M2.3 Derivation engine (T3)
- **Goal:** all arithmetic as typed, recorded ops.
- **Files:** `src/seleric_swarm/derive/{__init__,_ops,_engine}.py` and `README.md` (public: `derive`), plus a tool `toolsets/derive.py::derive(op, inputs, params)` registered in `agent/agent.py` for the agent-loop path.
- **Ops (exact):**

| op | inputs | params | refuses when |
|---|---|---|---|
| `sum` | 1 ResultSet | `where: {dim: [values]}` optional | non-additive metric (`read_model.aggregation_for`) |
| `share` | part, whole | — | units differ |
| `delta` / `pct_change` | 2 ResultSets or Derivations | `per_day: bool` | windows of unequal length unless `per_day=True` |
| `per_day` | 1 | — | — |
| `ratio` | numerator, denominator | — | the catalogue declares a composition for the ratio metric (use `recompute` then) |
| `recompute` | ratio metric id plus component ResultSets | — | components missing; reads `composition` / `depends_on` from the read model; replaces `_MERGE_DERIVED` |
| `reconcile` | parts, total | `tolerance` | — (returns residual plus explained share) |
| `rank` | 1 | `by`, `order`, `limit` | — |
| `join` | ≥2 ResultSets | `on: [dims]` | keys not carried by all; replaces `_PREFERRED_JOIN_KEYS` |

- **Reuse:** `analytics/comparison.py::period_deltas`, `analytics/breakdown.py::shares/contributions`, `executor.py::_change`.
- **Rules:** every output is a `Derivation` persisted as an artifact (`artifact_type="derivation"`) citing its input evidence ids.
- **Tests:** each op, including each refusal; a property test that `sum` of a breakdown equals the total row when additive.
- **Acceptance:** golden reconcile questions (Q17 shape) compute every subtotal through `derive`.
- **Flag:** `DERIVE_MODE`.
- **Delete after enforce:** `toolsets/analytics.py::_MERGE_DERIVED`, `_PREFERRED_JOIN_KEYS`, the derive/merge methods of `analyze`; `run_python` stays as an escape hatch (it already unwraps findings).

### Phase 3 — Answers by construction (after M2.2 and M2.3 merge; M3.1 and M3.2 in parallel)

#### M3.1 Answer drafting and rendering (T1)
- **Goal:** the model writes an `AnswerDraft`; code renders the markdown.
- **Files:** `src/seleric_swarm/answer/{__init__,_draft_call,_render,_format}.py` (public: `draft_answer`, `render`).
- **Steps:**
  1. The agent's `output_type` becomes `AnswerDraft` (`agent/output.py`). It is mapped back to `MissionResult` for the API: `final_response = rendered_markdown`, `evidence_ids` = the union of resolved refs.
  2. `render.py`:
     - placeholders `{claim_id}` become the formatted value;
     - `TableBlock` is rendered from the ResultSet rows, with column labels from the read model and table-cell escaping reusing `services/markdown.table_cell`;
     - `ChartBlock` goes through the existing `generate_visualization` spec.
  3. `format.py` formats numbers from `unit` and `aggregation` plus a percent hint from the read model (`extra.display`) when present; otherwise it uses a generic rule. This replaces the `services/metrics.py::_SHARE_HINT` regexes.
- **Rules:** a paragraph contains no digits except inside `{}` placeholders. That check is structural (digits outside placeholders mean a revision), done by character scan, not regex intent.
- **Tests:** rendering with every block type; units; a table rendered from rows.
- **Flag:** `ANSWER_V2_MODE`.

#### M3.2 Verifier v2 (T2)
- **Goal:** structural verification of `AnswerDraft` against spec and evidence.
- **Files:** `src/seleric_swarm/answer/_verify.py` (public: `verify`, exported from `answer`). Keep `validation/signals.py` artifact checks (`check_evidence`, `check_provenance`, `check_contradiction`, `check_causal`, `check_prediction`, `check_scope_coverage`) and `trust.py` / `verdict.py`.
- **Checks:**
  1. Every claim ref resolves, and `row_key` matches a row.
  2. Role consistency: a `change` claim references a delta or pct_change derivation, and a `total` claim references a `sum` or a total row.
  3. Spec coverage: every measure, window role, breakdown and entity in the spec has at least one claim or table.
  4. Exclusions are respected: no ResultSet used lacks the spec's `not_in` filters.
  5. There are no digits outside placeholders.
  6. Status and limitations are consistent.
- **Revision:** at most 1 on structural failure (A4).
- **Tests:** one per check.
- **Acceptance:** replay revision rate < 5% and value correctness ≥ baseline.
- **Delete after enforce:**
  - `answer_audit.py`: `total_mismatch`, `without_mismatched_totals`, `_figures`, `_claim_candidates`, `ragged_table`, `realign_*`, `escape_labels_*`;
  - `coherence.py::ratio_incoherence`;
  - `grounding.py::check_answer_grounding` (the prose part);
  - `validation/__init__.py`: `_unbacked_figures`, `_derived_from_shown`, `_mission_labels`, `_same_metric_arithmetic`, `_unequal_window_change`, `_repair_citations`, auto-cite, `_strip_tables_when_charted`, `_salvage`;
  - and the corresponding sections of `agent/instructions.py`. Track the prompt-size reduction in the PR.

#### M3.3 Turn record v2 (T3)
- **Goal:** a typed turn record built from the spec and the draft (no regex over the answer).
- **Files:** adopt `conversations/contracts.py::TurnRecord`, extended with `spec: QuestionSpec`, `offer: str` (from `AnswerDraft.next_step`), `windows`, `claim_labels`, `entities` (from spec and ResultSets).
- **Delete after enforce:** `runner.py::_parse_named_entities`, `_PERIOD_LINE`, `_answer_period`, `_closing_offer`.
- **Tests:** a follow-up edit using the stored spec.

### Phase 4 — Reasoning engines (M4.1 and M4.2 parallel; M4.3 after both)

#### M4.1 Context providers (T1)
- **Goal:** events and data health that diagnosis can cite.
- **Files:** `src/seleric_swarm/context/{__init__,_model,_calendar,_incidents,_changes,_health,_registry}.py` (public: `events`, `health`, `ContextEvent`, `DataHealth`).
- **Interface:**
  - `ContextEvent(kind, start, end, scope: dict[str,str], source, title, detail)`.
  - `ContextProvider.events(window, scope, port) -> list[ContextEvent]`.
  - `DataHealth(metric_id, day, status: ok|late|incomplete|incident, detail)`.
- **Providers:**
  - `CalendarProvider`: reuse `forecasting/calendar.py` loaders on `config/calendar_in.yaml`.
  - `IncidentProvider`: `config/data_incidents.yaml`, via `forecasting/quality.py`.
  - `ChangeLogProvider`: catalogue metrics whose read-model `grain` / `extra` mark them as change events (discovered through the port, no names), queried for the window. If none exist, it returns nothing.
  - `HealthProvider`: business-state and domain-health snapshots (`services/domain_health/snapshot_store.py`) plus view freshness from the read model when present.
- **Rules:** providers are listed in `config/context_providers.yaml` (enable/disable); there are no business literals in code.
- **Tests:** each provider with fixtures; a disabled provider returns nothing.

#### M4.2 Metric graph (T2)
- **Goal:** identity and causal edges as one graph.
- **Files:** `src/seleric_swarm/graph/{__init__,_model,_build}.py` (public: `metric_graph`, `MetricGraph`), `config/causal_graph.yaml` (rename from `causal_graphs.example.yaml`; keyed by **catalogue ids or concept names**).
- **Build:**
  - Identity edges come from the read model's `composition` / `depends_on`. Reuse `causal/diagnosis.py::lineage_from_definitions`, `discover_identities`, `rate_chain_proposals`.
  - Causal edges come from the YAML, each with sign and lag.
  - Every node is validated against the read model at warm. Unknown nodes are skipped and reported in the port's health; this is the semantic pickup rule.
- **Optional adapter:** DoWhy-GCM `attribute_anomalies` / `distribution_change` (DoWhy is already a dependency) behind `GRAPH_GCM_MODE`.
- **Tests:** identity edges from the invented catalogue; an unknown YAML node is skipped.
- **Delete:** `config/diagnostic_ontology.yaml` (unused) after confirming no reader.

#### M4.3 Investigation controller (T3, after M4.1 and M4.2)
- **Goal:** a deep "why" as a code state machine over the existing engines.
- **Files:** `src/seleric_swarm/investigate/{__init__,_state,_controller,_select}.py`, `investigate/_steps/*.py` (public: `investigate`); registered as `EngineStep(engine="investigate")` and as tool `investigate` (replacing `diagnose_metric_change` in the why template).
- **States, in order:**
  1. `premise`: event sizing; reuse `causal/diagnosis.py::diagnose` event layer. Stop with "premise false" if not confirmed.
  2. `health`: M4.1 `HealthProvider`. Unhealthy data adds a limitation, or stops if incomplete.
  3. `decompose`: identities and Shapley from the M4.2 graph; reuse `diagnose` decomposition.
  4. `localise`: segment shift (reuse `diagnose` localisation and `exploration/insights.py::distribution_shift`).
  5. `recurse`: if the top segment explains ≥ `INVESTIGATE_RECURSE_SHARE` (policy_config, default 0.5) and depth < `INVESTIGATE_MAX_DEPTH` (default 3), re-run 3–4 with that segment as a filter.
  6. `context`: M4.1 events overlapping the event window and scope.
  7. `drivers`: graph causal parents; reuse `diagnose` DoWhy driver tests.
  8. `stop`: when explained share ≥ `INVESTIGATE_EXPLAINED_TARGET` (default 0.8), or the budget is spent.
- **`select.py`:** at the `recurse` / `drivers` branch points, one structured LLM call chooses among **typed candidates** (segments or parents with their numbers) and gives a one-line rationale. The engine numbers are never changed by the LLM.
- **State:** `InvestigationState` (pydantic) persisted as an artifact, so "dig deeper" resumes from it.
- **Output:** Findings plus Derivations plus `Claim`s for M3.
- **Tests:** planted incidents (synthetic series, like `tests/unit/test_exploration_engine.py`): a segment collapse found at depth 2, a festival event cited, an unhealthy day stopping the run, and a premise that is false. Plus replays of real incidents from `docs/OPEN_GAPS_*`.
- **Acceptance:** "why" eval tag: root cause at the right depth ≥ target; no causal wording without a `supported_cause` driver.
- **Flag:** `INVESTIGATE_MODE`.

#### M4.4 Exploration integration (T3)
- `explore_data` (`toolsets/exploration.py`) follow-ups become typed `EngineStep`s.
- The exploration result is stored per thread (`ExplorationMap` artifact) so "what else?" continues.
- The overview kind routes to `explore` instead of the snapshot fast path when a period is named.
- **Tests:** a follow-up continues from the stored map.

### Phase 5 — Operations and consolidation (parallel)

#### M5.1 LLM operations (T1)
- **Role config:** `config/llm_roles.yaml` with roles `understand | tool_loop | answer | select`, each with a deployment, effort and timeout. This replaces the env sprawl in `agent/model.py`.
- **Model pinning:** pin a model family per mission. The fallback restarts the *stage*, never mid-conversation; change `FallbackModel` use in `agent/model.py:44`.
- **Structured outputs:** use them for every role.
- **Prompt registry:** prompts live in `prompts/<role>/<version>.md`, loaded by version. The eval report records the prompt version.
- **Track:** `instructions.py` size per release, which falls as M3.2 deletes rules.

#### M5.2 Memory (T2)
- Thread spec history comes from M3.3.
- A per-brand preferences store (`brand_prefs` table: default net/gross basis, default window, focus channels) is fed into spec compile as defaults and recorded as `assumptions`.

#### M5.3 Latency tiers (T3)
- Per-template soft deadlines in `policy_config` (lookup 10 s, comparison 30 s, investigate 90 s).
- On the deadline, wrap up with what the ResultSets hold (reuse `_wrap_up` behaviour in `validation/__init__.py:1153`).
- Report p50 and p90 per tag in the eval.

#### M5.4 Single agent brain (T5) — Core, not the semantic layer
- The dashboard chat calls the Seleric_Agent conversation API (`api/conversations.py`) with module/brand scope.
- Retire `seleric_agent_core/src/seleric_mcp/gateway/analyst.py`, `llm/agent_core.py` and `gateway/prompts.py` (the latter hardcodes v1 ids).
- Keep Core's MCP tools.
- **Tests:** a contract test from the dashboard API shape; parity on 10 dashboard questions.

---

## Part D — Dependency graph and parallel schedule

```
Phase 0:  M0.1 ─┐  M0.2 (freeze day 3) ─┬─ M0.3   M0.4   M0.5   M0.6(after M0.1)
Phase 1:        └──────────────────────► M1.1 ──► M1.4
                                         M1.2 (needs M1.1 draft type) ──┐
                                         M1.3 (needs M0.4 cases)        │
Phase 2:                                 M2.1  M2.2  M2.3  ◄────────────┘ (need M1.2 spec)
Phase 3:                                 M3.1  M3.2 (need M2.2, M2.3) ──► M3.3
Phase 4:                                 M4.1  M4.2 ──► M4.3 ──► M4.4   (M4.1/M4.2 may start in Phase 2)
Phase 5:                                 M5.1  M5.2  M5.3  M5.4          (M5.1 may start any time after M0.3)
```

**Track assignment for 5 agents.** One agent keeps one track for the whole programme. Cursor takes the tracks that freeze interfaces or move the existing brain. OpenCode takes the tracks that are new code against those frozen interfaces.

| Track | Tool | Phase 0 | Phase 1 | Phase 2 | Phase 3 | Phase 4 | Phase 5 |
|---|---|---|---|---|---|---|---|
| T1 | OpenCode | M0.1 → M0.6 | M1.1 → M1.4 | M2.1 | M3.1 | M4.1 | M5.1 |
| T2 | Cursor | M0.2 | M1.2 | M2.2 | M3.2 | M4.2 → M4.3 | M5.2 |
| T3 | OpenCode | M0.3 | M1.3 | M2.3 | M3.3 | M4.4 | M5.3 |
| T4 | OpenCode | M0.4 | eval cases, cassettes, baselines | stay on the suite | stay on the suite | stay on the suite | stay on the suite |
| T5 | Cursor | M0.5 | port cleanup | — | — | — | M5.4 |

**Merge order inside a phase:** contracts → port → producers → consumers. A consumer behind its flag can merge first against a stub of the producer.

**Day 1.** All five start together, on `gaurav`. Land T1's guardrails first, then tag T2's contracts (`contracts-v1`, day 3), then land T5's port. T3 and T4 draft against Part B and continue on `gaurav` after `contracts-v1`.

| Agent | Branch | Blocked until |
|---|---|---|
| T1 | `gaurav` | nothing |
| T2 | `gaurav` | nothing; tag `contracts-v1` on day 3 |
| T3 | `gaurav` | `contracts-v1` |
| T4 | `gaurav` | `contracts-v1` |
| T5 | `gaurav` | `contracts-v1` |

**Handoffs.**

- T1 publishes `QuestionDraft` before T2 starts M1.2. T1 adds `spec/_clarify.py` only after T2's `compile_question_spec` has merged.
- T3's M1.3 waits for T4's golden cases. T2's M3.2 waits for M2.2 and T3's M2.3. T2's M4.3 waits for T1's M4.1 and T2's M4.2.
- T4 does not pick up feature modules. M5.4 belongs to T5, not T4.
- T1's M4.1 may start during Phase 2. T1's M5.1 may start any time after T3's M0.3. T2's M4.2 may start during Phase 2.

### Note for every agent — this file is the status channel

Read this note before writing code. Read [`plans/AGENT_CHAT.md`](AGENT_CHAT.md) at the start of every session. Post findings, questions, and handoffs there, and keep posting as the work moves. Module status stays in the board below.

1. Every change is committed on the `gaurav` branch. If you are on any other branch now, move that work onto `gaurav` before the next commit and continue there. Do not open or keep a `mod/*` branch. The `Branch` column below is `gaurav`.
2. On every session start, read `plans/AGENT_CHAT.md`. When you learn something another track needs, add a findings row. When you ask, answer, or hand off, append a chat line. Do this in the same commit as the code or the status change.
3. On starting a module, set its row below to `in progress` and append one log line.
3. When blocked, set `blocked` and name the module you are waiting on in `Note`. Do not start a later module on your track to skip the block, except where this plan explicitly allows an early start (M4.1, M4.2, M5.1).
4. When the change is ready for review, set `in review` and put the PR URL in `PR` if there is one.
5. When the module is on `gaurav` and its checks pass, set `merged`, add the date, and append one log line. Then start the next module on your track.
6. Edit only your own rows. You may append a log line if you unblocked another track. Do not rewrite another track's status or delete log lines.
7. A change contains that module's package, its tests, and its README, plus one flag line in `config/settings.py` (`Literal["off","shadow","enforce"]`, default `"off"`). Do not edit another module's flag. Do not add logic to `agent/runner.py`.
8. After `contracts-v1`, only T2 changes a frozen contract, and only with an ADR.
9. If the plan itself is wrong, change the module text in the same change and say so in the log. Status rows and the log stay in this file so the next agent sees the truth.

**Status board.** Values: `not started` | `in progress` | `blocked` | `in review` | `merged`.

| Module | Track | Tool | Branch | Status | PR | Note | Updated |
|---|---|---|---|---|---|---|---|
| M0.1 | T1 | OpenCode | | not started | | | 2026-10-10 |
| M0.2 | T2 | Cursor | `gaurav` | merged | https://github.com/Tervigon-Collective/Seleric_Agent/pull/5 | landed on `gaurav` (`fdb43ce`); tag `contracts-v1` | 2026-10-10 |
| M0.3 | T3 | OpenCode | | not started | | rebase onto `contracts-v1` | 2026-10-10 |
| M0.4 | T4 | OpenCode | | not started | | rebase onto `contracts-v1` | 2026-10-10 |
| M0.5 | T5 | Cursor | | not started | | rebase onto `contracts-v1` | 2026-10-10 |
| M0.6 | T1 | OpenCode | | not started | | after M0.1 | 2026-10-10 |
| M1.1 | T1 | OpenCode | | not started | | after `contracts-v1` and M0.5; publish `QuestionDraft` | 2026-10-10 |
| M1.2 | T2 | Cursor | | not started | | after `contracts-v1`, M0.5, and M1.1 draft type | 2026-10-10 |
| M1.3 | T3 | OpenCode | | not started | | after M0.4 golden cases | 2026-10-10 |
| M1.4 | T1 | OpenCode | | not started | | after M1.2 merges; `spec/_clarify.py` only | 2026-10-10 |
| M2.1 | T1 | OpenCode | | not started | | after M1.2 | 2026-10-10 |
| M2.2 | T2 | Cursor | | not started | | after M1.2 | 2026-10-10 |
| M2.3 | T3 | OpenCode | | not started | | after M1.2 | 2026-10-10 |
| M3.1 | T1 | OpenCode | | not started | | after M2.2 and M2.3 | 2026-10-10 |
| M3.2 | T2 | Cursor | | not started | | after M2.2 and M2.3 | 2026-10-10 |
| M3.3 | T3 | OpenCode | | not started | | after M2.2 and M2.3 | 2026-10-10 |
| M4.1 | T1 | OpenCode | | not started | | may start in Phase 2 | 2026-10-10 |
| M4.2 | T2 | Cursor | | not started | | may start in Phase 2 | 2026-10-10 |
| M4.3 | T2 | Cursor | | not started | | after M4.1 and M4.2 | 2026-10-10 |
| M4.4 | T3 | OpenCode | | not started | | after M4.3 | 2026-10-10 |
| M5.1 | T1 | OpenCode | | not started | | any time after M0.3 | 2026-10-10 |
| M5.2 | T2 | Cursor | | not started | | after M3.3 | 2026-10-10 |
| M5.3 | T3 | OpenCode | | not started | | after M2.1 templates | 2026-10-10 |
| M5.4 | T5 | Cursor | | not started | | Phase 5; Core `analyst.py`, not T4 | 2026-10-10 |

**Log.** Append only. Newest at the bottom.

| Date | Track | Module | What changed |
|---|---|---|---|
| 2026-10-10 | — | — | Status board opened. T1/T3/T4 = OpenCode. T2/T5 = Cursor. M5.4 assigned to T5. |
| 2026-10-10 | T2 | M0.2 | Branch `mod/M0.2-pipeline-contracts` cut from `gaurav`; implementing Part B models. |
| 2026-10-10 | T2 | M0.2 | PR open: https://github.com/Tervigon-Collective/Seleric_Agent/pull/5 — Part B models + dump/load/fingerprint; 40 unit tests. Tag `contracts-v1` after merge. |
| 2026-10-10 | — | — | Every change lands on `gaurav`. Work already on another branch, including `mod/M0.2-pipeline-contracts`, moves onto `gaurav`. |
| 2026-10-10 | T2 | M0.2 | Fast-forwarded onto `gaurav` (`fdb43ce`). Closing mod/* PR; tagging `contracts-v1`. |

## Part E — Files you will touch most (and the rule for each)
- `agent/runner.py` (1,827 lines): **do not add logic**. M1.2/M2.x/M3.x move stage code into `pipeline/mission.py` (`run_mission(question, deps) -> AnswerDocument`), and `run_v3_mission` becomes a thin adapter. Each module removes its block from `runner.py` when it reaches enforce.
- `agent/agent.py`: tool registration only. Tool narrowing flags shrink as M2.2 lands.
- `config/settings.py`: add flags as `Literal["off","shadow","enforce"]` with default `"off"`, one per module: `UNDERSTAND_V2_MODE`, `QUERY_SPEC_MODE` (existing), `VERIFIED_EXAMPLES_MODE`, `CLARIFY_MODE`, `PLANNER_V2_MODE`, `DERIVE_MODE`, `ANSWER_V2_MODE`, `INVESTIGATE_MODE`, `GRAPH_GCM_MODE`.
- `toolsets/policy_config.py`: all thresholds (no literals inside modules).

## Verification (end to end)
1. **Per PR:**
   - `uv run ruff check . && uv run mypy src && uv run lint-imports && uv run python scripts/lint_semantic_literals.py && uv run pytest -q`
   - plus `uv run python scripts/eval_run.py --mode replay --compare eval/baselines/golden.json` (no metric worse than baseline).
2. **Semantic pickup:** `uv run pytest tests/contract/test_semantic_pickup.py` (catalogue mutations need zero code change).
3. **Before deploy (Jenkins):** `scripts/eval_run.py --mode live` against ClickHouse truth; block on regression. After deploy, run the service health check for `api`, `recovery` and `business-state-cron`.
4. **Flag promotion:** run a module in `shadow` for at least one day of live traffic, then check Langfuse disagreement counts and the live eval before flipping to `enforce`, then merge the legacy-deletion PR.
5. **Programme-level targets** (tracked in the eval report):

| Measure | Target |
|---|---|
| Golden value correctness | ≥ baseline, then 100% |
| Revision rate | < 5% |
| Missions with prose arithmetic | 0 |
| Validation code (`agent/validation/*`) | under 1,500 lines (from 3,842) |
| `instructions.py` | under 15 KB (from 35 KB) |
| Semantic-literal allowlist | 0 entries |
| p50 / p90 latency per tag | meets M5.3 |
