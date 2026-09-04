# 03 - Validation Pipelines

Every validator returns a `ValidatorOutcome`
(`validator`, `status`, `challenges`, `evidence_gaps`, `methodological_issues`,
`followups`, `detail`, `score_signals`). `status` is one of
`OK | WEAK | REJECTED | UNAVAILABLE | NOT_APPLICABLE | INSUFFICIENT`.

## Core validators (always run)

### evidence (`validators/evidence_validator.py`)
- material claim (numeric/comparison/causal/forecast/recommendation/action) with
  **no resolvable evidence and no typed artifact** -> `REJECTED` (blocking). The
  LLM cannot override this.
- dangling support refs, stale evidence (`> max_freshness_hours`), sample size
  below `min_sample_size`, `quality_flags` in {INVALID, PARTIAL, SUSPECT,
  LATE_DATA} -> warnings.
- causal claim with no `CausalAnalysisArtifact` / forecast claim with no
  `ForecastArtifact` -> blocking `EvidenceGap`.

### provenance (`validators/provenance_validator.py`)
- missing `source` -> blocking; missing query/tool hash, calculation/metric
  version, retrieval timestamp -> warnings (per claim type via policy).

### metric (`validators/metric_validator.py`)
- compares evidence rows for the same `metric_id`. A difference in
  `calculation_version` / `source_version` / unit / timezone / attribution /
  gross-net / returns treatment / grain / cohort is classified
  **`metric_semantic_conflict`** - a warning + a
  `metric_definition_reconciliation` follow-up, **never a factual contradiction**.

### contradiction (`validators/contradiction_validator.py`)
- same metric, **same dimensions**, same window, same definition, values differ
  > 5% -> `factual_conflict` (blocking).
- same as above but different `source` -> `source_conflict` (warning) +
  `cross_source_reconciliation` follow-up.
- different window -> `time_range_conflict` (info). Different definition is left
  to the metric validator. Different dimension slices are **not** contradictions.
- any `claim.contradiction_refs` -> blocking.

## Type-specific validators

| claim_type | validators (router) |
| --- | --- |
| numeric / comparison | statistical (skipped at R0/R1) |
| anomaly | anomaly, statistical |
| correlation | statistical, causal |
| causal | causal, statistical |
| forecast | model, forecast |
| recommendation / action | strategy |

Plus: any attached artifact pulls in its validator regardless of declared type.

### statistical (`validators/statistics_validator.py`)
Delegates every test (`sample_size`, `effect_size`,
`confidence_interval_excludes_zero`, `segment_robustness`) to the injected
`StatisticalValidatorService`. The Skeptic (and the LLM) never compute a p-value.

### anomaly (`validators/anomaly_validator.py`)
Outputs `SUPPORTED_ANOMALY | WEAK_ANOMALY | NOT_ENOUGH_DATA |
INVALID_DETECTOR_FOR_CONTEXT | REJECTED_ANOMALY`. Insufficient history/sample ->
`NOT_ENOUGH_DATA` -> WEAK + gap.

### causal - see `04_CAUSAL_VALIDATION.md`
### model / forecast - see `05_MODEL_FORECAST_VALIDATION.md`
### strategy - see `06_STRATEGY_VALIDATION.md`

## Alternatives, stress, gaps, trust, verdict

- `hypothesis/alternative_generator.py` - constrained candidates from the
  incident registry, causal-graph common causes and `claim.metadata`; optional
  LLM phrasing; capped at `max_alternative_hypotheses`; each carries a
  falsification test.
- `hypothesis/falsification.py` - "if true / if false / tests" implications.
- `stress/counterfactual.py` - forecast base/optimistic/pessimistic and strategy
  diagnosis-correct/partial/incorrect scenarios. Missing values -> `EvidenceGap`,
  never invented numbers.
- `stress/sensitivity.py` - deterministic robustness proxy for causal effects.
- `evidence_gaps.py` - de-dupe + `Priority ~ EIG * (0.5+impact) / cost`.
- `scoring/trust_score.py` - weighted per-type profile over validator
  `score_signals`; a blocking failure caps trust at 0.3; LLM confidence is never
  read.
- `scoring/verdict_engine.py` - see `03` mapping below.

## Verdict mapping

- **REJECT** - any `REJECTED` validator or any blocking `Challenge`.
- **REVISE** - a blocking `EvidenceGap`; an unresolved alternative (priority>=6);
  an unreconciled metric/source/contradiction/anomaly/model/forecast/causal
  warning; a validator `UNAVAILABLE`/`INSUFFICIENT`; `trust_score` below
  `trust.verdict_thresholds.revise_below`.
- **PASS** - none of the above.
