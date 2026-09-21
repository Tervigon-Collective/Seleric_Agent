# ADR-002 - LangGraph, A2A and MCP Boundaries

> **Superseded by `docs/CURRENT_ARCHITECTURE.md`** (V3 single-agent-loop
> migration) — LangGraph and A2A were removed; V3 is a single PydanticAI
> agent loop, with MCP (`seleric-mcp`) remaining the only agent/service-to-
> tool/data boundary. Kept as a historical decision record.

## Status
Accepted

## Decision

- LangGraph: internal orchestration/state.
- A2A: agent-to-agent boundary.
- MCP: agent/service-to-tool/data boundary.

## Consequence

Protocols have non-overlapping responsibilities and can evolve independently.
