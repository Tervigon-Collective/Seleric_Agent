# Eval report — reports (record)

- cases: 1
- generated: 2026-10-12T10:05:10.465845+00:00

## Overall

| metric | value |
|---|---|
| spec_accuracy | n/a |
| status_ok | 0.0000 |
| value_correctness | n/a |
| revisions | 0.0000 |
| latency_ms | 50250.0000 |
| llm_calls | 1.0000 |
| tokens | 11893.0000 |

## By tag

| metric | funnel | multi_objective | ranking | report |
|---|---|---|---|---|
| spec_accuracy | n/a | n/a | n/a | n/a |
| status_ok | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| value_correctness | n/a | n/a | n/a | n/a |
| revisions | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| latency_ms | 50250.0000 | 50250.0000 | 50250.0000 | 50250.0000 |
| llm_calls | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| tokens | 11893.0000 | 11893.0000 | 11893.0000 | 11893.0000 |

## Cases

| id | tags | route | status_ok | value_correctness | spec_accuracy | revisions |
|---|---|---|---|---|---|---|
| report-full-funnel-meta-oct1-11 | report,multi_objective,funnel,ranking | failed | 0.0000 | n/a | n/a | 0.0000 |

## Diagnostics

- `report-full-funnel-meta-oct1-11`: route failed (fallback: no_data); status failed; spec -/- from v2_mission; evidence []; truth -; v2_mission {"status": "fallback", "reason": "no_data", "plan": {"template_id": "funnel", "steps": 9}, "run": {"result_sets": 5, "step_errors": ["unknown_window_role", "unknown_window_role", "unknown_window_role", "unknown_window_role", "unknown_window_role"], "derivations": 4, "derive_errors": ["unit_mismatch", "zero_whole", "zero_whole", "zero_whole"], "investigations": {}, "explorations": {}, "unsupported": [], "deadline_hit": []}}; v2_shadow {"status": "ok", "disagree": ["windows", "filters"], "plan": {"template_id": "funnel", "needs_agent": false, "steps": 11, "metrics": ["funnel_order_purchases", "funnel_order_revenue", "funnel_purchase

## Baseline

_not compared_
