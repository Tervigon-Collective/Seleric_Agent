# 03 - Coordinator Design

The Coordinator is a control plane. Full Coordinator V1 documentation lives under
[`docs/coordinator/`](./coordinator/00_OVERVIEW.md).

## Coordinator is a control plane

The Coordinator should not compete with domain agents for analytical leadership.

## Mission planning flow

```text
User query
  -> normalize dates/entities/metrics
  -> classify query type
  -> decompose into atomic questions
  -> determine evidence requirements
  -> create task DAG
  -> select minimum agent set
  -> select initial mission lead
  -> execute
  -> refine decomposition on evidence / Skeptic
  -> complete when objectives + validation pass
```

## Query classes

1. **Lookup** - deterministic factual retrieval.
2. **Comparison** - compare periods/entities.
3. **Anomaly** - determine what changed abnormally.
4. **Diagnostic** - investigate why.
5. **Predictive** - estimate future outcome.
6. **Prescriptive** - recommend intervention.
7. **Compound** - requires multiple stages.

## Minimum-agent principle

Do not invoke the whole swarm for simple retrieval.

## Implementation

| Layer | Path |
| --- | --- |
| Control plane package | `src/seleric_swarm/coordinator/` |
| Lookup fast path | `orchestration/graph.py` (`lookup_v1`) |
| Swarm V1 (legacy) | `swarm/orchestrator.py` |
| Swarm V2 (default) | `coordinator/graph.py` |
| Policies | `config/coordinator_policies.yaml` |

`ControlPlane` is the deterministic facade for the lookup graph node.
The lookup TaskGraph is **authoritative** for routing (dispatchability) and
completion scoring (live task statuses). `swarm_v2` owns progressive
decomposition, targeted Skeptic remediation, claims, and completion for L2+ missions.

## Termination conditions

Mission can terminate when:

- requested questions are answered,
- required evidence is present,
- skeptic requirements are satisfied,
- unresolved uncertainty is explicitly represented,
- no blocking task remains,
- budget/latency thresholds have not forced a partial answer.
