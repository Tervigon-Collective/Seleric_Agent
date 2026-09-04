# 43 — Architecture settings validation

Validates current Seleric Agent settings against the canonical architecture
diagram in `docs/diagrams/two_axis_swarm_architecture.mmd` (same source as
`diagrams/final_architecture.mmd`).

## How ownership / handoffs stay automatic

Add a metric to `config/metric_registry.yaml`:

```yaml
- id: metric.example
  domain: attribution   # → attribution_agent owns it
  frontier: false       # optional: outcome KPI (RCA prefers these as bridge symptoms)
```

Then without code changes:

- `owned_metrics` / `probe_metrics` / `frontier_metrics` refresh via `ids_for_domain`
- `handoff_targets` = every other agent in `DOMAIN_WIRING`
- `evaluate_handoff` routes to `{domain}_agent` from metric ownership

## Verdict

**Core architecture matches.** Eleven domain agents are now in swarm `DOMAIN_WIRING`
(seven classic + attribution / product / customer / operations).

## Domain agent matrix

| Agent | Registry `enabled` | Swarm wired | `seleric_module` | Example owned metrics |
| --- | --- | --- | --- | --- |
| performance | true | Yes | paidmedia | cac, spend, cpm, cpc, ctr |
| attribution | true | Yes | attribution | attributed_net_revenue (`frontier: false`) |
| funnel | true | Yes | webanalytics | sessions, rates, purchase_cvr |
| technical | false | Yes | — | LCP, JS/API errors (terminal) |
| commerce | true | Yes | commerce | net/gross sales, orders, return_rate |
| product | true | Yes | product | units_sold |
| customer | true | Yes | customer | repeat_rate (`frontier: false`) |
| operations | true | Yes | operations | refunded_amount_excl_tax |
| finance | true | Yes | finance | net_profit, net_profit_shopify |
| inventory | false | Yes | — | none yet |
| procurement | false | Yes | — | none yet |

## Partial / next

- A2A HTTP Agent Cards, anomaly model router, feature store — not fully live
- Inventory / Procurement / Technical need MCP modules before `enabled: true`
