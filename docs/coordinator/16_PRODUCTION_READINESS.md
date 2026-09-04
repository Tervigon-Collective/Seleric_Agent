# 16 — Production Readiness

## Migration

1. `swarm_workflow=swarm_v1` — legacy (preserved).
2. `swarm_workflow=swarm_v2` — Coordinator V1 (default after regression green).
3. `lookup_v1` remains L0/L1 fast path.

## Remaining gaps

- ~~Live DECIDE→EXECUTE scheduler not yet driving swarm_v2 LangGraph edges~~ **Done (v1.1)**
- ~~Conflict manager classification is structural~~ **Done (v1.2)**
- ~~HTTP A2A transport~~ **Done (v1.3)**
- ~~Lookup_v1 ControlPlane DAG remains advisory~~ **Done (v1.4)** — `route_from_plan` + live task status progression; see [04_TASK_DAG.md](./04_TASK_DAG.md)
- ~~Budget hard-stops not enforced in swarm_v2 loop~~ **Done (v1.5)** — `check_swarm_budget` gates execute/specialists/remediate; exhaustion → `partial` + `budget_exhausted` event; see [13_FAILURE_HANDLING.md](./13_FAILURE_HANDLING.md)
- ~~Structured mission events ad-hoc~~ **Done (v1.6)** — `MissionEventEmitter` + envelope + family taxonomy; see [14_OBSERVABILITY.md](./14_OBSERVABILITY.md)
- ~~Persistence incomplete for restart / event query~~ **Done (v1.7)** — full JSON + `mission_events` durability; `GET /v1/missions/{id}/events`; see [18_PERSISTENCE.md](./18_PERSISTENCE.md)
- Lookup_v1 now emits structured events (`mission_created`, `claim_validated`, `mission_completed`, handoffs) for the events API
- API edge hardening: reject empty query / unknown or missing `scenario_id` on swarm (400); Skeptic probe follow-ups classify as `hypothesis_test`; challenged missions always surface limitations
- ~~Health + forecast + action skipped diagnostic/skeptic; API could return `status: running`~~ **Done (v1.8)** — `executive_health` always implies `diagnostic`; intake/swarm intents merged; skeptic gates on diagnostic/predictive/prescriptive; terminal statuses never leak `running` (maps to `partial`); `full_*` flags also force matching specialist intents so Swagger defaults actually activate Prediction/Diagnostic
- ~~Creative decomposition matrix + plan DoD gaps~~ **Done (v1.9)** — lookup routing (`how many` / intake-aware); preserve `prototype_completed`; legacy “Root cause” synthesis gated; unresolved primary metric limitations; Phase 13 A–Q report in [19_FINAL_REPORT_AQ.md](./19_FINAL_REPORT_AQ.md)
- ~~LangSmith task-level metadata incomplete~~ **Done (v1.10)** — `coordinator_task_metadata` on activate + dispatcher task spans; workflow_version **1.4.0**
- ~~Swarm has no MCP data path (fixture-only)~~ **Done (v1.11)** — `HybridMcpDataProvider` + `execution_mode` staging/production prefer MCP for commerce/performance with fixture fallback; see [12_MCP_BOUNDARIES.md](./12_MCP_BOUNDARIES.md)
- ~~Long missions only synchronous~~ **Done (v1.12)** — `wait=false` accepts with `status=running` + background execution; poll `GET /v1/missions/{id}`
- ~~No API rate limit / readiness / optional auth~~ **Done (v1.13)** — sliding-window rate limit, optional `SELERIC_API_KEY`, `GET /readyz`; see [21_API_SECURITY.md](./21_API_SECURITY.md)
- ~~No request correlation / async cancel~~ **Done (v1.14)** — `X-Request-ID` middleware; `POST /v1/missions/{id}/cancel` for running async missions

## Checklist

- Progressive decomposition versioning
- Targeted Skeptic remediation
- Claim-aware synthesis (no validated language on CHALLENGED)
- Artifact dedup + synthetic taint
- Leadership hysteresis
- Completion gate objective-based
- API fields preserved (`route`, `mission_id`, `status`, leadership, artifacts, events, …)
- Live DECIDE→EXECUTE LangGraph cycle (`swarm_v2` workflow_version 1.4.0)
- Budget hard-stops on agent / leadership / remediation / LLM ceilings
- Structured mission event envelope + family coverage
- Durable mission payload + events API
- Intent alignment for executive_health + predictive + prescriptive (diagnostic always included)
- Terminal API status ∈ {completed, prototype_completed, partial, blocked, failed} only
- Lookup fast-path covers `how many` / intake lookup+comparison
- Phase 13 A–Q final report checked in
- Plan phases 0–13: **COMPLETE** (see [19_FINAL_REPORT_AQ.md](./19_FINAL_REPORT_AQ.md))
- LangSmith per-activation / per-task spans (`task_id`, `subquestion_id`, `active_specialist`, `remediation_round`, …)
- MCP-preferring hybrid providers via `execution_mode=staging|production`
- Async missions via `wait=false` (poll GET until terminal)
- Optional API key + per-client rate limit; `/readyz` dependency checks
- `X-Request-ID` on all responses; async mission cancel endpoint
