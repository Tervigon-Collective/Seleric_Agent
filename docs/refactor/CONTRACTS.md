# Seleric V3 — Sprint 0 Frozen Contracts

Status: **frozen** 2026-09-18 (Sprint 0 gate). Changes after this point
require sign-off from all three profiles (per `SPRINT_PLAN.md` Sprint 0
gate) — edit this file and note the change in `TASK_SHEET.md`'s Log, don't
silently drift the shape in code.

Grounding: read before drafting, not assumed —
`src/seleric_swarm/conversations/contracts.py` (existing `Artifact`,
`ArtifactProvenance`, `Principal`, `ContextBundle`),
`src/seleric_swarm/conversations/context.py::ContextBuilder.build()`,
`src/seleric_swarm/coordinator/governance/budget.py::MissionLimits`.
Sprint 0 freeze introduced `SelericDeps`/`ToolResult`; by 2026-09-18 they
are materialized in `agent/dependencies.py` / `agent/output.py` with
`pydantic-ai-slim` installed (A1.6). Artifact envelope, provenance, and
context assembly are reused as-is, not rebuilt.

## 1. `SelericDeps`

Plain dataclass (PydanticAI convention — deps are not validated Pydantic
models), one instance per mission run, immutable for the run's lifetime.

```python
# agent/dependencies.py
@dataclass(frozen=True)
class SelericDeps:
    mission_id: str
    as_of: datetime            # one per mission (non-negotiable rule 7), UTC, tz-aware
    principal: Principal       # reuse conversations/contracts.py::Principal, not a new identity model
    thread_id: str
    run_id: str
    trace_id: str
    context: ContextBundle     # reuse conversations/contracts.py::ContextBundle, built once via ContextBuilder.build()
    mcp_client: SelericMcpClient   # thin wrapper over the mcp__seleric-mcp__* tools; owned by Profile B
    artifact_store: ArtifactStore  # owned by Profile A, state/artifacts.py
    limits: ExecutionLimits         # see below
```

`ExecutionLimits` — supersedes `governance/budget.py::MissionLimits` (that
class's fields carry over by name where the concept survives; two fields
are new per spec §39/non-negotiable rule 11, everything else is a rename
not a redesign):

```python
@dataclass(frozen=True)
class ExecutionLimits:
    max_tool_calls: int = 8
    max_cube_queries: int = 6          # new — was implicit inside max_tool_calls before
    max_causal_queries: int = 3        # new
    max_prediction_calls: int = 3      # new
    max_validation_revisions: int = 1  # new — the EvidenceValidator's bounded retry
    max_runtime_seconds: float = 120.0 # carried from MissionLimits.max_runtime_seconds
```

`max_llm_calls`, `max_agent_calls`, `max_leadership_transfers`,
`max_iterations` from the old `MissionLimits` do **not** carry over —
they were LangGraph/swarm-specific (multi-agent handoff counting); a
single-agent loop has one call boundary (`max_tool_calls`) instead.

## 2. `ToolResult` envelope

Every toolset function returns this — the one shape the agent loop and the
`EvidenceValidator` reason about, regardless of which of the seven
toolsets produced it.

```python
# agent/output.py (or a shared contracts module both A/B/C import)
class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool
    artifact_ids: list[str] = Field(default_factory=list)  # ids into ArtifactStore, empty on failure
    summary: str                     # short, agent-readable — not the full payload
    provenance: ArtifactProvenance   # reuse conversations/contracts.py::ArtifactProvenance verbatim
    warnings: list[str] = Field(default_factory=list)
    error_code: str | None = None    # set only when success=False — see allowed codes below
    retryable: bool = False          # only meaningful when success=False
```

Contract rule: `success=False` requires `error_code` set and
`artifact_ids` empty. `success=True` requires at least one `artifact_id`
for any toolset that produces evidence/findings (Semantic, Analytics,
Causal, Models); Action/Knowledge/Experiment toolsets may return
`success=True` with zero artifacts for non-evidence-producing calls
(e.g. `actions_status` polling, a knowledge-search miss).

Allowed `error_code` values (Amendment A1 accepted 2026-09-18; shape stays
`str | None` so new codes can be added without a schema break):

| Code | When |
|---|---|
| `INSUFFICIENT_EVIDENCE` | Rule 16 / policy-gate precondition decline (A1.3); also validator fail-closed when revisions exhausted |
| `EVIDENCE_GRAIN_MISMATCH` | Analytics grain/span precondition failed (A1.2) |
| `EXECUTION_LIMIT_EXCEEDED` | `ExecutionBudgetTracker` refused a consume / runtime check |

