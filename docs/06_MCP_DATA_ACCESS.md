# 06 - MCP Data and Tool Access

## Principle

MCP is the governed data/tool access plane. Business data retrieval should happen through MCP-capable domain services or an MCP gateway, not through arbitrary direct connector logic inside prompts.

## Access pattern

```text
Agent
  -> capability request
  -> MCP gateway
  -> allowed server/tool
  -> result
  -> normalization
  -> evidence artifact
  -> evidence ledger
```

## Recommended ownership

- Performance agent: ad platform MCP capabilities
- Commerce agent: ecommerce/marketplace MCP capabilities
- Funnel agent: analytics/PostHog MCP capabilities
- Finance agent: accounting/finance MCP capabilities
- Inventory/Procurement agents: WMS/ERP capabilities
- Technical agent: observability/log capabilities

Observer may request data through domain agents or through a read-only aggregator capability.

## Tool output requirements

Every MCP result converted into evidence must capture:

- source/server,
- tool name,
- tool version when available,
- query/arguments hash,
- requested time range,
- timezone,
- entity/dimensions,
- row count,
- freshness timestamp,
- calculation lineage,
- retrieval timestamp.

## Write tools

Disable by default. Write actions require explicit policy, approval, dry-run and rollback controls.
