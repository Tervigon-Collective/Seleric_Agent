# Eval report — reports_after (record)

- cases: 1
- generated: 2026-10-12T10:20:25.933354+00:00

## Overall

| metric | value |
|---|---|
| spec_accuracy | n/a |
| status_ok | 0.0000 |
| value_correctness | n/a |
| sections_covered | 0.8333 |
| chart_reconciles | 1.0000 |
| numbers_grounded | 1.0000 |
| revisions | 0.0000 |
| latency_ms | 117382.0000 |
| llm_calls | 3.0000 |
| tokens | 19479.0000 |
| query_calls | 14.0000 |

## By tag

| metric | funnel | multi_objective | ranking | report |
|---|---|---|---|---|
| spec_accuracy | n/a | n/a | n/a | n/a |
| status_ok | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| value_correctness | n/a | n/a | n/a | n/a |
| sections_covered | 0.8333 | 0.8333 | 0.8333 | 0.8333 |
| chart_reconciles | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| numbers_grounded | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| revisions | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| latency_ms | 117382.0000 | 117382.0000 | 117382.0000 | 117382.0000 |
| llm_calls | 3.0000 | 3.0000 | 3.0000 | 3.0000 |
| tokens | 19479.0000 | 19479.0000 | 19479.0000 | 19479.0000 |
| query_calls | 14.0000 | 14.0000 | 14.0000 | 14.0000 |

## Cases

| id | tags | route | status_ok | value_correctness | spec_accuracy | revisions |
|---|---|---|---|---|---|---|
| report-full-funnel-meta-oct1-11 | report,multi_objective,funnel,ranking | v2 | 0.0000 | n/a | n/a | 0.0000 |

## Diagnostics

- `report-full-funnel-meta-oct1-11`: route v2; status partial; spec -/- from v2_mission; evidence ['funnel_order_purchases', 'funnel_order_revenue', 'funnel_purchases', 'funnel_revenue', 'net_profit']; truth -; v2_mission {"status": "answered", "reason": "", "plan": {"template_id": "funnel+ranking+lookup", "steps": 17, "sections": [{"id": "s1", "template_id": "funnel", "needs_agent": false, "steps": 3}, {"id": "s2", "template_id": "funnel", "needs_agent": false, "steps": 3}, {"id": "s3", "template_id": "funnel", "needs_agent": false, "steps": 6}, {"id": "s4", "template_id": "ranking", "needs_agent": false, "steps": 2}, {"id": "s5", "template_id": "agent", "needs_agent": true, "steps": 0}, {"id": "s6", "template_id": "lookup", "needs_agent": false, "steps": 3}]}, "run": {"result_sets": 14, "step_errors": [], "de; v2_shadow {"status": "ok", "disagree": ["windows", "filters"], "plan": {"template_id": "funnel", "needs_agent": false, "steps": 6, "metrics": ["funnel_order_purchases", "funnel_order_revenue", "gross_profit", "

## Baseline

_not compared_
