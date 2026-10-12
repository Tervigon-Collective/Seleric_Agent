# Eval report — golden (live)

- cases: 12
- generated: 2026-10-11T13:57:01.051400+00:00

## Overall

| metric | value |
|---|---|
| spec_accuracy | 0.9342 |
| status_ok | 1.0000 |
| value_correctness | 0.0000 |
| revisions | 0.0000 |
| latency_ms | 24125.6667 |
| llm_calls | 2.1667 |
| tokens | 7136.4167 |

## By tag

| metric | breakdown | comparison | entity_comparison | follow_up | lookup | period_comparison | roas | trend | why |
|---|---|---|---|---|---|---|---|---|---|
| spec_accuracy | 0.8571 | 0.7843 | 0.3529 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.7843 |
| status_ok | n/a | 1.0000 | n/a | n/a | 1.0000 | n/a | 1.0000 | 1.0000 | n/a |
| value_correctness | n/a | n/a | n/a | n/a | 0.0000 | n/a | n/a | n/a | n/a |
| revisions | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| latency_ms | 32836.0000 | 25548.6667 | 14748.0000 | 37590.0000 | 18142.2000 | 35158.0000 | 19875.0000 | 18636.0000 | 28475.6667 |
| llm_calls | 3.0000 | 1.6667 | 1.0000 | 3.0000 | 2.0000 | 2.0000 | 2.0000 | 2.0000 | 2.3333 |
| tokens | 6924.0000 | 8136.0000 | 5655.0000 | 7894.0000 | 6406.0000 | 10697.0000 | 6884.0000 | 6506.0000 | 7141.3333 |

## Cases

| id | tags | status_ok | value_correctness | spec_accuracy | revisions |
|---|---|---|---|---|---|
| lookup-net-sales-yesterday | lookup | 1.0000 | 0.0000 | 1.0000 | 0.0000 |
| lookup-net-sales-explicit-day | lookup | 1.0000 | n/a | 1.0000 | 0.0000 |
| lookup-gross-sales-yesterday | lookup | 1.0000 | n/a | 1.0000 | 0.0000 |
| compare-net-sales-two-days | comparison | 1.0000 | n/a | 1.0000 | 0.0000 |
| trend-net-sales-this-week | trend | 1.0000 | n/a | 1.0000 | 0.0000 |
| spec-th383-entity-comparison | entity_comparison,comparison,why | n/a | n/a | 0.3529 | 0.0000 |
| spec-last3-vs-today | period_comparison,comparison | n/a | n/a | 1.0000 | 0.0000 |
| spec-net-sales-yesterday | lookup | 1.0000 | n/a | 1.0000 | 0.0000 |
| spec-last-week-roas | lookup,roas | 1.0000 | n/a | 1.0000 | 0.0000 |
| spec-why-cac-yesterday | why | n/a | n/a | 1.0000 | 0.0000 |
| spec-ads-by-platform | breakdown | n/a | n/a | 0.8571 | 0.0000 |
| spec-followup-keeps-windows | follow_up,why | n/a | n/a | 1.0000 | 0.0000 |

## Diagnostics

