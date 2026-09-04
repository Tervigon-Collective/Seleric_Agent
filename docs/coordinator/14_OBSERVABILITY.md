# 14 — Observability

LangSmith spans include: mission_id, request_id, session_id, task/subquestion/decomposition ids + version, agent id/version, mission_lead, active_specialist, leadership_epoch, remediation_round, artifact refs, synthetic, workflow_version.

`swarm_v2` (workflow_version **1.4.0**) opens child spans for every specialist activation (`swarm.activate.{agent_id}`) and DAG task dispatch (`swarm.task.{agent_id}`) via `coordinator_task_metadata`.

## Structured events (`coordinator/observability/events.py`)

Every control-plane event carries an envelope:

| Field | Meaning |
| --- | --- |
| `kind` | Canonical event name |
| `ts` | ISO-8601 UTC |
| `seq` | Monotonic per-mission sequence |
| `mission_id` | Owning mission |
| `workflow_name` / `workflow_version` | e.g. `swarm_v2` / `1.4.0` |
| `family` | One of mission, decomposition, task, artifact, leadership, claim, skeptic, remediation |

### Families

- `mission_*` — created, completed, partial, budget_exhausted, control_plane
- `decomposition_*` — created, refined
- `task_*` — plan_created, wave_executed, specialists_activated
- `artifact_*` — posted/updated/discarded (Blackboard)
- `leadership_*` — transfer, rejected
- `claim_*` — proposed, validated, challenged, rejected
- `skeptic_*` — pass / revise / reject / gate
- `remediation_*` — planned, activated, round_done

`MissionEventEmitter` aliases legacy kinds (`decide_execute_wave` → `task_wave_executed`, etc.) and records `legacy_kind` when remapped.
