# Profile C — Capabilities & Intelligence

Owns turning the current domain agents + intelligence specialists into
stateless toolsets the central agent calls directly. This is the largest
behavioral-risk profile — it's where actual analytical/causal/predictive
logic lives, not just plumbing.

Revised 2026-09-18 after a design review against source (see `TASK_SHEET.md`
Log). The revision changed three things materially: the retire-set paths
were wrong in several places, the exit criteria were not satisfiable as
written, and the profile had no home for the deterministic control state
that rules 4+5 push onto the caller. That last one is §3 below and is the
most important part of this brief.

## Mission

Replace `DomainAgent.observe()` (performance/attribution/commerce/product/
customer/operations/finance/funnel agents) and the four specialist bridges
plus two swarm specialists (`ObserverAgent`, `AnomalyAgent`,
`SwarmDiagnosticSpecialist`, `SwarmPredictionSpecialist`,
`SwarmStrategySpecialist`, `SwarmSkepticSpecialist`) with deterministic tool
functions the central agent calls when it decides to, instead of agents that
decide for themselves when to run.

## 1. What the bug history says this refactor is actually for

Worth stating, because it's the justification for the whole profile: the
documented failures in `docs/BUG_SHEET.md` are *coordination* bugs, not
analysis bugs.

- #6 — remediation round-kind routing was decorative; context dropped at
  `ctx.activate()`.
- #8 — `dimensions_in_query()` token-overlap heuristic attached a dimension
  the metric can't slice by.
- #14 — `ObserverAgent` fetched a window sum, `anomaly.py` compared it to a
  single-day baseline.

In each case the math was fine and the machinery deciding *when* and *with
what* to run it was not. Retiring the machinery and keeping the math is the
correct diagnosis. §3 is about not recreating the same class of bug in the
new machinery.

## 2. Retires

Paths verified against source 2026-09-18. Note the retire-set spans **two
trees** (`swarm/` and `agents/`) — the earlier draft of this brief had
several of these in the wrong place.

- `swarm/domain/` — `base.py` + `configs.py`. This is **one config-driven
  base class**, not 8 agent implementations; `agents/domains/*.py` are
  ~730 B config stubs (`performance.py`, `attribution.py`, `commerce.py`,
  `product.py`, `customer.py`, `operations.py`, `finance.py`, `funnel.py`).
  Correction: `funnel_agent` is **not** disabled —
  `config/agent_registry.yaml:47` has `enabled: true`. It retires with the
  rest, it isn't already gone. Domain *boundaries* survive as Cube semantic
  views/domains (spec §11); domain *agents* don't.
- `swarm/specialists/observer.py::ObserverAgent` →
  `AnalyticsToolset.compare_periods()`. This is the observer carrying bug
  #14's `_daily_windows` fix (`observer.py:42`).
- `agents/intelligence/observer.py` (25 KB, a **second** observer) — its
  `_query_windows` is Profile B's to delete (Sprint 2); the rest needs an
  explicit disposition from C. Do not assume "the observer" means one
  module.
- `swarm/specialists/anomaly.py::AnomalyAgent` →
  `AnalyticsToolset.detect_anomalies()`. The detection math it uses lives in
  `services/business_state/detectors.py::robust_zscore` — `anomaly.py` sets
  `force_robust_zscore` and delegates. Keep the existing implementation
  (robust z-score, MAD): this is a wrapping change, not a re-derivation.
  Bug #14's guarantee must carry over as an **enforced precondition**, not a
  calling convention — see §3.
- `agents/diagnostic/*` (`swarm_bridge.py::SwarmDiagnosticSpecialist`,
  `causal_discovery.py`, `causal_graph_builder.py`, DoWhy wiring) →
  `CausalToolset` + `causal/service.py`. Keep DoWhy. **Correction: EconML is
  not a dependency of this repo** — not in `pyproject.toml`, zero imports in
  `src/`/`tests/`. Earlier drafts said "keep DoWhy/EconML"; there is no
  EconML to keep. Adding it is new scope with a heavy transitive stack and
  needs its own decision, not a port line.
- `CausalGraphRegistry` — a `Protocol` in `agents/skeptic/registries.py:225`,
  alongside `ModelRegistry` (:277) and `MetricSemanticsRegistry` (:140).
  Retiring it means untangling a protocol module three subsystems import,
  not deleting a registry file. Curated causal knowledge per spec §17, still
  versioned.
