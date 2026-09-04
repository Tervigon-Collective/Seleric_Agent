# 05 — Agent Routing

Specialist activation matrix (`config/coordinator_policies.yaml`):

| Band | Required |
| --- | --- |
| LOOKUP / COMPARISON | observer |
| ANOMALY | observer + anomaly |
| DIAGNOSTIC | observer + anomaly + diagnostic + skeptic |
| PREDICTIVE | observer + prediction + skeptic |
| PRESCRIPTIVE | strategy + skeptic (+ optional diagnostic/prediction) |

`AgentInvoker` Protocol: `LocalAgentInvoker`, `A2AAgentInvoker`.

Domain axis ≠ specialist axis. `mission_lead` ≠ `active_specialist`.