## 3. Artifact payload schemas

All four are stored as `Artifact.payload` (existing envelope, unchanged)
with `Artifact.artifact_type` set to the discriminator below. Profile A
owns the `ArtifactStore`; Profile B is the only writer of
`EvidenceArtifact`; Profile C is the only writer of `Finding`,
`CausalArtifact`, `PredictionArtifact`. No other profile writes these
types directly (non-negotiable rule 6/8).

### `EvidenceArtifact` (`artifact_type="evidence"`, `classification="factual"`)

Immutable once written (rule 8) — never edited in place; a correction is a
new `EvidenceArtifact` plus a `Finding` with `status=REJECTED,
supersedes=<old_finding_id>` if a downstream finding depended on it.

```python
class EvidenceArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric_id: str              # catalogue id, as returned by seleric-mcp, never a local alias
    dimensions: dict[str, str] = Field(default_factory=dict)
    grain: Literal["day", "week", "month", "none"]
    as_of: datetime             # inherited from SelericDeps.as_of
    period_start: datetime
    period_end: datetime
    value: float | None         # None only paired with a warning, never a fabricated 0
    unit: str | None = None
    source_query: dict[str, Any]  # the exact query_metrics()/drilldown() args sent to seleric-mcp
    fetched_at: datetime = Field(default_factory=_utc_now)
```

### `Finding` (`artifact_type="finding"`, `classification="derived"`)

Output of Analytics toolset calculations over already-fetched
`EvidenceArtifact`s (rule 5 — never fetches its own data).

```python
class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finding_type: str           # e.g. "anomaly", "period_comparison", "contribution"
    statement: str              # short natural-language claim, evidence-traceable
    evidence_ids: list[str] = Field(min_length=1)   # into EvidenceArtifact, enforced by Artifact.require_provenance()
    metrics: dict[str, float] = Field(default_factory=dict)  # e.g. {"z_score": 3.2, "delta_pct": -12.4}
    status: Literal["ACTIVE", "REJECTED"] = "ACTIVE"
    supersedes: str | None = None    # Finding id this one replaces; never edits history in place
```

### `CausalArtifact` (`artifact_type="causal"`, `classification="derived"`)

```python
class CausalArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: dict[str, Any]        # the CausalQuery that produced this
    evidence_classification: Literal[
        "OBSERVATION", "ASSOCIATION", "HYPOTHESIS",
        "CAUSALLY_SUPPORTED", "EXPERIMENTALLY_VALIDATED",
    ]                            # non-negotiable rule 9 — required, no default
    effect_estimate: float | None
    refutation_checks: list[dict[str, Any]] = Field(default_factory=list)  # rule 19 — required before CAUSALLY_SUPPORTED
    evidence_ids: list[str] = Field(min_length=1)
    method: str                  # e.g. "dowhy.backdoor.linear_regression"

    @model_validator(mode="after")
    def causally_supported_requires_refutation(self) -> "CausalArtifact":
        if self.evidence_classification in {"CAUSALLY_SUPPORTED", "EXPERIMENTALLY_VALIDATED"} and not self.refutation_checks:
            raise ValueError("CAUSALLY_SUPPORTED/EXPERIMENTALLY_VALIDATED requires refutation_checks")
        return self
```

### `PredictionArtifact` (`artifact_type="prediction"`, `classification="derived"`)

```python
class PredictionArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    model_id: str                # required, rule 10 — no bare number without knowing the model
    model_version: str
    prediction_type: str         # e.g. "forecast", "ltv", "churn_propensity"
    value: float
    confidence_interval: tuple[float, float] | None = None
    evidence_ids: list[str] = Field(min_length=1)   # training/input evidence, not a live re-fetch
    feature_leakage_checked: bool  # rule 20 — required field, not optional; False must be explicit, never silently absent
```

## 4. Seven toolsets — frozen names and signatures

Function names/params/return types only — no implementation. All return
`ToolResult`; the artifact(s) it references live in `ArtifactStore`,
fetched separately via `artifact_ids`.

**Semantic** (`toolsets/semantic.py`, Profile B)
```python
def search_semantics(ctx: RunContext[SelericDeps], query: str) -> ToolResult: ...
def get_metric_definition(ctx: RunContext[SelericDeps], metric_id: str) -> ToolResult: ...
def query_metrics(ctx: RunContext[SelericDeps], metric_id: str, dimensions: dict[str, str], grain: str, period_start: datetime, period_end: datetime) -> ToolResult: ...
def drilldown(ctx: RunContext[SelericDeps], metric_id: str, dimension: str, period_start: datetime, period_end: datetime) -> ToolResult: ...
```

