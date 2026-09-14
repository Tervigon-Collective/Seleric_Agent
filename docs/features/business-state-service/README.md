# Business State Service (thin layer for Seleric_Agent)

**Status:** design / pre-implementation  
**Project:** `Seleric_Agent` (mission swarm) at `C:\SpacePeppers\SpacePeppers\Seleric_Agent`  
**Related blueprint:** Seleric Voice Node V1 (`business-state-service` microservice)  
**Decision:** fold a **thin in-process Business State layer** into the swarm — do **not** transplant the full Voice Node microservice.

## Documents in this folder

| Doc | Purpose |
|---|---|
| [01_ARCHITECTURE.md](01_ARCHITECTURE.md) | Role, bounded context, internal shape, APIs, data ownership (from Voice Node BSS, adapted) |
| [02_SELERIC_AGENT_INTEGRATION.md](02_SELERIC_AGENT_INTEGRATION.md) | How to plug into the swarm without Voice Node complexity |
| [03_DEFINITIONS_TO_MAKE_FUNCTIONAL.md](03_DEFINITIONS_TO_MAKE_FUNCTIONAL.md) | Contracts, profiles, series rules, wiring, and checklist before code |
| [04_DOMAIN_HEALTH_SNAPSHOTS.md](04_DOMAIN_HEALTH_SNAPSHOTS.md) | Per-domain health metrics, cron-resolved JSONB snapshots, overview-query answer path |
| [05_SPRINT_PLAN.md](05_SPRINT_PLAN.md) | Phased implementation plan, Sprint 0 → 6 |
| [06_DATA_VALIDATION_FINDINGS.md](06_DATA_VALIDATION_FINDINGS.md) | Live `seleric-mcp` check of the domain metrics in 04 — 4 corrections, rest confirmed |

## One-liner

Business State turns **certified Seleric MCP metrics** into **reproducible metric state** (features, optional anomaly/forecast, freshness, provenance). Agents consume evidence; Business State never chooses interventions.

## Golden rules

1. MCP only for production numbers — no raw warehouse SQL from agents.
2. Catalogue / binding miss = error, not silent fallback.
3. Missing / stale / sparse history → quality flags / `INSUFFICIENT_EVIDENCE`, never invent values.
4. LLM never is the number — it chooses what to ask; Business State computes.
5. Every output carries provenance into the Evidence Ledger.
6. Voice Node control plane, ClickHouse marts, outbox, and a separate deployable are **out of scope for V0**.
