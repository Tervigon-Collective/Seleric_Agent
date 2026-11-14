# 15. Glossary

| Term | Meaning |
|------|---------|
| Artifact | A typed stored result (`v3_artifacts`, `artifacts`): evidence, forecast, causal and other payloads |
| Attempt | One lease-fenced execution try of a run (`run_attempts`) |
| Bundle (context) | History, memories and summary assembled for a run (`ContextBundle`) |
| Core | Seleric Agent Core, the MCP gateway the agent reads data through |
| Correction pass | The single re-run of the loop that lists exactly which figures failed verification |
| Domain health | Per-domain snapshot of selected metrics with judgement of the latest value |
| Evidence | A stored tool result row set (`EvidenceArtifact`) referenced by `query_id` |
| Grounding | The deterministic check that each figure in an answer is measured or derived from tool results |
| Lane / lease | A lease is the time-limited claim a worker holds on an attempt; renewed by heartbeat |
| Loop | The single PydanticAI agent run that answers one turn |
| Memory | A stored user, project, thread or episodic fact with consent state |
| Mission | One execution of the loop (legacy name); a run has one mission |
| Outbox | `submission_outbox` rows written with a submission so dispatch cannot be lost |
| Principal | The authenticated caller: workspace, user, roles, method |
| Run | The conversation-level record of one question's execution |
| Scratchpad | Per-run ledger of facts already established, rendered back into the prompt |
| Target (monitoring) | A floor, target and ceiling band for a metric |
| Trust gate | Rules that set aside days whose data events make a metric unjudgeable |
| Unverified note | Text appended to an answer when figures still fail verification after the correction pass |
