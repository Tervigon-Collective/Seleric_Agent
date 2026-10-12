# After response (live)

status: partial
route: v2
fallback: None
latency_ms: 170657.0
llm_calls: 3
tokens: 11068

## Diagnostics (truncated)
```json
{
  "spec_source": "v2_mission",
  "route": "v2",
  "fallback_reason": null,
  "v2_mission": {
    "status": "answered",
    "reason": "",
    "plan": {
      "template_id": "funnel+composition+ranking",
      "steps": 6,
      "sections": [
        {
          "id": "s1",
          "template_id": "funnel",
          "needs_agent": false,
          "steps": 3
        },
        {
          "id": "s2",
          "template_id": "composition",
          "needs_agent": false,
          "steps": 1
        },
        {
          "id": "s3",
          "template_id": "ranking",
          "needs_agent": false,
          "steps": 2
        },
        {
          "id": "s4",
          "template_id": "agent",
          "needs_agent": true,
          "steps": 0
        },
        {
          "id": "s5",
          "template_id": "agent",
          "needs_agent": true,
          "steps": 0
        }
      ]
    },
    "run": {
      "result_sets": 5,
      "step_errors": [],
      "derivations": 1,
      "derive_errors": [],
      "acquired_metrics": [
        "funnel_order_purchases",
        "funnel_order_revenue",
        "net_sales"
      ],
      "failed_metrics": [],
      "query_calls": 4,
      "cache_hits": 0,
      "investigations": {},
      "explorations": {},
      "unsupported": [],
      "deadline_hit": []
    },
    "answer": {
      "attempts": 2,
      "input_tokens": 2707,
      "output_tokens": 2820,
      "model": "gpt-5-mini",
      "issues": [
        "unresolved_claim",
        "unresolved_claim",
        "unresolved_claim",
        "unresolved_claim"
      ],
      "status": "partial"
    },
    "coverage": {
      "status": "partial",
      "sections": [
        {
          "section_id": "s1",
          "status": "complete",
          "objective": "Full funnel diagnostic and drop-off analysis",
          "acquired_metrics": [
            "funnel_order_purchases",
            "funnel_order_revenue"
          ],
          "missing_metrics": [],
          "failed_metrics": [],
          "notes": [],
          "attempts": [
            {
              "section_id": "s1",
              "metric_id": "funnel_order_purchases",
              "args_hash": "fac4fe322659cc8e",
              "ok": true,
              "error_code": "",
              "rows": 11,
              "latency_ms": 141.0,
              "recovered_from": "",
              "batched": false
            },
            {
              "section_id": "s1",
              "metric_id": "funnel_order_revenue",
              "args_hash": "a45e5dd08d6682d0",
              "ok": true,
              "error_code": "",
              "rows": 11,
              "latency_ms": 1048.0,
              "recovered_from": "",
              "batched": false
            }
          ]
        },
        {
          "section_id": "s2",
          "status": "unsupported",
          "objective": "Top learnings and overall data summary",
          "acquired_metrics": [],
          "missing_metrics": [],
          "failed_metrics": [],
          "notes": [
            "no_query_steps"
          ],
          "attempts": []
        },
        {
          "section_id": "s3",
          "status": "complete",
          "objective": "Performance for Meta ads",
          "acquired_metrics": [
            "funnel_order_purchases",
            "funnel_order_purchases"
          ],
          "missing_metrics": [],
          "failed_metrics": [],
          "notes": [],
          "attempts": [
            {
              "section_id": "s3",
              "metric_id": "funnel_order_purchases",
              "args_hash": "bac6e3b368fe1495",
              "ok": true,
              "error_code": "",
              "rows": 5,
              "latency_ms": 762.0,
              "recovered_from": "",
              "batched": false
            },
            {
              "section_id": "s3",
              "metric_id": "funnel_order_purchases",
              "args_hash": "fac4fe322659cc8e",
              "ok": true,
              "error_code": "",
              "rows": 11,
              "latency_ms": 66.0,
              "recovered_from": "",
              "batched": false
            }
          ]
        },
        {
          "section_id": "s4",
          "status": "unsupported",
          "objective": "Top performer and net profit performer ranking",
          "acquired_metrics": [],
          "missing_metrics": [],
          "failed_metrics": [],
          "notes": [
            "no_template"
          ],
          "attempts": []
        },
        {
          "section_id": "s5",
          "status": "unsupported",
          "objective": "Net profit performer specifically",
          "acquired_metrics": [],
          "missing_metrics": [],
          "failed_metrics": [],
          "notes": [
            "no_template"
          ],
          "attempts": []
        }
      ],
      "query_calls": 4,
      "cache_hits": 0,
      "attempts": [
        {
          "section_id": "s1",
          "metric_id": "funnel_order_purchases",
          "args_hash": "fac4fe322659cc8e",
          "ok": true,
          "error_code": "",
          "rows": 11,
          "latency_ms": 141.0,
          "recovered_from": "",
          "batched": false
        },
        {
          "section_id": "s1",
          "metric_id": "funnel_order_revenue",
          "args_hash": "a45e5dd08d6682d0",
          "ok": true,
          "error_code": "",
          "rows": 11,
          "latency_ms": 1048.0,
          "recovered_from": "",
          "batched": false
        },
        {
          "section_id": "s3",
          "metric_id": "funnel_order_purchases",
          "args_hash": "bac6e3b368fe1495",
          "ok": true,
          "error_code": "",
          "rows": 5,
          "latency_ms": 762.0,
          "recovered_from": "",
          "batched": false
        },
        {
          "section_id": "s3",
          "metric_id": "funnel_order_purchases",
          "args_hash": "fac4fe322659cc8e",
          "ok": true,
          "error_code": "",
          "rows": 11,
          "latency_ms": 66.0,
          "recovered_from": "",
          "batched": false
        }
      ],
      "limitations": [
        "s2:no_query_steps",
        "s4:no_template",
        "s5:no_template"
      ]
    },
    "report_spec": {
      "sections": [
        {
          "id": "s1",
          "objective": "Full funnel diagnostic and drop-off analysis",
          "shape": "funnel",
          "metrics": [
            "funnel_order_purchases",
            "funnel_order_revenue"
          ],
          "intent": "funnel"
        },
        {
          "id": "s2",
          "objective": "Top learnings and overall data summary",
          "shape": "composition",
          "metrics": [
            "net_sales",
            "gross_profit",
            "net_profit"
          ],
          "intent": "kpis"
        },
        {
          "id": "s3",
          "objective": "Performance for Meta ads",
          "shape": "entity_comparison",
          "metrics": [
            "funnel_order_purchases",
            "funnel_order_revenue",
            "net_profit"
          ],
          "intent": "trend"
        },
        {
          "id": "s4",
          "objective": "Top performer and net profit performer ranking",
          "shape": "ranking",
          "metrics": [
            "funnel_order_purchases",
            "net_profit"
          ],
          "intent": "ranking"
        },
        {
          "id": "s5",
          "objective": "Net profit performer specifically",
          "shape": "ranking",
          "metrics": [
            "net_profit"
          ],
          "intent": "ranking"
        }
      ],
      "windows": [
        "event 2026-10-01..2026-10-11"
      ]
    }
  },
  "coverage": {
    "status": "partial",
    "sections": [
      {
        "section_id": "s1",
        "status": "complete",
        "objective": "Full funnel diagnostic and drop-off analysis",
        "acquired_metrics": [
          "funnel_order_purchases",
          "funnel_order_revenue"
        ],
        "missing_metrics": [],
        "failed_metrics": [],
        "notes": [],
        "attempts": [
          {
            "section_id": "s1",
            "metric_id": "funnel_order_purchases",
            "args_hash": "fac4fe322659cc8e",
            "ok": true,
            "error_code": "",
            "rows": 11,
            "latency_ms": 141.0,
            "recovered_from": "",
            "batched": false
          },
          {
            "section_id": "s1",
            "metric_id": "funnel_order_revenue",
            "args_hash": "a45e5dd08d6682d0",
            "ok": true,
            "error_code": "",
            "rows": 11,
            "latency_ms": 1048.0,
            "recovered_from": "",
            "batched": false
          }
        ]
      },
      {
        "section_id": "s2",
        "status": "unsupported",
        "objective": "Top learnings and overall data summary",
        "acquired_metrics": [],
        "missing_metrics": [],
        "failed_metrics": [],
        "notes": [
          "no_query_steps"
        ],
        "attempts": []
      },
      {
        "section_id": "s3",
        "status": "complete",
        "objective": "Performance for Meta ads",
        "acquired_metrics": [
          "funnel_order_purchases",
          "funnel_order_purchases"
        ],
        "missing_metrics": [],
        "failed_metrics": [],
        "notes": [],
        "attempts": [
          {
            "section_id": "s3",
            "metric_id": "funnel_order_purchases",
            "args_hash": "bac6e3b368fe1495",
            "ok": true,
            "error_code": "",
            "rows": 5,
            "latency_ms": 762.0,
            "recovered_from": "",
            "batched": false
          },
          {
            "section_id": "s3",
            "metric_id": "funnel_order_purchases",
            "args_hash": "fac4fe322659cc8e",
            "ok": true,
            "error_code": "",
            "rows": 11,
            "latency_ms": 66.0,
            "recovered_from": "",
            "batched": false
          }
        ]
      },
      {
        "section_id": "s4",
        "status": "unsupported",
        "objective": "Top performer and net profit performer ranking",
        "acquired_metrics": [],
        "missing_metrics": [],
        "failed_metrics": [],
        "notes": [
          "no_template"
        ],
        "attempts": []
      },
      {
        "section_id": "s5",
        "status": "unsupported",
        "objective": "Net profit performer specifically",
        "acquired_metrics": [],
        "missing_metrics": [],
        "failed_metrics": [],
        "notes": [
          "no_template"
        ],
        "attempts": []
      }
    ],
    "query_calls": 4,
    "cache_hits": 0,
    "attempts": [
      {
        "section_id": "s1",
        "metric_id": "funnel_order_purchases",
        "args_hash": "fac4fe322659cc8e",
        "ok": true,
        "error_code": "",
        "rows": 11,
        "latency_ms": 141.0,
        "recovered_from": "",
        "batched": false
      },
      {
        "section_id": "s1",
        "metric_id": "funnel_order_revenue",
        "args_hash": "a45e5dd08d6682d0",
        "ok": true,
        "error_code": "",
        "rows": 11,
        "latency_ms": 1048.0,
        "recovered_from": "",
        "batched": false
      },
      {
        "section_id": "s3",
        "metric_id": "funnel_order_purchases",
        "args_hash": "bac6e3b368fe1495",
        "ok": true,
        "error_code": "",
        "rows": 5,
        "latency_ms": 762.0,
        "recovered_from": "",
        "batched": false
      },
      {
        "section_id": "s3",
        "metric_id": "funnel_order_purchases",
        "args_hash": "fac4fe322659cc8e",
        "ok": true,
        "error_code": "",
        "rows": 11,
        "latency_ms": 66.0,
        "recovered_from": "",
        "batched": false
      }
    ],
    "
```

