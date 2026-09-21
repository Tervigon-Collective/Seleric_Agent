# ADR-001 - Separate Intelligence Roles from Business Domains

> **Superseded by `docs/CURRENT_ARCHITECTURE.md`** (V3 single-agent-loop
> migration) — the specialist/domain axis split this ADR records was
> reversed: V3 uses one agent with capability toolsets instead. Kept as a
> historical decision record.

## Status
Accepted

## Decision

Use reusable intelligence specialists (Observer, Anomaly, Diagnostic, Prediction, Strategy, Skeptic) combined dynamically with domain agents.

## Consequence

Avoids multiplying each analytical role by every domain and keeps capability contracts reusable.
