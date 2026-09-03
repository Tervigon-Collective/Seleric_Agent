# 21 - Deployment Architecture

## Start logically distributed, physically simple

Avoid one microservice per agent in the first release.

Recommended initial deployments:

```text
swarm-core
  coordinator
  intelligence specialists
  LangGraph orchestration

domain-agents
  domain agent implementations

model-service
  anomaly and prediction models

causal-service
  DoWhy and causal registry

data-gateway
  MCP clients, auth, normalization

state-service
  mission/evidence persistence
```

## Scale-out criteria

Split an agent/service when one or more becomes true:

- separate security boundary,
- different scaling profile,
- different team ownership,
- independent release cadence,
- model/GPU isolation,
- external A2A interoperability requirement.

## Environments

- local
- development
- staging/replay
- production

Production writes remain disabled until a separate action-governance milestone.
