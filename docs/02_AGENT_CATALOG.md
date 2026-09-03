# 02 - Agent Catalog

## Control agent

### Swarm Coordinator

Owns mission lifecycle, not domain conclusions.

Responsibilities:

- normalize query,
- classify intent,
- decompose into atomic questions,
- build dependency DAG,
- determine required evidence,
- select initial domain leader,
- activate minimum required specialists,
- watch loops/budgets,
- arbitrate handoff conflicts,
- enforce completion criteria,
- synthesize only validated outputs.

## Intelligence specialist agents

| Agent | Primary question | Must rely on |
|---|---|---|
| Observer | What is happening? | MCP + semantic metrics |
| Anomaly | What changed unusually? | Statistical/ML anomaly engine |
| Diagnostic | Why might it have changed? | Evidence + stats + causal service |
| Prediction | What happens if nothing changes? | Model registry + forecast service |
| Strategy | What interventions are available? | Validated causes + constraints + optimizer/rules |
| Skeptic | What could make this conclusion wrong? | Independent evidence/test paths |

## Domain agents

| Agent | Scope |
|---|---|
| Performance | Meta, Google Ads, campaign economics, CAC, CPM, CTR, CPC, ROAS |
| Commerce | Shopify, marketplaces, orders, products, pricing, discounts |
| Funnel | sessions, PDP, ATC, checkout, purchase, journeys, PostHog |
| Finance | revenue, COGS, margin, contribution, profit, cash |
| Inventory | stock, cover, ageing, stockout, replenishment risk |
| Procurement | PO, vendor, MOQ, lead time, inbound dependencies |
| Technical | latency, errors, deployments, APIs, incidents |

## Agent anti-patterns

Do not create a separate Observer/Anomaly/Diagnostic stack for every domain. Compose domain context with reusable intelligence specialists.
