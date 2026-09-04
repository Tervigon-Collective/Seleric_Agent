# 09 - Failure Modes (spec sec. 53)

The Skeptic degrades; it never silently accepts an unsupported claim and never
fabricates.

| Situation | Behaviour |
| --- | --- |
| Evidence unavailable (required) | `EvidenceValidator` -> blocking `EvidenceGap` -> **REVISE** (or REJECT if the claim type requires evidence and none/no typed artifact exists) |
| Registry unavailable (metric/model/graph) | relevant validator marks the check `UNAVAILABLE`; validity is **not** assumed; verdict -> at best REVISE |
| Model metadata missing | `MODEL_METADATA_INCOMPLETE` -> forecast cannot exceed WEAK trust -> **REVISE** |
| Causal graph unavailable | `graph_ok=False` -> causal claim downgraded (`PLAUSIBLE_CAUSAL` / `ASSOCIATION_ONLY`) |
| DoWhy / causal service unavailable | `UnavailableCausalValidationService` -> `available=False`, `ASSOCIATION_ONLY`, methodological issue; **no fake pass** |
| LLM (reasoning model) failure | deterministic validators + verdict still run; `explanation` falls back to a templated summary; a limitation is added |
| Malformed upstream artifact | `_run_validator` catches the exception -> `ValidatorOutcome(status="UNAVAILABLE")` + methodological issue -> REVISE/REJECT by severity |
| A validator raises | isolated by `graph._run_validator`; the run completes with that validator `UNAVAILABLE` |

## Budgets & termination (spec sec. 49-50)

`config/skeptic_policies.yaml -> skeptic.budgets`:
`max_alternative_hypotheses`, `max_challenges`, `max_followup_rounds`,
`max_parallel_checks`, `max_validator_calls`, `max_llm_calls`,
`max_runtime_seconds`.

- alternative generation, challenge plan and validator selection are all capped.
- the graph is acyclic - there is no "request more evidence forever" loop; the
  Coordinator owns re-submission and enforces `max_followup_rounds`.
- gap ranking uses `Priority ~ EIG * (0.5 + impact) / cost` so the highest-value
  gap is surfaced first (`evidence_gaps.py`).

## Idempotency (spec sec. 54)

- `FollowUpTask.task_id = sha1(mission_id | claim_id | capability | question)` -
  re-running the Skeptic on the same claim yields the same task ids, so the
  Coordinator dedupes.
- With no reasoning model the whole run is deterministic
  (`tests/skeptic/test_skeptic_agent.py::test_14_...` asserts identical
  `verdict` + `trust_score` across two runs).