## Answer

Overall performance: net sales for the period is INR 966,373 and the funnel-level derivation (including the largest reported leakage) is 1,877 ratio.

Drop-off and daily funnel outcomes are provided in the tables below; daily purchase and revenue breakdowns are not available from certified evidence, not available from certified evidence and the platform-level sample is not available from certified evidence.

**Full funnel diagnostic and drop-off analysis**
- Funnel purchases (orders): 453 ratio
- Funnel revenue (orders): 850,368 ratio
- Funnel revenue (orders): 1,877 ratio

### Top learnings
- **Largest supported drop-off at Funnel revenue (orders)** (mathematical)
  - Limitation: Stage ratios only for compatible populations
  - Next check: Inspect filters and traffic sources on the leaky stage

**Coverage (unsupported)**
- no_query_steps

**Performance for Meta ads**
- {'ad_platform': 'meta', 'period_start': '2026-10-08'}: 40
- {'ad_platform': 'meta', 'period_start': '2026-10-03'}: 39
- {'ad_platform': 'meta', 'period_start': '2026-10-07'}: 38
- {'ad_platform': 'meta', 'period_start': '2026-10-06'}: 38
- {'ad_platform': 'meta', 'period_start': '2026-10-05'}: 35
- {'period_start': '2026-10-01'}: 41
- {'period_start': '2026-10-02'}: 19
- {'period_start': '2026-10-03'}: 53
- {'period_start': '2026-10-04'}: 48
- {'period_start': '2026-10-05'}: 52
- {'period_start': '2026-10-06'}: 56
- {'period_start': '2026-10-07'}: 54
- {'period_start': '2026-10-08'}: 58
- {'period_start': '2026-10-09'}: 26
- {'period_start': '2026-10-10'}: 27
- {'period_start': '2026-10-11'}: 19

| ad_platform | Funnel purchases (orders) |
|---|---|
| meta | 40 |
| meta | 39 |
| meta | 38 |
| meta | 38 |
| meta | 35 |

_Ranked by Funnel purchases (orders)_

**Top by Funnel purchases (orders)**
- {'ad_platform': 'meta', 'period_start': '2026-10-08'}: 40
- {'ad_platform': 'meta', 'period_start': '2026-10-03'}: 39
- {'ad_platform': 'meta', 'period_start': '2026-10-07'}: 38
- {'ad_platform': 'meta', 'period_start': '2026-10-06'}: 38
- {'ad_platform': 'meta', 'period_start': '2026-10-05'}: 35

**Coverage (unsupported)**
- no_template

**Coverage (unsupported)**
- no_template

### Coverage and limitations
- Server-built KPI/chart/table blocks were used; I returned narrative paragraphs and claims only and did not invent numbers.
- Some template and query-step items referenced in the skeleton are missing; this report is partial.
- s2:no_query_steps
- s4:no_template
- s5:no_template

Would you like a focused drop-off diagnosis for the Funnel revenue (orders) stage and traffic-source breakdown?
