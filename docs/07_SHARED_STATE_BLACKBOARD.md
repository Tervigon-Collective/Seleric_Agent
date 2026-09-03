# 07 - Mission Blackboard and Shared State

## Why a blackboard

Agents should coordinate through structured mission state instead of repeatedly copying conversational history.

## Core objects

- Mission
- Task
- EvidenceArtifact
- AnomalyArtifact
- Hypothesis
- CausalResult
- ForecastArtifact
- StrategyArtifact
- SkepticFinding
- LeadershipTransfer
- Claim

## Blackboard sections

```yaml
mission:
  id: M-...
  question: ...
  normalized_question: ...
  status: running

routing:
  mission_lead: performance_agent
  active_specialist: diagnostic_agent
  leadership_epoch: 2

tasks: []
evidence: []
anomalies: []
hypotheses: []
causal_results: []
forecasts: []
strategies: []
skeptic_findings: []
claims: []

budgets:
  max_agent_turns: 30
  max_handoffs: 8
  max_runtime_seconds: 120
```

## Storage

Use durable transactional storage for mission/event data. Redis may be used for ephemeral coordination, locks and caching; it should not be the sole audit store.
