# 02 - Hypothesis Model

## Ontology (`ontology.py`)

`outcome_metric -> tuple[MechanismTemplate]`, most specific first. Each template:

| field | meaning |
| --- | --- |
| `statement` | the hypothesis sentence |
| `mechanism` | one-sentence causal story |
| `treatment_metric` | the metric that plausibly drives the outcome |
| `outcome_metric` | what it drives |
| `domains` | owning business domain(s) |
| `evidence_hints` | metric/event keys whose presence counts as direct support |

Seeded outcomes: `metric.purchase_cvr` (7 mechanisms), `metric.cac` (4),
`metric.net_sales` (3). Add an outcome -> add a tuple, no code change.

## Generation (`hypotheses/generator.py`)

1. **Template hypotheses** - one per ontology entry for the outcome. Always
   present; `supporting_evidence` filled from `evidence_hints` matches.
2. **Caller alternatives** - `request.context["alternatives_to_test"]`.
3. **Constrained LLM** - only if `hypotheses.llm_enrichment` and under the cap.
   Each LLM candidate is kept **only if its `treatment_metric` is already in
   evidence or is a node in the outcome's causal graph**. Free-form causes are
   dropped (`test_llm_enrichment_is_bounded`).

De-duped by statement, capped at `budgets.max_hypotheses` (default 6).

## Prior ranking (`hypotheses/ranker.py`)

```
prior = w_evidence   * evidence_overlap       (support refs, +0.6 if the treatment metric itself moved anomalously)
      + w_incident   * incident_match         (overlap with IncidentRegistry patterns for the lead domain)
      + w_temporal   * temporal_alignment     (a treatment-event timestamp <= degradation start)
      + w_mechanism  * mechanism_specificity  (named treatment metric + a substantive mechanism sentence)
```

Weights in `diagnostic_policies.yaml -> hypotheses.prior_weights`. The
highest-prior hypothesis is marked `is_primary`. Prior only orders which
hypotheses get a causal estimate first - it is not a probability.

## Status lifecycle

```
proposed -> testing -> retained     (causal tier >= retain threshold)
                    -> inconclusive (survived tests, causal tier below threshold)
                    -> rejected      (hard-gate fail, or < 50% of non-skipped tests, or superseded)
```