**Action** (`toolsets/actions.py`, Profile B)
```python
def propose_action(ctx: RunContext[SelericDeps], action_type: str, action_params: dict[str, Any]) -> ToolResult: ...
def validate(ctx: RunContext[SelericDeps], approval_id: str) -> ToolResult: ...
def preview(ctx: RunContext[SelericDeps], approval_id: str) -> ToolResult: ...
def commit_action(ctx: RunContext[SelericDeps], approval_id: str, idempotency_key: str) -> ToolResult: ...
```

**Analytics** (`toolsets/analytics.py`, Profile C — pure functions over evidence, no fetch)

Grain precondition (A1.2), binding on `compare_periods()` and
`detect_anomalies()`: every `EvidenceArtifact` in a single call must share
one `grain`, and the observation's period span must match the baseline's
period span. Violation → `success=False`, `error_code="EVIDENCE_GRAIN_MISMATCH"`,
empty `artifact_ids`. Never normalize a multi-period aggregate to make it
comparable; never compare across grains silently. Implemented in
`analytics/grain.py::validate_grain_set()`.

```python
def compare_periods(ctx: RunContext[SelericDeps], evidence_ids: list[str]) -> ToolResult: ...
def detect_anomalies(ctx: RunContext[SelericDeps], evidence_ids: list[str], method: Literal["robust_zscore", "mad", "seasonal"] = "robust_zscore") -> ToolResult: ...
def contribution_analysis(ctx: RunContext[SelericDeps], evidence_ids: list[str], dimension: str) -> ToolResult: ...
def segment_decomposition(ctx: RunContext[SelericDeps], evidence_ids: list[str], dimensions: list[str]) -> ToolResult: ...
def funnel_decomposition(ctx: RunContext[SelericDeps], evidence_ids: list[str]) -> ToolResult: ...
def cohort_analysis(ctx: RunContext[SelericDeps], evidence_ids: list[str]) -> ToolResult: ...
```

**Causal** (`toolsets/causal.py`, Profile C)

`search_breadth` (A1.1) is the typed escalation ladder replacing hidden
`remediation_round` state. Maps to today's widening arithmetic:
`history_days = 30 * (1 + search_breadth)`,
`candidate_cap = base_cap + search_breadth`. Bounded at 2 so rule 11 holds
without a counter living in the tool. Hypothesis discovery stays inside
`estimate_effect` — no separate `discover_hypotheses()`.

```python
def estimate_effect(
    ctx: RunContext[SelericDeps],
    evidence_ids: list[str],
    treatment: str,
    outcome: str,
    method: str = "backdoor.linear_regression",
    search_breadth: Literal[0, 1, 2] = 0,
) -> ToolResult: ...
def refute_estimate(ctx: RunContext[SelericDeps], causal_artifact_id: str) -> ToolResult: ...
```

**Model** (`toolsets/models.py`, Profile C)
```python
def forecast(ctx: RunContext[SelericDeps], evidence_ids: list[str], horizon_days: int) -> ToolResult: ...
def predict_ltv(ctx: RunContext[SelericDeps], evidence_ids: list[str]) -> ToolResult: ...
def predict_propensity(ctx: RunContext[SelericDeps], evidence_ids: list[str], event: str) -> ToolResult: ...
```

**Knowledge** (`toolsets/knowledge.py`, Profile C — schema-forbidden from
returning a bare numeric metric value, per overview §9 suggestion 3)
```python
def search_knowledge(ctx: RunContext[SelericDeps], query: str) -> ToolResult: ...   # ToolResult.summary is text/citations only, never a metric value
```

**Experiment** (`toolsets/experiments.py`, Profile C)
```python
def get_experiment_history(ctx: RunContext[SelericDeps], experiment_id: str | None = None) -> ToolResult: ...
def estimate_sample_size(ctx: RunContext[SelericDeps], baseline_rate: float, mde: float, power: float = 0.8) -> ToolResult: ...
def evaluate_experiment(ctx: RunContext[SelericDeps], experiment_id: str, evidence_ids: list[str]) -> ToolResult: ...
```

## Amendment A1 — preconditions and escalation

Status: **ACCEPTED 2026-09-18** (three-profile sign-off). Applied into
frozen §2 (error codes) and §4 (Analytics grain rule + Causal
`search_breadth`) above. Rationale in `03_PROFILE_CAPABILITIES.md` §3 and
§6. Standing rule: **if the caller picks the inputs, the tool must validate
them.**

