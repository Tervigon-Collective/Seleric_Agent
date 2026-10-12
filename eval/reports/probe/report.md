# Eval report — probe (live)

- cases: 5
- generated: 2026-10-11T13:06:01.621580+00:00

## Overall

| metric | value |
|---|---|
| spec_accuracy | 1.0000 |
| status_ok | 0.8000 |
| value_correctness | 0.0000 |
| revisions | 0.0000 |
| latency_ms | 23049.4000 |
| llm_calls | 2.2000 |
| tokens | 6769.2000 |

## By tag

| metric | comparison | lookup | trend |
|---|---|---|---|
| spec_accuracy | 1.0000 | 1.0000 | 1.0000 |
| status_ok | 0.0000 | 1.0000 | 1.0000 |
| value_correctness | n/a | 0.0000 | n/a |
| revisions | 0.0000 | 0.0000 | 0.0000 |
| latency_ms | 36638.0000 | 18969.6667 | 21700.0000 |
| llm_calls | 3.0000 | 2.0000 | 2.0000 |
| tokens | 7831.0000 | 6574.0000 | 6293.0000 |

## Cases

| id | tags | status_ok | value_correctness | spec_accuracy | revisions |
|---|---|---|---|---|---|
| lookup-net-sales-yesterday | lookup | 1.0000 | 0.0000 | 1.0000 | 0.0000 |
| lookup-net-sales-explicit-day | lookup | 1.0000 | n/a | 1.0000 | 0.0000 |
| lookup-gross-sales-yesterday | lookup | 1.0000 | n/a | 1.0000 | 0.0000 |
| compare-net-sales-two-days | comparison | 0.0000 | n/a | 1.0000 | 0.0000 |
| trend-net-sales-this-week | trend | 1.0000 | n/a | 1.0000 | 0.0000 |

## Diagnostics

- `lookup-net-sales-yesterday`: status completed; spec 2/2 from v2_mission; evidence ['net_sales']; truth net_sales=97480.96143241807; v2_mission {"status": "answered", "reason": "", "plan": {"template_id": "lookup", "steps": 1}, "run": {"result_sets": 1, "step_errors": [], "derivations": 0, "derive_errors": [], "investigations": {}, "explorations": {}, "unsupported": [], "deadline_hit": []}, "answer": {"attempts": 1, "input_tokens": 726, "output_tokens": 1331, "model": "fallback:gpt-5-mini,gpt-4o-code,Llama-4-Maverick-17B-128E-Instruct-FP8", "issues": [], "status": "completed"}}; v2_shadow {"status": "ok", "disagree": [], "plan": {"template_id": "lookup", "needs_agent": false, "steps": 1, "metrics": ["net_sales"]}}
- `lookup-net-sales-explicit-day`: status completed; spec 2/2 from v2_mission; evidence ['net_sales']; truth -; v2_mission {"status": "answered", "reason": "", "plan": {"template_id": "lookup", "steps": 1}, "run": {"result_sets": 1, "step_errors": [], "derivations": 0, "derive_errors": [], "investigations": {}, "explorations": {}, "unsupported": [], "deadline_hit": []}, "answer": {"attempts": 1, "input_tokens": 733, "output_tokens": 1120, "model": "fallback:gpt-5-mini,gpt-4o-code,Llama-4-Maverick-17B-128E-Instruct-FP8", "issues": [], "status": "completed"}}; v2_shadow {"status": "ok", "disagree": [], "plan": {"template_id": "lookup", "needs_agent": false, "steps": 1, "metrics": ["net_sales"]}}
- `lookup-gross-sales-yesterday`: status completed; spec 2/2 from v2_mission; evidence ['gross_sales']; truth -; v2_mission {"status": "answered", "reason": "", "plan": {"template_id": "lookup", "steps": 1}, "run": {"result_sets": 1, "step_errors": [], "derivations": 0, "derive_errors": [], "investigations": {}, "explorations": {}, "unsupported": [], "deadline_hit": []}, "answer": {"attempts": 1, "input_tokens": 726, "output_tokens": 877, "model": "fallback:gpt-5-mini,gpt-4o-code,Llama-4-Maverick-17B-128E-Instruct-FP8", "issues": [], "status": "completed"}}; v2_shadow {"status": "ok", "disagree": [], "plan": {"template_id": "lookup", "needs_agent": false, "steps": 1, "metrics": ["gross_sales"]}}
- `compare-net-sales-two-days`: status partial; spec 2/2 from v2_mission; evidence ['net_sales']; truth -; v2_mission {"status": "answered", "reason": "", "plan": {"template_id": "period_comparison", "steps": 4}, "run": {"result_sets": 2, "step_errors": [], "derivations": 2, "derive_errors": [], "investigations": {}, "explorations": {}, "unsupported": [], "deadline_hit": []}, "answer": {"attempts": 2, "input_tokens": 865, "output_tokens": 1499, "model": "fallback:gpt-5-mini,gpt-4o-code,Llama-4-Maverick-17B-128E-Instruct-FP8", "issues": ["unresolved_claim", "unresolved_claim"], "status": "partial"}}; v2_shadow {"status": "ok", "disagree": [], "plan": {"template_id": "period_comparison", "needs_agent": false, "steps": 4, "metrics": ["net_sales"]}}
- `trend-net-sales-this-week`: status completed; spec 2/2 from v2_mission; evidence ['net_sales']; truth -; v2_mission {"status": "answered", "reason": "", "plan": {"template_id": "lookup", "steps": 1}, "run": {"result_sets": 1, "step_errors": [], "derivations": 0, "derive_errors": [], "investigations": {}, "explorations": {}, "unsupported": [], "deadline_hit": []}, "answer": {"attempts": 1, "input_tokens": 728, "output_tokens": 784, "model": "fallback:gpt-5-mini,gpt-4o-code,Llama-4-Maverick-17B-128E-Instruct-FP8", "issues": [], "status": "completed"}}; v2_shadow {"status": "ok", "disagree": [], "plan": {"template_id": "lookup", "needs_agent": false, "steps": 1, "metrics": ["net_sales"]}}

## Baseline

_not compared_