- `agents/prediction/*` (`swarm_bridge.py::SwarmPredictionSpecialist`) →
  `ModelToolset` + `models/service.py`. **Status finding that closes Sprint
  3's open question:** `swarm_bridge.py:61-62` builds an
  `InMemoryModelRegistry` from `scenario["forecast_truth"]` in
  `_fixture_deps()`, used whenever `self._deps` is None. The YAML path
  resolves `config/model_registry.yaml`, which **does not exist** — only
  `config/model_registry.example.yaml` (573 B, both entries
  `status: candidate`). There are no approved production models. ModelToolset
  is therefore greenfield on a fixture-driven template service, not a port,
  and `models/evaluation.py`'s prediction→actual loop has nothing to
  evaluate until real models land.
- `agents/strategy/*` (`swarm_bridge.py::SwarmStrategySpecialist`) → folded
  into the central agent's own reasoning (spec §16/§21) + `ExperimentToolset`
  for deterministic sample-size/design math. See §4 — this is a move toward
  LLM judgment and needs to be a decision, not a default.
- `agents/skeptic/*` (`swarm_bridge.py::SwarmSkepticSpecialist`) — the causal
  *challenge* logic (contradiction-finding, trust scoring) becomes part of
  `agent/validation.py::EvidenceValidator` (A owns the orchestration slot, C
  owns what it checks). `scoring/trust_score.py` and `scoring/verdict_engine.py`
  port over; the two-signal structure must survive (§4).
- `coordinator/governance/remediation.py` — the round-kind routing bug #6
  found was decorative. Don't port the broken routing; port the escalating
  widening that demonstrably worked. §3.2 covers where it goes, because the
  frozen contract currently has nowhere to put it.

## 3. The design problem this profile has to solve first

Non-negotiable rule 5 says analytics tools calculate but don't fetch. Rule 4
says tools never call other tools. Together they mean an analytics tool can
neither obtain evidence nor ask for it: **the caller chooses the grain, the
window and the retry**. Today that caller is deterministic code. After the
refactor it is the LLM. The regression requirements below assume it is still
deterministic, so unless something replaces the mechanism, they do not hold.

These rules are good rules — they're why analytics can't quietly re-fetch
and fabricate a baseline. They just have a corollary nobody wrote down:
**if the caller picks the inputs, the tool must validate them.** Control
state becomes typed preconditions the tool enforces, not prose the agent is
trusted to follow.

### 3.1 Grain (bug #14)

The mechanism that makes #14's fix hold today is a typed field threaded
through state:

```
llm_classifier.py:57    granularity: Literal["day","week","month","none"]   <- classified once, typed
intake/__init__.py:411  granularity=llm_result.granularity
graph.py:1194           mission.context["granularity"]
observer.py:42          _daily_windows(tr) if context["granularity"] == "day"
anomaly.py:44           per-evidence loop -> like-for-like comparison
```

One classification, consumed by two modules that cannot disagree. In the new
design there is no thread — the agent calls `query_metrics`, then calls
`detect_anomalies` with whatever came back, and the grain relationship
between them is a free-running choice on every mission. #14 would return as
an intermittent, not as a test failure.

**Resolution (cheap, because Sprint 0 already froze the field):**
`EvidenceArtifact.grain` is a typed `Literal["day","week","month","none"]`
(`CONTRACTS.md` §3). The tool can read grain off every `evidence_id` it is
handed and refuse a bad set. Contract to enforce:

> Every `EvidenceArtifact` passed to one `detect_anomalies()` or
> `compare_periods()` call must share a single `grain`, and the observation's
> period span must match the baseline's period span. Violation returns
> `success=False` with `error_code="EVIDENCE_GRAIN_MISMATCH"` — never a
> normalized fudge, never a silent comparison.

Exact boundary arithmetic is an implementation detail. The point is the
guarantee is non-optional and lives in the tool, where the LLM cannot route
around it. The old sum/normalize fallback branch in `anomaly.py` does **not**
port — it was the band-aid #14's real fix replaced.

### 3.2 Escalation state (bugs #6 and #7)

Bug #6's fix is stateful escalation: `remediation_round` written onto the
long-lived mission object, read by the diagnostic bridge to widen history
(`_CAUSAL_EXTRA_HISTORY_DAYS * (1 + round)`) and candidates
(`cap + round`). Per #7's own entry, that widening **is** the documented
mitigation for #7's intermittent zero-observation-rows: *"a retry after a
thin first attempt now has a better chance of finding enough data."*

A stateless tool has nowhere to put that counter. And the frozen Causal
surface (`CONTRACTS.md` §4) is exactly two functions — `estimate_effect`,
`refute_estimate` — with no hypothesis discovery, no candidate/history
widening parameter and nothing stateful. **Bug #6's fix has no home in the
contract as frozen.** This is the lead item of the amendment in §6.

