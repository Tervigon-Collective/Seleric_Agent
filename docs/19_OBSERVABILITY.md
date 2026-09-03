# 19 - Observability

## Every mission should trace

- mission creation,
- query normalization,
- task DAG,
- agent activation,
- tool/MCP calls,
- model calls,
- evidence creation,
- handoffs,
- causal runs,
- skeptic verdicts,
- completion decision,
- final claim references.

## Metrics

- mission latency,
- time to first evidence,
- agent turns per mission,
- handoffs per mission,
- loop rate,
- MCP failure rate,
- model failure/fallback rate,
- unsupported-claim rejection rate,
- skeptic overturn rate,
- cost per mission,
- cache hit rate,
- evidence freshness,
- user correction rate.

## Trace correlation

Use `mission_id`, `task_id`, `agent_id`, `evidence_id` and `trace_id` consistently across services.

## LangSmith run tree (V1)

Set `LANGSMITH_TRACING=true` + `LANGSMITH_API_KEY`. Every `traced_span` records
`inputs` and, via `SpanHandle.set_outputs(...)`, `outputs` - so the coordinator's
reasoning is visible, not just span names:

```
mission.lookup_v1                     inputs: query / timezone / as_of
└─ node.coordinator                   outputs: query_class, mission_lead, complexity, route_hint
   ├─ coordinator.classify            outputs: query_class, metric_hints, entities, time_range
   └─ coordinator.plan                outputs: complexity, lead_selection, decomposed_questions, dag_tasks, blocked_reasons
      ├─ coordinator.plan.task.T1     inputs: question/deps/capabilities/metric_ids  outputs: assigned_agent, dispatchable, blocked_reason
      ├─ coordinator.plan.task.T2     …one span per decomposed question
      └─ coordinator.plan.task.T-gate
   node.performance / node.commerce   outputs: metric_id, handoff_needed_metrics
   node.observer                      outputs: evidence_count, evidence_refs, status
   └─ tool.mcp.<capability>           inputs: capability/time_range  outputs: evidence_rows[value,day], mcp_called, missing
   node.leadership_transfer           outputs: pending_transfer (from/to/reason/evidence_refs/unresolved_question)
   node.coordinator_arbitrate         outputs: accepted, new_mission_lead, leadership_epoch  (or reject reason)
   node.claim_gate                    outputs: gate, claims[text,gate_status,support_refs]
   node.synthesizer                   outputs: final_response, limitations
   node.finalize                      outputs: status, completion_score, completion_components, unresolved_questions
```

Filter a project by `metadata.mission_id` to pull one mission's whole tree.

With `LANGSMITH_TRACING=true`, LangGraph also auto-instruments each node as its
own run (named by node key: `coordinator`, `observer`, ...). Those appear
alongside the explicit `node.*` / `coordinator.*` spans, which carry the
inputs/outputs. This is expected; the `node.*` spans are the ones to read.
