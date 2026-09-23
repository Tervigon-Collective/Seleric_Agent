# Business State Service (thin layer for Seleric_Agent)

**Status:** in progress — Sprints 0–5 done and live-verified (contracts,
facade MVP, anomaly detector, pluggable provider config, Domain Health
Snapshots for all 8 buildable domains + a cron-less scheduler entry point,
and the Coordinator overview answer path). Sprint 6 (hardening: caching,
per-domain profile overrides, multi-brand) is optional and not started. See
[05_SPRINT_PLAN.md](05_SPRINT_PLAN.md) for what's actually built vs. still
planned.
Code lives at `src/seleric_swarm/services/business_state/`.  
**Project:** `Seleric_Agent` (mission swarm) at `C:\SpacePeppers\SpacePeppers\Seleric_Agent`  
**Related blueprint:** Seleric Voice Node V1 (`business-state-service` microservice)  
**Decision:** fold a **thin in-process Business State layer** into the swarm — do **not** transplant the full Voice Node microservice.

## Documents in this folder

| Doc | Purpose |
|---|---|
| [01_ARCHITECTURE.md](01_ARCHITECTURE.md) | Role, bounded context, internal shape, APIs, data ownership (from Voice Node BSS, adapted) |
| [03_DEFINITIONS_TO_MAKE_FUNCTIONAL.md](03_DEFINITIONS_TO_MAKE_FUNCTIONAL.md) | Contracts, profiles, series rules, wiring, and checklist before code |
| [04_DOMAIN_HEALTH_SNAPSHOTS.md](04_DOMAIN_HEALTH_SNAPSHOTS.md) | Per-domain health metrics, cron-resolved snapshots (JSON files for now, Postgres JSONB later), overview-query answer path |
| [05_SPRINT_PLAN.md](05_SPRINT_PLAN.md) | Phased implementation plan, Sprint 0 → 6 |
| [06_DATA_VALIDATION_FINDINGS.md](06_DATA_VALIDATION_FINDINGS.md) | Live `seleric-mcp` check of the domain metrics in 04 — 4 corrections, rest confirmed |

## Diagrams

`diagrams/final_architecture.mmd`, `leadership_handoff.mmd`, and
`mission_lifecycle.mmd` (swarm_v2-era diagrams this section used to
reference) were removed in the V3 cleanup — see `diagrams/new.mmd` for the
current target architecture and `diagrams/evidence_flow.mmd` (still
current) for the `Business State` / `Evidence Map` path alongside the raw
MCP → Normalizer path.

## V3 integration

`02_SELERIC_AGENT_INTEGRATION.md` (wired to the retired swarm_v2 coordinator/
domain/specialist structure) was removed. The real integration today: the
V3 agent's `toolsets/analytics.py` calls into
`services/business_state/detectors.py` (e.g. `RobustZScoreDetector`) for
anomaly detection, consuming `MetricReading`/`AnomalyFinding` types from
`swarm/providers/base.py`. See `docs/CURRENT_ARCHITECTURE.md`'s agent-loop
section for how toolsets are wired onto the agent.

## One-liner

Business State turns **certified Seleric MCP metrics** into **reproducible metric state** (features, optional anomaly/forecast, freshness, provenance). Agents consume evidence; Business State never chooses interventions.

## Golden rules

1. MCP only for production numbers — no raw warehouse SQL from agents.
2. Catalogue / binding miss = error, not silent fallback.
3. Missing / stale / sparse history → quality flags / `INSUFFICIENT_EVIDENCE`, never invent values.
4. LLM never is the number — it chooses what to ask; Business State computes.
5. Every output carries provenance into the Evidence Ledger.
6. Voice Node control plane, ClickHouse marts, outbox, and a separate deployable are **out of scope for V0**.
