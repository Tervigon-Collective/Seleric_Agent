# 13 — Failure Handling

Classify: `success` | `retryable_failure` | `blocking_failure` | `nonblocking_failure`.

Implemented in `coordinator/execution/retry.py` and applied by `Dispatcher`.

| Class | Examples | Action |
| --- | --- | --- |
| `retryable_failure` | timeout, unavailable, network, a2a_error | Retry up to `max_retries` |
| `blocking_failure` | missing_causal_graph, invalid_metric, schema/policy | Do **not** blind-retry; remediate or stop |
| `nonblocking_failure` | other soft errors | Continue mission; record limitation |

## Budget hard-stops (swarm_v2)

`check_swarm_budget` (`governance/budget.py`) stops DECIDE→EXECUTE waves when:

- `agent_calls >= max_agent_calls`
- leadership transfers ≥ `max_leadership_transfers`
- remediation rounds ≥ `max_remediation_rounds`
- `llm_calls >= max_llm_calls`

On exhaustion the graph emits `budget_exhausted`, skips further activations, and completion resolves to **`partial`** with reasons (never unbounded looping).