- `lookup-net-sales-yesterday`: status completed; spec 2/2 from v2_mission; evidence ['net_sales']; truth net_sales=97480.96143241807; v2_mission {"status": "answered", "reason": "", "plan": {"template_id": "lookup", "steps": 1}, "run": {"result_sets": 1, "step_errors": [], "derivations": 0, "derive_errors": [], "investigations": {}, "explorations": {}, "unsupported": [], "deadline_hit": []}, "answer": {"attempts": 1, "input_tokens": 726, "output_tokens": 745, "model": "fallback:gpt-5-mini,gpt-4o-code,Llama-4-Maverick-17B-128E-Instruct-FP8", "issues": [], "status": "completed"}}; v2_shadow {"status": "ok", "disagree": [], "plan": {"template_id": "lookup", "needs_agent": false, "steps": 1, "metrics": ["net_sales"]}}
- `lookup-net-sales-explicit-day`: status completed; spec 2/2 from v2_mission; evidence ['net_sales']; truth -; v2_mission {"status": "answered", "reason": "", "plan": {"template_id": "lookup", "steps": 1}, "run": {"result_sets": 1, "step_errors": [], "derivations": 0, "derive_errors": [], "investigations": {}, "explorations": {}, "unsupported": [], "deadline_hit": []}, "answer": {"attempts": 1, "input_tokens": 733, "output_tokens": 916, "model": "fallback:gpt-5-mini,gpt-4o-code,Llama-4-Maverick-17B-128E-Instruct-FP8", "issues": [], "status": "completed"}}; v2_shadow {"status": "ok", "disagree": [], "plan": {"template_id": "lookup", "needs_agent": false, "steps": 1, "metrics": ["net_sales"]}}
- `lookup-gross-sales-yesterday`: status completed; spec 2/2 from v2_mission; evidence ['gross_sales']; truth -; v2_mission {"status": "answered", "reason": "", "plan": {"template_id": "lookup", "steps": 1}, "run": {"result_sets": 1, "step_errors": [], "derivations": 0, "derive_errors": [], "investigations": {}, "explorations": {}, "unsupported": [], "deadline_hit": []}, "answer": {"attempts": 1, "input_tokens": 726, "output_tokens": 749, "model": "fallback:gpt-5-mini,gpt-4o-code,Llama-4-Maverick-17B-128E-Instruct-FP8", "issues": [], "status": "completed"}}; v2_shadow {"status": "ok", "disagree": [], "plan": {"template_id": "lookup", "needs_agent": false, "steps": 1, "metrics": ["gross_sales"]}}
- `compare-net-sales-two-days`: status completed; spec 2/2 from v2_mission; evidence ['net_sales']; truth -; v2_mission {"status": "answered", "reason": "", "plan": {"template_id": "period_comparison", "steps": 4}, "run": {"result_sets": 2, "step_errors": [], "derivations": 2, "derive_errors": [], "investigations": {}, "explorations": {}, "unsupported": [], "deadline_hit": []}, "answer": {"attempts": 1, "input_tokens": 840, "output_tokens": 1556, "model": "fallback:gpt-5-mini,gpt-4o-code,Llama-4-Maverick-17B-128E-Instruct-FP8", "issues": [], "status": "completed"}}; v2_shadow {"status": "ok", "disagree": [], "plan": {"template_id": "period_comparison", "needs_agent": false, "steps": 4, "metrics": ["net_sales"]}}
- `trend-net-sales-this-week`: status completed; spec 2/2 from v2_mission; evidence ['net_sales']; truth -; v2_mission {"status": "answered", "reason": "", "plan": {"template_id": "lookup", "steps": 1}, "run": {"result_sets": 1, "step_errors": [], "derivations": 0, "derive_errors": [], "investigations": {}, "explorations": {}, "unsupported": [], "deadline_hit": []}, "answer": {"attempts": 1, "input_tokens": 728, "output_tokens": 1255, "model": "fallback:gpt-5-mini,gpt-4o-code,Llama-4-Maverick-17B-128E-Instruct-FP8", "issues": [], "status": "completed"}}; v2_shadow {"status": "ok", "disagree": [], "plan": {"template_id": "lookup", "needs_agent": false, "steps": 1, "metrics": ["net_sales"]}}
- `spec-th383-entity-comparison`: status failed; spec 6/17 from v2_mission; evidence []; truth -; v2_mission {"status": "fallback", "reason": "no_template", "plan": {"template_id": "agent", "steps": 0}}; v2_shadow {"status": "ok", "disagree": ["windows"], "plan": {"template_id": "agent", "needs_agent": true, "steps": 0, "metrics": []}}
- `spec-last3-vs-today`: status completed; spec 2/2 from v2_mission; evidence ['ad_spend', 'cac', 'contribution_margin_pct', 'conversion_rate', 'mer', 'net_profit', 'net_roas', 'net_sales', 'orders', 'repeat_rate']; truth -; v2_mission {"status": "answered", "reason": "", "plan": {"template_id": "period_comparison", "steps": 40}, "run": {"result_sets": 20, "step_errors": [], "derivations": 20, "derive_errors": [], "investigations": {}, "explorations": {}, "unsupported": [], "deadline_hit": []}, "answer": {"attempts": 1, "input_tokens": 1926, "output_tokens": 4130, "model": "fallback:gpt-5-mini,gpt-4o-code,Llama-4-Maverick-17B-128E-Instruct-FP8", "issues": [], "status": "completed"}}; v2_shadow {"status": "ok", "disagree": [], "plan": {"template_id": "period_comparison", "needs_agent": false, "steps": 40, "metrics": ["ad_spend", "cac", "contribution_margin_pct", "conversion_rate", "mer", "ne
- `spec-net-sales-yesterday`: status completed; spec 7/7 from v2_mission; evidence ['net_sales']; truth -; v2_mission {"status": "answered", "reason": "", "plan": {"template_id": "lookup", "steps": 1}, "run": {"result_sets": 1, "step_errors": [], "derivations": 0, "derive_errors": [], "investigations": {}, "explorations": {}, "unsupported": [], "deadline_hit": []}, "answer": {"attempts": 1, "input_tokens": 726, "output_tokens": 687, "model": "fallback:gpt-5-mini,gpt-4o-code,Llama-4-Maverick-17B-128E-Instruct-FP8", "issues": [], "status": "completed"}}; v2_shadow {"status": "ok", "disagree": [], "plan": {"template_id": "lookup", "needs_agent": false, "steps": 1, "metrics": ["net_sales"]}}
- `spec-last-week-roas`: status completed; spec 6/6 from v2_mission; evidence ['net_roas']; truth -; v2_mission {"status": "answered", "reason": "", "plan": {"template_id": "lookup", "steps": 1}, "run": {"result_sets": 1, "step_errors": [], "derivations": 0, "derive_errors": [], "investigations": {}, "explorations": {}, "unsupported": [], "deadline_hit": []}, "answer": {"attempts": 1, "input_tokens": 729, "output_tokens": 1119, "model": "fallback:gpt-5-mini,gpt-4o-code,Llama-4-Maverick-17B-128E-Instruct-FP8", "issues": [], "status": "completed"}}; v2_shadow {"status": "ok", "disagree": [], "plan": {"template_id": "agent", "needs_agent": true, "steps": 0, "metrics": []}}
- `spec-why-cac-yesterday`: status partial; spec 10/10 from v2_mission; evidence []; truth -; v2_mission {"status": "answered", "reason": "", "plan": {"template_id": "why", "steps": 1}, "run": {"result_sets": 0, "step_errors": [], "derivations": 1, "derive_errors": [], "investigations": {"e1": {"status": "done", "findings": 1, "limitations": ["localise_skipped_non_additive"]}}, "explorations": {}, "unsupported": [], "deadline_hit": []}, "answer": {"attempts": 2, "input_tokens": 784, "output_tokens": 1961, "model": "fallback:gpt-5-mini,gpt-4o-code,Llama-4-Maverick-17B-128E-Instruct-FP8", "issues": [], "status": "partial"}}; v2_shadow {"status": "ok", "disagree": ["windows"], "plan": {"template_id": "why", "needs_agent": false, "steps": 1, "metrics": ["cac"]}}
- `spec-ads-by-platform`: status completed; spec 6/7 from v2_mission; evidence ['ad_spend']; truth -; v2_mission {"status": "answered", "reason": "", "plan": {"template_id": "breakdown", "steps": 1}, "run": {"result_sets": 1, "step_errors": [], "derivations": 0, "derive_errors": [], "investigations": {}, "explorations": {}, "unsupported": [], "deadline_hit": []}, "answer": {"attempts": 2, "input_tokens": 768, "output_tokens": 1416, "model": "fallback:gpt-5-mini,gpt-4o-code,Llama-4-Maverick-17B-128E-Instruct-FP8", "issues": [], "status": "completed"}}; v2_shadow {"status": "ok", "disagree": [], "plan": {"template_id": "breakdown", "needs_agent": false, "steps": 1, "metrics": ["ad_spend"]}}
- `spec-followup-keeps-windows`: status partial; spec 7/7 from v2_mission; evidence []; truth -; v2_mission {"status": "answered", "reason": "", "plan": {"template_id": "why", "steps": 1}, "run": {"result_sets": 0, "step_errors": [], "derivations": 1, "derive_errors": [], "investigations": {"e1": {"status": "done", "findings": 1, "limitations": ["localise_skipped_non_additive"]}}, "explorations": {}, "unsupported": [], "deadline_hit": []}, "answer": {"attempts": 2, "input_tokens": 776, "output_tokens": 1755, "model": "fallback:gpt-5-mini,gpt-4o-code,Llama-4-Maverick-17B-128E-Instruct-FP8", "issues": ["role_mismatch", "role_mismatch"], "status": "partial"}}; v2_shadow {"status": "ok", "disagree": [], "plan": {"template_id": "why", "needs_agent": false, "steps": 1, "metrics": ["cac"]}}

## Baseline

_not compared_
