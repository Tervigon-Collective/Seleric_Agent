# 23 - Failure Modes and Guardrails

| Failure mode | Guardrail |
|---|---|
| Flat swarm talks endlessly | task DAG + budgets + termination gate |
| Same data fetched repeatedly | evidence references + cache |
| Handoff ping-pong | leadership epoch + loop detector + coordinator arbitration |
| Conflicting metric definitions | canonical metric registry |
| LLM calls normal variation an anomaly | quantitative anomaly engine |
| Narrative root cause | causal hypothesis + tests + DoWhy/refutation |
| DoWhy misused | registered graph + explicit treatment/outcome/confounders |
| LLM invents forecast | model/baseline/insufficient fallback hierarchy |
| Strategy untethered from cause | intervention must reference validated mechanism |
| Agent spoofs capability | registry + authenticated identity + allowlists |
| Tool result prompt injection | untrusted-data handling + system/tool policy isolation |
| Stale evidence | freshness field + claim policy |
| Full history leaks between agents | context-minimized artifacts |
| LLM confidence inflation | derived trust score/labels |
