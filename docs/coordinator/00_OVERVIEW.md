# 00 — Coordinator Overview

The Coordinator is the **mission control plane** of the Seleric Intelligence Swarm.

It owns: intake, progressive problem decomposition, task DAGs, capability routing, leadership, artifact/claim governance, Skeptic remediation, completion, and synthesis.

It does **not** own business truth. Evidence owns truth. Domain and specialist agents own calculations, models, and diagnosis.

## Workflows

| Workflow | Role |
| --- | --- |
| `lookup_v1` | L0/L1 fast path (LangGraph) |
| `swarm_v2` | Coordinator control plane (L2+) |

Set `SWARM_WORKFLOW=lookup_v1|swarm_v2` (settings: `swarm_workflow`). Dispatch sends L0/L1 to lookup and L2+ to swarm.

## Package

`src/seleric_swarm/coordinator/` — contracts, intake, decomposition, planning, routing, leadership, artifacts, governance, synthesis, `graph.py` (swarm_v2).

See also [03_COORDINATOR_DESIGN.md](../03_COORDINATOR_DESIGN.md).
