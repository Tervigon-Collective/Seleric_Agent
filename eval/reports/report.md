# Eval report — golden (live)

- cases: 12
- generated: 2026-10-11T09:13:45.088370+00:00

## Overall

| metric | value |
|---|---|
| spec_accuracy | 0.8966 |
| status_ok | 0.8571 |
| value_correctness | 0.0000 |
| revisions | 0.0000 |
| latency_ms | n/a |
| llm_calls | n/a |
| tokens | n/a |

## By tag

| metric | breakdown | comparison | entity_comparison | follow_up | lookup | period_comparison | roas | trend | why |
|---|---|---|---|---|---|---|---|---|---|
| spec_accuracy | 1.0000 | 0.8627 | 0.5882 | 0.5714 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.5866 |
| status_ok | n/a | 1.0000 | n/a | n/a | 0.8000 | n/a | 0.0000 | 1.0000 | n/a |
| value_correctness | n/a | n/a | n/a | n/a | 0.0000 | n/a | n/a | n/a | n/a |
| revisions | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| latency_ms | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |
| llm_calls | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |
| tokens | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |

## Cases

| id | tags | status_ok | value_correctness | spec_accuracy | revisions |
|---|---|---|---|---|---|
| lookup-net-sales-yesterday | lookup | 1.0000 | 0.0000 | 1.0000 | 0.0000 |
| lookup-net-sales-explicit-day | lookup | 1.0000 | n/a | 1.0000 | 0.0000 |
| lookup-gross-sales-yesterday | lookup | 1.0000 | n/a | 1.0000 | 0.0000 |
| compare-net-sales-two-days | comparison | 1.0000 | n/a | 1.0000 | 0.0000 |
| trend-net-sales-this-week | trend | 1.0000 | n/a | 1.0000 | 0.0000 |
| spec-th383-entity-comparison | entity_comparison,comparison,why | n/a | n/a | 0.5882 | 0.0000 |
| spec-last3-vs-today | period_comparison,comparison | n/a | n/a | 1.0000 | 0.0000 |
| spec-net-sales-yesterday | lookup | 1.0000 | n/a | 1.0000 | 0.0000 |
| spec-last-week-roas | lookup,roas | 0.0000 | n/a | 1.0000 | 0.0000 |
| spec-why-cac-yesterday | why | n/a | n/a | 0.6000 | 0.0000 |
| spec-ads-by-platform | breakdown | n/a | n/a | 1.0000 | 0.0000 |
| spec-followup-keeps-windows | follow_up,why | n/a | n/a | 0.5714 | 0.0000 |

## Diagnostics