### A1.1 — Causal escalation surface — ACCEPTED

`search_breadth: Literal[0, 1, 2] = 0` on `estimate_effect` (see §4).
Hypothesis discovery stays inside `estimate_effect` — no
`discover_hypotheses()`. Unblocks Profile C Sprint 2 Causal work.

### A1.2 — Analytics grain precondition — ACCEPTED

Binding rule live in §4 Analytics and in
`analytics/grain.py::validate_grain_set()` (Sprint 1 Profile C).

### A1.3 — Precondition refusal semantics — ACCEPTED

Policy-gate declines → `success=False`, `error_code="INSUFFICIENT_EVIDENCE"`,
named unmet condition in `warnings`, `retryable=False`. The five
`config/*_policies.yaml` files stay until Profile C's Sprint 2 policy-port
migrates thresholds into toolset config; do not delete them as part of this
acceptance.

### A1.4 — Evidence-classification migration — ACCEPTED (owner = Profile C)

Mapping to apply as one reviewed change during Causal Sprint 2 (not part of
A1 landing itself):

| Current value / site | New value |
|---|---|
| `"CAUSALLY_SUPPORTED_UNDER_ASSUMPTIONS"` — `causal/estimator.py:135` | `CAUSALLY_SUPPORTED` (+ populated `refutation_checks`) |
| `agents/diagnostic/contracts.py:43`, `policies.py:19` | `CAUSALLY_SUPPORTED` |
| `agents/diagnostic/policies.py:85` (default for `retain_at_or_above`) | `CAUSALLY_SUPPORTED` |
| `agents/skeptic/contracts.py:74`, `registries.py:519` | `CAUSALLY_SUPPORTED` |
| `services/dowhy_causal.py:37,100` | `CAUSALLY_SUPPORTED` |
| `config/diagnostic_policies.yaml:24` (`retain_at_or_above`) | `CAUSALLY_SUPPORTED` |

`ASSOCIATION_ONLY` (seen in bug #6's trace) maps to `ASSOCIATION`.

### A1.5 — Signature reconciliation — ACCEPTED (documentation-only)

| Toolset | Frozen in §4 | Struck from the brief |
|---|---|---|
| Analytics | 6 functions | `rolling_statistics`, `growth_rate`, `change_point`, `association_analysis` (`robust_zscore`/`seasonal_anomaly` fold into `detect_anomalies(method=)`) |
| Model | 3 functions | `simulate`, `predict_reverse_risk`, `predict_demand` |
| Experiment | 3 functions | `design_experiment`, `compare_variants`, `recommend_next_test` |

### A1.6 — `pydantic-ai` dependency — ACCEPTED / CLOSED

`pydantic-ai-slim>=2.45` is the declared dependency in `pyproject.toml`
(slim variant chosen deliberately — full `pydantic-ai` pulls unrelated SDKs).
Stub agent builds and runs against `RunContext[SelericDeps]` (Profile A
Sprint 1). §4 signatures are implementable-as-written against the installed
framework.

### Joint decisions recorded with A1 acceptance (A + C, Sprint 2)

1. **`max_validation_revisions = 1` confirmed.** Causal escalation is
   `search_breadth` (caller-chosen on `estimate_effect`), not the
   validation-revision counter. On STRONG-trust + REVISE when revisions are
   exhausted → fail closed with `INSUFFICIENT_EVIDENCE` (matches
   `agent/validation.py::run_validated_mission`).
2. **Skeptic → EvidenceValidator is a change in kind**, not a consolidation:
   cross-agent adversarial challenge becomes in-context self-review. Accepted
   trade-off; see `03_PROFILE_CAPABILITIES.md` §4.

## Change log

- 2026-09-18: initial freeze, Sprint 0.
- 2026-09-18: **Amendment A1 proposed** (not applied) — precondition and
  escalation semantics for Analytics/Causal, policy-gate refusal codes,
  evidence-classification migration mapping, brief/contract signature
  reconciliation, `pydantic-ai` dependency spike. Filed by Profile C,
  pending A and B sign-off.
- 2026-09-18: **Amendment A1 ACCEPTED** — applied into frozen §2/§4:
  `search_breadth` on `estimate_effect`; Analytics grain precondition;
  named `error_code` values; A1.4 owner = Profile C; A1.6 closed on
  `pydantic-ai-slim`. Joint decisions: keep `max_validation_revisions = 1`
  (causal ladder is `search_breadth`); skeptic→validator recorded as a
  change in kind. Unblocks Profile C Sprint 2 Causal toolset.
