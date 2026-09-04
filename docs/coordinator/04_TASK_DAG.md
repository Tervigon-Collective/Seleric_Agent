# 04 — Task DAG

`SubQuestion` → `TaskSpec` via `planning/mission_planner.py` (swarm_v2).

Lookup_v1 uses `coordinator.models.Task` / `TaskGraph` from `planning/dag_builder.py`.

## Authority

| Workflow | DAG role |
| --- | --- |
| `lookup_v1` | **Authoritative** — `route_from_plan` requires `plan_dispatchable`; task statuses advance on observe / claim_gate / synthesize (`execution/lookup_dag.py`) |
| `swarm_v2` | Progressive decomposition → TaskSpec waves |

Three separate components:

1. **Problem Decomposer** — WHAT questions
2. **Task DAG Builder** — HOW/WHEN
3. **Capability Resolver** — WHO

Tasks carry capabilities, dependencies, dispatchability, and status.