- `lookup-net-sales-yesterday`: status completed; spec 2/2 from v2_mission; evidence []; truth net_sales=97480.96143241807; v2_mission {"status": "answered", "reason": "", "plan": {"template_id": "lookup", "steps": 1}, "run": {"result_sets": 1, "step_errors": [], "derivations": 0, "derive_errors": [], "investigations": {}, "explorations": {}, "unsupported": [], "deadline_hit": []}, "answer": {"attempts": 1, "issues": [], "status": ; v2_shadow {"status": "ok", "disagree": [], "plan": {"template_id": "lookup", "needs_agent": false, "steps": 1, "metrics": ["net_sales"]}}
- `lookup-net-sales-explicit-day`: status completed; spec 2/2 from v2_mission; evidence []; truth -; v2_mission {"status": "answered", "reason": "", "plan": {"template_id": "lookup", "steps": 1}, "run": {"result_sets": 1, "step_errors": [], "derivations": 0, "derive_errors": [], "investigations": {}, "explorations": {}, "unsupported": [], "deadline_hit": []}, "answer": {"attempts": 1, "issues": [], "status": ; v2_shadow {"status": "ok", "disagree": [], "plan": {"template_id": "lookup", "needs_agent": false, "steps": 1, "metrics": ["net_sales"]}}
- `lookup-gross-sales-yesterday`: status completed; spec 2/2 from v2_mission; evidence []; truth -; v2_mission {"status": "answered", "reason": "", "plan": {"template_id": "lookup", "steps": 1}, "run": {"result_sets": 1, "step_errors": [], "derivations": 0, "derive_errors": [], "investigations": {}, "explorations": {}, "unsupported": [], "deadline_hit": []}, "answer": {"attempts": 1, "issues": [], "status": ; v2_shadow {"status": "ok", "disagree": [], "plan": {"template_id": "lookup", "needs_agent": false, "steps": 1, "metrics": ["gross_sales"]}}
- `compare-net-sales-two-days`: status completed; spec 2/2 from v2_mission; evidence []; truth -; v2_mission {"status": "answered", "reason": "", "plan": {"template_id": "period_comparison", "steps": 4}, "run": {"result_sets": 2, "step_errors": [], "derivations": 2, "derive_errors": [], "investigations": {}, "explorations": {}, "unsupported": [], "deadline_hit": []}, "answer": {"attempts": 1, "issues": [],; v2_shadow {"status": "ok", "disagree": [], "plan": {"template_id": "period_comparison", "needs_agent": false, "steps": 4, "metrics": ["net_sales"]}}
- `trend-net-sales-this-week`: status completed; spec 2/2 from v2_mission; evidence []; truth -; v2_mission {"status": "answered", "reason": "", "plan": {"template_id": "lookup", "steps": 1}, "run": {"result_sets": 1, "step_errors": [], "derivations": 0, "derive_errors": [], "investigations": {}, "explorations": {}, "unsupported": [], "deadline_hit": []}, "answer": {"attempts": 1, "issues": [], "status": ; v2_shadow {"status": "ok", "disagree": [], "plan": {"template_id": "lookup", "needs_agent": false, "steps": 1, "metrics": ["net_sales"]}}
- `spec-th383-entity-comparison`: status failed; spec 10/17 from v2_mission; evidence []; truth -; v2_mission {"status": "fallback", "reason": "no_template", "plan": {"template_id": "agent", "steps": 0}}; v2_shadow {"status": "ok", "disagree": ["shape", "windows"], "plan": {"template_id": "agent", "needs_agent": true, "steps": 0, "metrics": []}}
- `spec-last3-vs-today`: status partial; spec 2/2 from v2_mission; evidence []; truth -; v2_mission {"status": "answered", "reason": "", "plan": {"template_id": "period_comparison", "steps": 40}, "run": {"result_sets": 20, "step_errors": ["unsupported_query", "unsupported_query", "unsupported_query", "unsupported_query", "unsupported_query", "unsupported_query", "unsupported_query", "unsupported_q; v2_shadow {"status": "ok", "disagree": [], "plan": {"template_id": "period_comparison", "needs_agent": false, "steps": 40, "metrics": ["ad_spend", "cac", "contribution_margin_pct", "conversion_rate", "mer", "ne
- `spec-net-sales-yesterday`: status completed; spec 7/7 from v2_mission; evidence []; truth -; v2_mission {"status": "answered", "reason": "", "plan": {"template_id": "lookup", "steps": 1}, "run": {"result_sets": 1, "step_errors": [], "derivations": 0, "derive_errors": [], "investigations": {}, "explorations": {}, "unsupported": [], "deadline_hit": []}, "answer": {"attempts": 1, "issues": [], "status": ; v2_shadow {"status": "ok", "disagree": [], "plan": {"template_id": "lookup", "needs_agent": false, "steps": 1, "metrics": ["net_sales"]}}
- `spec-last-week-roas`: status failed; spec 6/6 from v2_mission; evidence []; truth -; v2_mission {"status": "fallback", "reason": "no_template", "plan": {"template_id": "agent", "steps": 0}}; v2_shadow {"status": "ok", "disagree": [], "plan": {"template_id": "agent", "needs_agent": true, "steps": 0, "metrics": []}}
- `spec-why-cac-yesterday`: status failed; spec 6/10 from v2_mission; evidence []; truth -; v2_mission {"status": "fallback", "reason": "answer_failed", "plan": {"template_id": "why", "steps": 1}, "run": {"result_sets": 0, "step_errors": [], "derivations": 0, "derive_errors": [], "investigations": {}, "explorations": {}, "unsupported": [], "deadline_hit": []}, "answer": {"attempts": 1, "issues": [], ; v2_shadow {"status": "ok", "disagree": [], "plan": {"template_id": "why", "needs_agent": false, "steps": 1, "metrics": ["cac"]}}
- `spec-ads-by-platform`: status completed; spec 7/7 from v2_mission; evidence []; truth -; v2_mission {"status": "answered", "reason": "", "plan": {"template_id": "breakdown", "steps": 1}, "run": {"result_sets": 1, "step_errors": [], "derivations": 0, "derive_errors": [], "investigations": {}, "explorations": {}, "unsupported": [], "deadline_hit": []}, "answer": {"attempts": 1, "issues": [], "status; v2_shadow {"status": "ok", "disagree": [], "plan": {"template_id": "breakdown", "needs_agent": false, "steps": 1, "metrics": ["ad_spend"]}}
- `spec-followup-keeps-windows`: status partial; spec 4/7 from v2_mission; evidence []; truth -; v2_mission {"status": "answered", "reason": "", "plan": {"template_id": "why", "steps": 1}, "run": {"result_sets": 0, "step_errors": [], "derivations": 0, "derive_errors": [], "investigations": {}, "explorations": {}, "unsupported": [], "deadline_hit": []}, "answer": {"attempts": 2, "issues": ["unresolved_clai; v2_shadow {"status": "ok", "disagree": ["windows"], "plan": {"template_id": "why", "needs_agent": false, "steps": 1, "metrics": ["cac"]}}

## Baseline

_not compared_
