# Profile C — Capabilities & Intelligence

Owns turning the current domain agents + intelligence specialists into
stateless toolsets the central agent calls directly. This is the largest
behavioral-risk profile — it's where actual analytical/causal/predictive
logic lives, not just plumbing.

## Mission

Replace `DomainAgent.observe()` (performance/attribution/commerce/product/
customer/operations/finance agents) and the five intelligence specialists
(`ObserverAgent`, `AnomalyAgent`, `SwarmDiagnosticSpecialist`,
`SwarmPredictionSpecialist`, `SwarmStrategySpecialist`,
`SwarmSkepticSpecialist`) with deterministic tool functions the central
agent calls when it decides to, instead of agents that decide for
themselves when to run.

## Retires

- `swarm/domain/*` — all 8 domain agents (`performance_agent`,
  `attribution_agent`, `commerce_agent`, `product_agent`, `customer_agent`,
  `operations_agent`, `finance_agent`, `funnel_agent` [already disabled]).
  Domain *boundaries* survive as Cube semantic views/domains (spec §11);
  domain *agents* don't.
- `swarm/specialists/observer.py::ObserverAgent` →
  `AnalyticsToolset.compare_periods()`/`summarize_metric_state()`.
- `swarm/specialists/anomaly.py::AnomalyAgent` →
  `AnalyticsToolset.detect_anomalies()`. Keep the exact detection methods
  already implemented (robust z-score, MAD) — this is a wrapping change,
  not a re-derivation of the math. The per-day-granularity fix from bug #14
  (`docs/TASK_SHEET.md` Phase 3/4) must carry over exactly: the tool takes
  already-fetched per-day evidence (from Profile B's `query_metrics`), never
  re-derives its own window.
- `agents/diagnostic/*` (`SwarmDiagnosticSpecialist`, causal_discovery.py,
  DoWhy/EconML wiring, `CausalGraphRegistry`) → `CausalToolset` +
  `causal/service.py`. Keep DoWhy/EconML themselves; retire the
  agent-decides-when-to-run wrapper and the standalone graph registry
  (fold into curated causal knowledge per spec §17, still versioned).
- `agents/prediction/*` (`SwarmPredictionSpecialist`) → `ModelToolset` +
  `models/service.py`. `ModelRegistry` — already confirmed NOT duplicated
  (46_ARCHITECTURE_CONSOLIDATION_PLAN.md Item 3, retracted) — keep as-is,
  just called from the new `ModelService` instead of the specialist.
- `agents/strategy/*` (`SwarmStrategySpecialist`) → folded into the central
  agent's own reasoning (spec §16/§21: diagnosis and strategy are dynamic
  investigation, not a separate agent) + `ExperimentToolset` for anything
  that needs deterministic sample-size/design math.
- `agents/skeptic/*` (`SwarmSkepticSpecialist`) → the causal *challenge*
  logic (contradiction-finding, trust scoring) becomes part of
  `agent/validation.py::EvidenceValidator` (owned jointly with Profile A —
  A owns the validator's orchestration slot, C owns what it actually
  checks, since that requires the same causal/evidence-classification
  domain knowledge as the rest of this profile). `agents/skeptic/scoring/
  trust_score.py` and `verdict_engine.py`'s logic ports over; the
  documented "STRONG trust + REVISE" behavior (`docs/BUG_SHEET.md` #12)
  must reproduce identically — same two independent signals (trust vs.
  verdict), not merged into one score.
- `coordinator/governance/remediation.py` — the "targeted remediation"
  round-kind routing that `docs/BUG_SHEET.md` #6 found was partially
  decorative (context dropped on `ctx.activate()`). Don't port the broken
  routing; port the part that demonstrably worked (widening history/
  candidates per remediation round) as the `EvidenceValidator`'s bounded
  retry (`max_validation_revisions = 1` — note this caps remediation at
  *one* round in the new design, tighter than the old multi-round loop;
  confirm that's an acceptable behavior change, not an oversight).

## Builds

- `toolsets/analytics.py` + `analytics/*` — `compare_periods`,
  `rolling_statistics`, `growth_rate`, `robust_zscore`,
  `seasonal_anomaly`, `change_point`, `contribution_analysis`,
  `segment_decomposition`, `funnel_decomposition`, `cohort_analysis`,
  `association_analysis` (spec §13). Pure functions over evidence Profile B
  already fetched — no data access inside this module.
- `toolsets/causal.py` + `causal/*` — `CausalQuery` in,
  `CausalArtifact` out, DoWhy/EconML underneath. Every result carries an
  evidence classification (OBSERVATION/ASSOCIATION/HYPOTHESIS/
  CAUSALLY_SUPPORTED/EXPERIMENTALLY_VALIDATED — spec §18, non-negotiable
  rule 9).
- `toolsets/models.py` + `models/*` — `forecast()`, `simulate()`,
  `predict_reverse_risk()`, `predict_ltv()`, `predict_propensity()`,
  `predict_demand()`, plus `models/evaluation.py`'s prediction→actual
  feedback loop (spec §20) — new capability, not in the current system
  today; confirm priority/sequencing with the user, it's additive scope.
- `toolsets/knowledge.py` + `knowledge/*` — retrieval over
  incident/SOP/model-card/experiment-note documents. Schema-enforce it
  cannot return a bare numeric metric value (see overview §9 suggestion 3).
- `toolsets/experiments.py` + `experiments/*` — `get_experiment_history`,
  `design_experiment`, `estimate_sample_size`, `evaluate_experiment`,
  `compare_variants`, `recommend_next_test` — new capability, same
  additive-scope note as Models above.

## Depends on

- Profile A's `SelericDeps`/`ToolResult`/artifact schemas (frozen Sprint 0).
- Profile B's `SemanticToolset.query_metrics()`/`drilldown()` for evidence
  — Analytics/Causal tools consume evidence, they don't fetch it
  themselves (non-negotiable rule 5).

## Key risks

- This is where the most actual business logic lives (causal graphs, trust
  scoring, anomaly thresholds) — highest regression risk of the three
  profiles. Every retired module has a documented bug history
  (`docs/BUG_SHEET.md` #6, #7, #8, #12, #13, #14) that must not silently
  reappear. Treat every one of those bug entries as a named regression test
  before this profile's exit gate, not just "port the code and hope."
- `docs/BUG_SHEET.md` #13 (`test_diagnostic_bridge_is_idempotent` fails on
  template/scenario-based causal runs) is open and unfixed today — decide
  explicitly whether to fix it before porting or port the same known gap
  forward with a tracked ticket. Don't let it silently vanish because the
  containing module was rewritten.
- Models/Knowledge/Experiments toolsets are **new capability surface**, not
  1:1 ports — they should not block Profile A/B's cutover gate; sequence
  them after Analytics/Causal/Validator parity is proven (see sprint plan).

## Exit criteria

1. Every named regression in `docs/BUG_SHEET.md` (#2, #6, #7, #8, #12, #14 —
   the ones whose root cause lived in a module this profile retires) has an
   explicit passing test against the new toolset code, not just an assumed
   carry-over.
2. `docs/BUG_SHEET.md` #13 has an explicit disposition (fixed, or ported
   forward with a tracked follow-up) — not silently dropped.
3. EvidenceValidator reproduces the "STRONG trust + REVISE" case (#12) with
   the same two-signal structure.
4. Causal/prediction outputs carry evidence classification / model version
   metadata on 100% of `CausalArtifact`/`PredictionArtifact` instances in
   the eval set (automatable check, not a spot check).
