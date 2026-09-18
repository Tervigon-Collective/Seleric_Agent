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
No `SelericDeps`/`ToolResult`/PydanticAI usage exists anywhere in this repo
today (confirmed by grep) — these four contracts are genuinely new, the
rest of the runtime around them (artifact envelope, provenance, context
assembly) already exists and is reused as-is, not rebuilt.

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
    error_code: str | None = None    # e.g. INSUFFICIENT_EVIDENCE (rule 16) — set only when success=False
    retryable: bool = False          # only meaningful when success=False
```

Contract rule: `success=False` requires `error_code` set and
`artifact_ids` empty. `success=True` requires at least one `artifact_id`
for any toolset that produces evidence/findings (Semantic, Analytics,
Causal, Models); Action/Knowledge/Experiment toolsets may return
`success=True` with zero artifacts for non-evidence-producing calls
(e.g. `actions_status` polling, a knowledge-search miss).

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
```python
def compare_periods(ctx: RunContext[SelericDeps], evidence_ids: list[str]) -> ToolResult: ...
def detect_anomalies(ctx: RunContext[SelericDeps], evidence_ids: list[str], method: Literal["robust_zscore", "mad", "seasonal"] = "robust_zscore") -> ToolResult: ...
def contribution_analysis(ctx: RunContext[SelericDeps], evidence_ids: list[str], dimension: str) -> ToolResult: ...
def segment_decomposition(ctx: RunContext[SelericDeps], evidence_ids: list[str], dimensions: list[str]) -> ToolResult: ...
def funnel_decomposition(ctx: RunContext[SelericDeps], evidence_ids: list[str]) -> ToolResult: ...
def cohort_analysis(ctx: RunContext[SelericDeps], evidence_ids: list[str]) -> ToolResult: ...
```

**Causal** (`toolsets/causal.py`, Profile C)
```python
def estimate_effect(ctx: RunContext[SelericDeps], evidence_ids: list[str], treatment: str, outcome: str, method: str = "backdoor.linear_regression") -> ToolResult: ...
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

## Proposed Amendment A1 — preconditions and escalation

Status: **PROPOSED — pending sign-off** (filed 2026-09-18 by Profile C after
a design review against source). The freeze text above is unchanged and
remains authoritative until all three profiles sign off. Rationale in
`03_PROFILE_CAPABILITIES.md` §3 and §6.

### Why this is needed at all

