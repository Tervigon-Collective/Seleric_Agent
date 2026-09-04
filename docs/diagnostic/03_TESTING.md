# 03 - Hypothesis Testing

Six deterministic runners (`testing/runners.py`). No LLM. A runner returns a
`TestResult` with `passed` and, if it could not evaluate, `detail["skipped"]`
(skipped tests never fail a hypothesis).

| kind | checks | fails when |
| --- | --- | --- |
| `evidence_sufficiency` | direct evidence / a preceding event for the treatment metric | no evidence and no supporting refs |
| `temporal_precedence` **(hard gate)** | a treatment-change timestamp is at or before the degradation start (+ `temporal_tolerance_minutes`) | every treatment change is strictly after the outcome change |
| `segment_specificity` | the outcome drop is concentrated in the segment the mechanism predicts (e.g. "mobile") | drop is uniform across segments, or the predicted segment is not the worst |
| `control_divergence` | an unaffected control segment (e.g. `device=desktop` / `segment=control`) moved much less | control moved ≥ half as much as the affected segment (common-shock signal) |
| `dose_response` | units with worse treatment show worse outcome (`request.context["dose_pairs"]`) | no monotone relationship |
| `mechanism_consistency` | both treatment and outcome moved materially (≥5% / ≥3%) | movement too small to support the mechanism |

## Hard gates

`diagnostic_policies.yaml -> testing.hard_gates` (default: `temporal_precedence`).
A failed hard-gate test rejects the hypothesis outright in `classify_hypothesis`,
regardless of any other signal - `rejection_reason` starts with `"hard gate"`.

## Scoring

```
score = passed / (tests that were actually evaluated)     # skipped tests excluded
```

- `score >= 0.5` (or `is_primary`) -> status `testing`, eligible for the causal step
- `score < 0.5` and not primary -> `rejected`

## Planning (`testing/planner.py`)

The test set depends only on shape + data availability:
segment/control tests are added only when the outcome has segmented evidence;
`dose_response` only when `dose_pairs` (or ≥2 treatment rows) exist. Capped at
`budgets.max_tests_per_hypothesis`.
