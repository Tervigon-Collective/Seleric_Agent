# 03 - Coordinator Design

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

Example:

```text
"Sales yesterday?"
Coordinator -> Commerce -> Observer -> Claim Gate -> Response
```

Example:

```text
"Why did CAC increase and what should we do?"
Coordinator -> Performance lead -> Observer -> Anomaly -> Diagnostic
            -> possible handoff -> Prediction -> Strategy -> Skeptic
```

## Implementation (V1) - `seleric_swarm.coordinator`

The coordinator is a control plane, not the smartest agent. The LLM entry point
stays a single call (`agents/coordinator.py::Agent.classify`); everything else is
deterministic and unit-tested with no LLM/MCP access:

| Module | Plane | Responsibility |
| --- | --- | --- |
| `planning/complexity.py` | Planning | L0-L5 band from the classified query |
| `planning/dag_builder.py` | Planning | Typed `Task` DAG with capabilities, metric reads, dependencies |
| `routing/capability_resolver.py` | Control | Capability -> registry agents; flags which are wired |
| `routing/dispatchability.py` | Governance | Task is executable only if capability -> wired agent AND metric data path is live |
| `routing/agent_selector.py` | Control | `AgentScore` weighting (spec sec. 14) as a pure function |
| `leadership/lead_selector.py` | Control | `LeadScore` check on the classifier's `domain_lead` |
| `governance/budget.py` | Governance | LLM/tool ceilings (legacy `_budget_ok` semantics) + iteration/transfer hard stops |
| `governance/completion.py` | Governance | Weighted completion score -> finish / review / continue |
| `evidence/gap_detector.py` | Governance | Emits gaps only for capabilities that pass dispatchability |
| `plane.py` | - | `ControlPlane` facade the graph's coordinator node calls |

`ControlPlane` attaches `complexity`, `task_graph`, `lead_selection`,
`plan_dispatchable`, `completion_score` and `unresolved_questions` to
`MissionState`. Unreachable analytical work (stub agents, missing MCP data
paths) is planned and marked `blocked` with a specific reason rather than
dispatched, so `finalize_unsupported` can explain exactly what a question needs.

The plan is **advisory in V1**: `graph.V1_SUPPORTED` remains the routing
authority and `route_after_coordinator` is unchanged. The DAG builder's
dispatchability verdict and the graph's routing are two independently derived
answers; neither is made authoritative over the other until the live
DECIDE -> EXECUTE cycle exists.

## Termination conditions

Mission can terminate when:

- requested questions are answered,
- required evidence is present,
- skeptic requirements are satisfied,
- unresolved uncertainty is explicitly represented,
- no blocking task remains,
- budget/latency thresholds have not forced a partial answer.