Non-negotiable rules 4 (tools never call tools) and 5 (analytics don't
fetch) together mean an analytics or causal tool can neither obtain evidence
nor ask for it. The **caller** therefore chooses the grain, the window and
the retry. Today that caller is deterministic code; after the refactor it is
the LLM. The contract froze the tool signatures without the input
constraints that made the current behavior correct, so three documented
fixes (`docs/BUG_SHEET.md` #6, #8-adjacent, #14) currently port forward as
conventions rather than guarantees.

The corollary, proposed as a standing rule: **if the caller picks the
inputs, the tool must validate them.** Control state becomes typed
preconditions the tool enforces, not prose in a tool description.

### A1.1 — Causal escalation surface (blocks Profile C Sprint 2)

The frozen Causal toolset is exactly `estimate_effect` + `refute_estimate`:
no hypothesis discovery, no candidate/history widening parameter, nothing
stateful. Bug #6's shipped fix is stateful escalation — `remediation_round`
on the long-lived mission object, widening history
(`_CAUSAL_EXTRA_HISTORY_DAYS * (1 + round)`) and ancestor candidates
(`cap + round`) on each retry. Per bug #7's entry that widening is also the
**only documented mitigation** for #7's intermittent zero-observation-rows.

As frozen, that fix has nowhere to live. Proposed: make the escalation an
explicit typed input rather than hidden state, so "search wider" is a schema
position the agent selects, not a counter the tool secretly keeps.

```python
def estimate_effect(
    ctx: RunContext[SelericDeps],
    evidence_ids: list[str],
    treatment: str,
    outcome: str,
    method: str = "backdoor.linear_regression",
    search_breadth: Literal[0, 1, 2] = 0,   # NEW — bounded escalation ladder
) -> ToolResult: ...
```

`search_breadth` maps to the existing widening arithmetic (0 = today's base
history/candidate cap, each step widens as the current `remediation_round`
does). Bounded at 2 so rule 11 still holds without a counter living in the
tool. Open for sign-off: whether a `discover_hypotheses()` function is also
needed, or whether hypothesis discovery stays inside `estimate_effect`.

### A1.2 — Analytics grain precondition

`EvidenceArtifact.grain` is already a typed `Literal` in §3, so the tool can
read grain off every `evidence_id` it is handed. Proposed contract rule,
binding on `compare_periods()` and `detect_anomalies()`:

> Every `EvidenceArtifact` in a single call must share one `grain`, and the
> observation's period span must match the baseline's period span. On
> violation the tool returns `success=False` with
> `error_code="EVIDENCE_GRAIN_MISMATCH"` and empty `artifact_ids`. It never
> normalizes a multi-period aggregate to make it comparable, and never
> compares across grains silently.

Boundary arithmetic is an implementation detail; the guarantee is not. This
is what makes bug #14's fix survive the loss of the
`classifier → mission.context → observer` thread. The old sum/normalize
fallback in `swarm/specialists/anomaly.py` is explicitly not carried
forward — it was the band-aid the real fix replaced.

### A1.3 — Precondition refusal semantics (policy gates)

Eight `policy(blackboard, mission) -> bool` gates and five
`config/*_policies.yaml` files currently encode when an analysis is
appropriate (minimum history, evidence sufficiency, intent match, retention
thresholds). No profile owns them and nothing in the frozen contract
replaces them. Bug #8's entry records them working correctly in the trace
where everything else failed.

Proposed: gate conditions become tool preconditions, and a tool that
declines on policy grounds returns `success=False` with
`error_code="INSUFFICIENT_EVIDENCE"` (rule 16's existing code) plus a
`warnings` entry naming the unmet condition. A declined call is a normal,
non-retryable outcome, not an error the agent should work around by calling
a different tool. Disposition of the five YAML files (migrate values into
toolset config, or fold into `ExecutionLimits`) to be decided in Sprint 2.

### A1.4 — Evidence-classification migration mapping

§3's `CausalArtifact.evidence_classification` froze `CAUSALLY_SUPPORTED`.
The code in `src/` uses `CAUSALLY_SUPPORTED_UNDER_ASSUMPTIONS` as a bare
string literal, and `config/diagnostic_policies.yaml:24` uses it as a config
**value** (`retain_at_or_above`). This is a cross-file plus cross-YAML
migration with no owner today.

The rename is defensible rather than a loss of precision: the frozen
`causally_supported_requires_refutation` validator turns the "under
assumptions" caveat into an *enforced refutation requirement*, which is
strictly stronger than a string suffix. Proposed mapping, to be applied as
one reviewed change:

| Current value / site | New value |
|---|---|
| `"CAUSALLY_SUPPORTED_UNDER_ASSUMPTIONS"` — `causal/estimator.py:135` | `CAUSALLY_SUPPORTED` (+ populated `refutation_checks`) |
| `agents/diagnostic/contracts.py:43`, `policies.py:19` | `CAUSALLY_SUPPORTED` |
| `agents/diagnostic/policies.py:85` (default for `retain_at_or_above`) | `CAUSALLY_SUPPORTED` |
| `agents/skeptic/contracts.py:74`, `registries.py:519` | `CAUSALLY_SUPPORTED` |
| `services/dowhy_causal.py:37,100` | `CAUSALLY_SUPPORTED` |
| `config/diagnostic_policies.yaml:24` (`retain_at_or_above`) | `CAUSALLY_SUPPORTED` — **config migration, owner needed** |

`ASSOCIATION_ONLY` (seen in bug #6's trace) maps to `ASSOCIATION`.

### A1.5 — Signature reconciliation (documentation-only, no shape change)

The profile briefs listed function names that §4 never froze. Recorded here
so the disagreement doesn't persist across two documents; §4 stands as-is
and the extra names are struck unless separately re-proposed:

| Toolset | Frozen in §4 | Struck from the brief |
|---|---|---|
| Analytics | 6 functions | `rolling_statistics`, `growth_rate`, `change_point`, `association_analysis` (`robust_zscore`/`seasonal_anomaly` fold into `detect_anomalies(method=)`) |
| Model | 3 functions | `simulate`, `predict_reverse_risk`, `predict_demand` |
| Experiment | 3 functions | `design_experiment`, `compare_variants`, `recommend_next_test` |

### A1.6 — Unblocked dependency

`pydantic-ai` is not in `pyproject.toml` and has no usage in `src/`. §4's
signatures are typed against `RunContext[SelericDeps]`. Sprint 0 validated
these shapes against the spec, not against an installed framework — a
Sprint 1 spike must install it and confirm the toolset/`RunContext` API
matches before any of §4 is treated as implementable-as-written.

## Change log

- 2026-09-18: initial freeze, Sprint 0.
- 2026-09-18: **Amendment A1 proposed** (not applied) — precondition and
  escalation semantics for Analytics/Causal, policy-gate refusal codes,
  evidence-classification migration mapping, brief/contract signature
  reconciliation, `pydantic-ai` dependency spike. Filed by Profile C,
  pending A and B sign-off. Frozen text above unchanged.
