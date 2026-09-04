# Conflict governance

Conflicts are classified and arbitrated **deterministically**. The LLM never chooses winners.

## Types

| Type | Meaning |
| --- | --- |
| DATA_CONTRADICTION | Same metric/dims/window, different values |
| METRIC_SEMANTIC_CONFLICT | Competing metric identities (e.g. CAC aliases) |
| TIME_RANGE_CONFLICT | Incompatible windows used as contemporaneous |
| SOURCE_CONFLICT | Sources disagree on the same fact |
| METHODOLOGY_CONFLICT | Strategy action mismatches diagnosed mechanism |
| CAUSAL_CONFLICT | Multiple retained hypotheses compete |
| MODEL_CONFLICT | Forecast with invalid/drifted/missing model |
| FACTUAL_CONFLICT | Passthrough contradictions |

## Arbitration rules (examples)

- Prefer non-synthetic / MCP provenance over FIXTURE/TEMPLATE
- Prefer `NormalizedQuery.primary_metric` for semantic CAC conflicts
- Reject strategy that cuts media spend when diagnosis is technical/funnel
- Reject forecasts with invalid drift / insufficient model (recorded as limitation)
- Causal multi-hypothesis conflicts stay unresolved until Skeptic/ranking — not LLM choice

## Completion

Only **unresolved blocking** conflicts prevent completion. Handled methodology/model conflicts become limitations in the response.