Compounding it: `ExecutionLimits.max_validation_revisions = 1` is frozen.
One revision means one widening step and no escalation ladder. The old
system also had a stall-detector that decided when further rounds were
pointless — #6's entry credits it as the part that worked. A hard cap of 1
is not a stall-detector; it fires before the detector would have anything to
detect. Decide this explicitly (Sprint 2, task in `SPRINT_PLAN.md`), don't
inherit it by default.

### 3.3 Policy gates

Eight `policy(blackboard, mission) -> bool` implementations
(`swarm/specialists/{base,anomaly,prediction,skeptic}.py`,
`agents/{diagnostic,prediction,skeptic,strategy}/swarm_bridge.py`) plus five
`config/*_policies.yaml` files (`coordinator`, `diagnostic`, `prediction`,
`skeptic`, `strategy`) encode *when an analysis is appropriate* — minimum
history, evidence sufficiency, intent match, retention thresholds. No
profile currently owns them and no retire-line mentions them.

They are not incidental plumbing. Bug #8's entry is explicit: when the
misclassification emptied the evidence, *"every downstream specialist
correctly skipped via policy gates for lack of data."* In the trace where
everything else failed, the gates worked.

Converting specialists to tools converts executable policy into tool
*descriptions* — prose the model may or may not honor. Same resolution as
§3.1: gates become tool preconditions returning a structured refusal
(`error_code="INSUFFICIENT_EVIDENCE"`, already in the `ToolResult` contract
as rule-16's code). C owns porting the gate conditions; the five YAML files
need an explicit disposition (migrate values, or fold into `ExecutionLimits`
/ toolset config) — tracked in `SPRINT_PLAN.md` Sprint 2.

## 4. Skeptic → validator is a change in kind, not a consolidation

Today the skeptic is a separate agent: separate context, separate prompt,
separate model invocation, adversarial to the diagnostic agent's output.
That independence is what makes the two-signal structure meaningful —
`score_trust` and `decide_verdict` are independent functions looking at
different things (`docs/BUG_SHEET.md` #12).

Folding strategy into "the central agent's own reasoning" and skeptic into
`EvidenceValidator` leaves the agent's output checked by a component in the
same loop, informed by the same context that produced the claim. In-context
self-review is a weaker check than cross-agent challenge. That may be an
acceptable trade — it is not a simplification, and it should be recorded as
a decision with the downgrade named.

Open question the bounded loop creates: what does the loop do with
STRONG-trust + REVISE when it gets exactly one revision? Answer it alongside
§3.2's cap decision.

## 5. Builds — reconciled against the frozen contract

`CONTRACTS.md` §4 is authoritative; the earlier draft of this brief listed
function names that were never frozen. Dispositions:

- **Done (Sprint 1) — 2 of 6.** `toolsets/analytics.py` + `analytics/*`:
  `compare_periods`, `detect_anomalies` (with the A1.2 grain precondition).
  Folded into `detect_anomalies(method=...)`: `robust_zscore` (implemented),
  `seasonal_anomaly` (frozen but no implementation — returns
  `METHOD_NOT_AVAILABLE`, A1.7). **Struck, re-propose if wanted:**
  `rolling_statistics`, `growth_rate`, `change_point`, `association_analysis`.
- **Not started (Sprint 4) — 4 of 6, greenfield.** `contribution_analysis`,
  `segment_decomposition`, `funnel_decomposition`, `cohort_analysis` have no
  implementation anywhere in `src/` — **Analytics is mostly greenfield, not
  a port**, which the earlier draft flagged only for Models/Knowledge/
  Experiments. Sequenced in `SPRINT_PLAN.md` Sprint 4.
- **Done (Sprint 2).** `toolsets/causal.py` + `causal/*` — `estimate_effect` +
  `refute_estimate`. `CausalQuery` in, `CausalArtifact` out, DoWhy
  underneath. Every result carries an evidence classification (rule 9), which
  the frozen `CausalArtifact` already makes a required field with no default.
- **Done (Sprint 3) — full build, not a stub.** `toolsets/models.py` +
  `models/*` — `forecast` (real Holt/exponential-smoothing forecaster via
  `statsmodels`), `predict_ltv`/`predict_propensity` (both correctly refuse
  with `INSUFFICIENT_EVIDENCE` + `policy:no_approved_model` — no per-customer
  labels or feature store exist in this deployment, so an approved entry
  would be the fabrication, not the refusal). `models/evaluation.py`
  measures forecast accuracy but does not yet feed back into registry
  promotion — `last_validated_at` is still hand-set (`00_OVERVIEW.md` §8
  flags this as needing a named owner). **Struck, re-propose if wanted:**
  `simulate`, `predict_reverse_risk`, `predict_demand`.
- **Not started (Sprint 4).** `toolsets/knowledge.py` + `knowledge/*` —
  frozen surface is `search_knowledge`. Schema-enforced that it cannot
  return a bare numeric metric value (overview §9 suggestion 3).
- **Not started (Sprint 4).** `toolsets/experiments.py` + `experiments/*` —
  frozen surface is `get_experiment_history`, `estimate_sample_size`,
  `evaluate_experiment`. **Struck, re-propose if wanted:**
  `design_experiment`, `compare_variants`, `recommend_next_test`. No
  experiment infrastructure exists in `src/` today.

## 6. Contract amendment A1 — ACCEPTED 2026-09-18

Full text in `CONTRACTS.md` under "Amendment A1". Applied into frozen §2
(error codes) and §4 (`search_breadth` on `estimate_effect`; Analytics grain
rule). Summary of what landed for C:

1. **Causal escalation surface** — `search_breadth: Literal[0,1,2]=0` on
   `estimate_effect`. Unblocks C's Sprint 2. No separate
   `discover_hypotheses()`.
2. **Analytics grain precondition + `EVIDENCE_GRAIN_MISMATCH`** — already
   implemented in `analytics/grain.py` (Sprint 1); now contract-authoritative.
3. **Precondition/refusal semantics for policy gates** — declines return
   `INSUFFICIENT_EVIDENCE` + named warning; YAML files migrate in Sprint 2 C.
4. **Evidence-classification migration** — owner = Profile C; run during
   Causal Sprint 2 (`CAUSALLY_SUPPORTED_UNDER_ASSUMPTIONS` →
   `CAUSALLY_SUPPORTED` across `src/` + `config/diagnostic_policies.yaml:24`).

Joint decisions recorded with acceptance: keep
`max_validation_revisions = 1` (causal ladder is `search_breadth`); skeptic →
validator is a change in kind (in-context self-review).

## 7. Depends on

- Profile A's `SelericDeps`/`ToolResult`/artifact schemas (frozen
  `CONTRACTS.md`, 2026-09-18) — and **A1 ACCEPTED** (same day).
- Profile B's `SemanticToolset.query_metrics()`/`drilldown()` for evidence —
  Analytics/Causal consume evidence, they don't fetch it (rule 5). B is also
  the writer of `EvidenceArtifact.grain`, which §3.1's precondition depends
  on being set correctly at the source.
- **`pydantic-ai-slim>=2.45`** is in `pyproject.toml` (A1.6 closed). Stub
  agent validates `RunContext[SelericDeps]` against the installed framework.

## 8. Key risks

- Highest regression risk of the three profiles — this is where the actual
  business logic lives (causal graphs, trust scoring, anomaly thresholds).
  §3's preconditions are the mitigation; without them the risk is unmitigated.
- **Fixture-validated ports in a codebase with standing evidence that
  fixtures diverge from reality.** C's Sprint 1/2 validate against fixtures,
  and bug #13 *is* the fixture path and the real path silently disagreeing
  (`TemplateCausalEstimationService` never populates `observations`, so
  scenario runs produce 0 hypotheses while the DoWhy path works). The port's
  validation strategy has the same failure mode as the open bug it's meant
  to disposition. **Resolved (Sprint 3):** #13 root-caused to commit
  `79e88cc`'s `fallback_to_unfiltered_candidates` block and confirmed fixed
  by 20 consecutive live passes — the risk materialized as this one named
  bug and didn't recur elsewhere in the port.
- Models/Knowledge/Experiments and four of six Analytics functions are **new
  capability surface**, not 1:1 ports. They must not block Profile A/B's
  cutover gate; sequence after Analytics-port/Causal/Validator parity.

## 9. Exit criteria — all 8 met, Sprint 3 close 2026-09-19 (see `TASK_SHEET.md` for evidence)

Revised — the previous version demanded "an explicit passing test" for
`#2, #6, #7, #8, #12, #14`, which is not satisfiable: #7 is documented as
*"not a deterministic code defect… tied to real LLM classification
variance"*, #12 is filed under "Design tradeoffs discovered (not bugs)" and
titled `INVESTIGATED, NOT A BUG`, and #2/#8 root-cause in Profile B modules
(`lookup_fast_path.py`, `catalogue_grounding.py`) that C does not retire.
As written the gate would either block forever or be waived, and a waived
gate is worse than no gate. Split by what each bug actually is:

**a. C-owned deterministic regressions — named passing test required**
1. **Met.** Bug #6: escalating widening genuinely searches a larger space
   across retries — `test_search_breadth_widens_history_and_candidate_cap`,
   `test_estimate_effect_records_widened_caps_in_query`.
2. **Met.** Bug #14: per-day evidence reaches the detector un-normalized,
   **and** a grain-mismatched evidence set is rejected with
   `EVIDENCE_GRAIN_MISMATCH` rather than normalized (§3.1) —
   `tests/unit/test_analytics_toolset.py`.

**b. Cross-profile — C reviews and signs off, B owns the gate**
3. **Met.** Bugs #2 and #8 closed via Profile B's exit criteria 2 and 3
   (`02_PROFILE_SEMANTIC_MCP.md`) —
   `tests/unit/test_semantic_toolset_bug_regressions.py`.

**c. Behavioral assertions, not regression tests**
4. **Met.** Bug #7: confirmed still intermittent (LLM primary-metric
   variance), not newly deterministic; its mitigation (the #6 widening) is
   now caller-chosen `search_breadth`, independent of and surviving the
   `max_validation_revisions = 1` cap.
5. **Met.** Bug #12: `EvidenceValidator` reproduces STRONG-trust + REVISE
   with the same **two independent signals**, not merged —
   `tests/unit/test_v3_validation_signals.py` (19 passed, incl. a
   signature-inspection test that `score_trust` cannot see a verdict).
   Recorded loop behavior: REVISE consumes a revision and re-prompts,
   REJECT fails closed without consuming one, exhaustion mid-REVISE →
   `status="failed"`, `error_code="INSUFFICIENT_EVIDENCE"`.
6. **Met.** Bug #13: fixed, not just ported forward — root-caused to commit
   `79e88cc`'s `fallback_to_unfiltered_candidates` block; 20 consecutive
   live runs of `test_diagnostic_bridge_is_idempotent` passed, confirming
   it isn't #7's intermittent pattern.

**d. Behavioral parity — new, and the one that actually matters**
7. **Met.** The replay missions run through both the old specialists and
   the new toolsets produce equivalent findings —
   `evals/parity.py` + `tests/replay/test_v3_parity.py` (3 passed). Bar is
   structural equivalence (metric/direction/classification/hypothesis, per
   user decision), with `EXPECTED_DIVERGENCES` naming the intentional ones
   (e.g. `detect_anomalies` sourcing its baseline from evidence, not
   `BusinessStateService` — rule 5 working as designed) and a test proving
   the harness can actually fail on an *unexplained* divergence.

   This profile previously had no behavioral parity criterion at all, while
   the brief itself called it the highest-behavioral-risk profile. The old
   criterion 4 (below) is schema completeness: a required Pydantic field
   satisfies it trivially and proves nothing about correctness. It stays,
   as a cheap automatable floor, but it is not the parity gate.

**e. Schema floor (automatable)**
8. **Met by construction.** `evidence_classification`/`model_id`+`model_version`
   are required fields with no default on `CausalArtifact`/`PredictionArtifact`
   (`CONTRACTS.md` §3) — a mission cannot produce either artifact type
   without them; not separately re-verified against a live eval-set run
   this pass.

## 10. Recommended approach: extract in place, then wrap, then delete

The stated biggest risk is silent math drift during the port. Writing new
modules and proving parity later maximizes that risk, because "did the math
change?" becomes a review judgment across two file trees. A three-step
strangler inside each module is cheaper and mechanically checkable:

1. **Extract** the math out of `ObserverAgent`/`AnomalyAgent`/
   `causal_discovery.py` into pure functions **in place**, no behavior
   change. The existing suite staying green is the proof the math is
   unchanged — not a reviewer's eye.
2. **Wrap** those functions with the new toolset. The toolset is a thin
   adapter, so there is no second implementation to drift.
3. **Delete** the agent shell once the toolset is the only caller.

`robust_zscore` already lives this way (`services/business_state/detectors.py:32`,
called by `anomaly.py` rather than reimplemented in it). The pattern exists
in this codebase and works.

It also separates cleanly what this brief previously conflated: ports
(`compare_periods`, `detect_anomalies`, Causal, validator content checks)
from greenfield (the other four Analytics functions, Models, Knowledge,
Experiments). Steps 1–2 are near-zero-risk refactors that do not depend on
amendment A1 landing, so they can start immediately.
