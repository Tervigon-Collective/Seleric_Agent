# AG-08 before — Full Funnel Diagnostic (1–11 October)

Recorded 2026-10-12 against live MCP (`eval/cassettes/reports.json`).

## Question

> I NEED Full Funnel Diagnostic (1-11th October). Drop Off Analysis, Top Learnings, Meta Ads, Top Performer and Net Profit Performer, and Overall Data.

`as_of`: 2026-10-12 · `V2_NO_FALLBACK=true`

## Outcome

| Field | Value |
|---|---|
| Route | `failed` |
| Fallback reason | `no_data` |
| Status | failed |
| Latency | 50250 ms |
| LLM calls | 1 |
| Tokens | 11893 |
| Evidence | none |

## QuestionSpec (v2)

```json
{
  "shape": "funnel",
  "measures": [
    "funnel_order_purchases",
    "funnel_order_revenue",
    "net_profit",
    "net_sales",
    "gross_profit"
  ],
  "entities": [],
  "windows": []
}
```

## Plan

- **template_id**: `funnel` (single template; Meta / rankings / overall collapsed away)
- **steps**: 9 (5 queries + 4 share derivations)

## Run

- Result sets: 5, all `unknown_window_role` (windows list was empty despite user naming 1–11 October)
- Derivations: 4 share ops → `unit_mismatch`, `zero_whole` ×3
- No campaign/ad rankings, no KPI cards, no charts, no coverage artifact

## Failure modes this assignment must fix

1. **Multi-objective collapse** — funnel + Meta ads + net-profit performers + overall → one `funnel` shape.
2. **Empty windows** — explicit Oct 1–11 not compiled onto the spec.
3. **Unsafe funnel shares** — order purchases/revenue mixed with P&L metrics; adjacent `share` without grain/view checks.
4. **All-or-nothing fallback** — partial evidence discarded; mission ends `no_data`.
5. **No structured report blocks** — title-only charts / no KPI grid even if data had landed.

## Cassette

`eval/cassettes/reports.json` (508 KB) — catalogue + metrics traffic for replay.
