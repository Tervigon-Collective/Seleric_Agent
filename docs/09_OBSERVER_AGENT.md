# 09 - Observer Agent

## Question

**What is happening?**

## Responsibilities

- translate business questions into canonical metrics,
- request data through appropriate domain/MCP capabilities,
- normalize grain/timezone/entity semantics,
- calculate deterministic metrics through the metric service,
- create evidence artifacts,
- detect missing/stale/contradictory data,
- never invent missing values.

## Output

Observer returns an EvidenceBundle containing facts, not root-cause narratives.

## Failure behavior

If metric definition or data source is ambiguous, mark the evidence as blocked/ambiguous and return the ambiguity to the coordinator rather than silently selecting a definition.
