# Eval report — golden (live)

- cases: 12
- generated: 2026-10-11T08:04:52.230140+00:00

## Overall

| metric | value |
|---|---|
| spec_accuracy | 0.8133 |
| status_ok | 0.0000 |
| value_correctness | 0.0000 |
| revisions | 0.0000 |
| latency_ms | n/a |
| llm_calls | n/a |
| tokens | n/a |

## By tag

| metric | breakdown | comparison | entity_comparison | follow_up | lookup | period_comparison | roas | trend | why |
|---|---|---|---|---|---|---|---|---|---|
| spec_accuracy | 1.0000 | 0.5294 | 0.5882 | 0.5714 | 1.0000 | 0.0000 | 1.0000 | 1.0000 | 0.5866 |
| status_ok | n/a | 0.0000 | n/a | n/a | 0.0000 | n/a | 0.0000 | 0.0000 | n/a |
| value_correctness | n/a | n/a | n/a | n/a | 0.0000 | n/a | n/a | n/a | n/a |
| revisions | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| latency_ms | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |
| llm_calls | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |
| tokens | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |

## Cases

| id | tags | status_ok | value_correctness | spec_accuracy | revisions |
|---|---|---|---|---|---|
| lookup-net-sales-yesterday | lookup | 0.0000 | 0.0000 | 1.0000 | 0.0000 |
| lookup-net-sales-explicit-day | lookup | 0.0000 | n/a | 1.0000 | 0.0000 |
| lookup-gross-sales-yesterday | lookup | 0.0000 | n/a | 1.0000 | 0.0000 |
| compare-net-sales-two-days | comparison | 0.0000 | n/a | 1.0000 | 0.0000 |
| trend-net-sales-this-week | trend | 0.0000 | n/a | 1.0000 | 0.0000 |
| spec-th383-entity-comparison | entity_comparison,comparison,why | n/a | n/a | 0.5882 | 0.0000 |
| spec-last3-vs-today | period_comparison,comparison | n/a | n/a | 0.0000 | 0.0000 |
| spec-net-sales-yesterday | lookup | 0.0000 | n/a | 1.0000 | 0.0000 |
| spec-last-week-roas | lookup,roas | 0.0000 | n/a | 1.0000 | 0.0000 |
| spec-why-cac-yesterday | why | n/a | n/a | 0.6000 | 0.0000 |
| spec-ads-by-platform | breakdown | n/a | n/a | 1.0000 | 0.0000 |
| spec-followup-keeps-windows | follow_up,why | n/a | n/a | 0.5714 | 0.0000 |

## Diagnostics

- `lookup-net-sales-yesterday`: spec 2/2 from v2_mission; evidence []; truth net_sales=97480.96143241807; v2_shadow {"status": "ok", "disagree": [], "plan": {"template_id": "lookup", "needs_agent": false, "steps": 1, "metrics": ["net_sales"]}}
- `lookup-net-sales-explicit-day`: spec 2/2 from v2_mission; evidence []; truth -; v2_shadow {"status": "ok", "disagree": [], "plan": {"template_id": "lookup", "needs_agent": false, "steps": 1, "metrics": ["net_sales"]}}
- `lookup-gross-sales-yesterday`: spec 2/2 from v2_mission; evidence []; truth -; v2_shadow {"status": "ok", "disagree": [], "plan": {"template_id": "lookup", "needs_agent": false, "steps": 1, "metrics": ["gross_sales"]}}
- `compare-net-sales-two-days`: spec 2/2 from v2_shadow; evidence []; truth -; v2_shadow {"status": "ok", "disagree": [], "plan": {"template_id": "period_comparison", "needs_agent": false, "steps": 4, "metrics": ["net_sales"]}}
- `trend-net-sales-this-week`: spec 2/2 from v2_mission; evidence []; truth -; v2_shadow {"status": "ok", "disagree": ["windows"], "plan": {"template_id": "lookup", "needs_agent": false, "steps": 1, "metrics": ["net_sales"]}}
- `spec-th383-entity-comparison`: spec 10/17 from v2_mission; evidence []; truth -; v2_shadow {"status": "ok", "disagree": ["windows"], "plan": {"template_id": "agent", "needs_agent": true, "steps": 0, "metrics": []}}
- `spec-last3-vs-today`: spec 0/2 from None; evidence []; truth -; v2_shadow {"status": "clarification", "disagree": [], "clarification": {"reason": "You didn't name which metric(s) to compare. I need to know what measure(s) you want shown (e.g., sales, orders, impressions), and whether you want totals or a per-day breakdown or any channel/product filter.", "question": "Whic
- `spec-net-sales-yesterday`: spec 7/7 from v2_mission; evidence []; truth -; v2_shadow {"status": "ok", "disagree": [], "plan": {"template_id": "lookup", "needs_agent": false, "steps": 1, "metrics": ["net_sales"]}}
- `spec-last-week-roas`: spec 6/6 from v2_mission; evidence []; truth -; v2_shadow {"status": "clarification", "disagree": [], "clarification": {"reason": "\u201cROAS\u201d can refer to multiple measures in the catalogue (e.g., Gross ROAS, Net ROAS, or P&L net ROAS). I need to know which one you mean before I fetch last week\u2019s value.", "question": "Which ROAS do you want for 
- `spec-why-cac-yesterday`: spec 6/10 from v2_shadow; evidence []; truth -; v2_shadow {"status": "ok", "disagree": [], "plan": {"template_id": "why", "needs_agent": false, "steps": 1, "metrics": ["cac"]}}
- `spec-ads-by-platform`: spec 7/7 from v2_mission; evidence []; truth -; v2_shadow {"status": "ok", "disagree": [], "plan": {"template_id": "breakdown", "needs_agent": false, "steps": 1, "metrics": ["ad_spend"]}}
- `spec-followup-keeps-windows`: spec 4/7 from v2_mission; evidence []; truth -; v2_shadow {"status": "ok", "disagree": [], "plan": {"template_id": "why", "needs_agent": false, "steps": 1, "metrics": ["cac"]}}

## Baseline

_not compared_
