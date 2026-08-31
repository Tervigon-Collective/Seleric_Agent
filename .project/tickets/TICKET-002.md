```yaml
id: TICKET-002
title: Ontology/State Week 1 live company-health payload
type: feature
status: ready
priority: critical
owner_agent: state-decision-agent
created: 2026-08-25
updated: 2026-08-25
depends_on: []
blocks: []
related_files:
  - 05_COMPONENT_CONTRACTS.md
  - 06_LOW_LEVEL_DESIGN_OOP.md
  - 08_ADMIN_AND_CONFIGURATION_CONTROL_PLANE.md
  - 14_DATA_MODEL_AND_PERSISTENCE.md
```

# Summary

Deliver the data-side half of the Week 1 gate: a config schema with a
publish lifecycle, an initial node/edge draft, at least one validated
certified MCP metric, and a live company-health payload with provenance.

# Context

Doc 10 marks Workstream 2 (Ontology, State, and Decision) the **critical
path**. This is the highest-priority Week 1 ticket.

# Requirements

- Create the config schema and publication lifecycle (draft → validate →
  approve → publish → rollback) per doc 08.
- Import an initial node/edge draft — use the "Initial Executive Node Set"
  in doc 10 §5 as the starting content, subject to admin approval.
- Validate against first certified MCP metrics — confirm the MCP catalogue
  version referenced in `00_README.md` §4 (`47f987dbb82d`) is still current
  before binding to it (see `BACKLOG.md` open question).
- Create `MetricState` and `FounderBrief` contracts per doc 06.
- Produce a live company-health payload end-to-end from MCP → state → API response.

# Acceptance Criteria

- [ ] Config draft/validate/approve/publish/rollback lifecycle works end-to-end
- [ ] Initial node/edge set imported and passes DAG/cycle validation (NetworkX, doc 04 §6)
- [ ] At least one certified MCP metric bound and queryable with provenance (query/config/version IDs)
- [ ] `GET /v1/executive/health` (doc 10 §3) returns live TH data, not fixtures
- [ ] 100% certified-metric usage — no unbacked numbers (doc 10 §8 acceptance metric)

# Technical Notes

PostgreSQL stores graph config; NetworkX executes it in memory (doc 04 §6)
— no graph database. Every feature/state row must carry `as_of_ts`, entity
keys, and config/version IDs for point-in-time reproducibility (doc 04 §5).

# Dependencies

Confirms the Seleric MCP catalogue is reachable and the referenced version
is current — blocking risk if metric contracts have drifted.

# Risks

"Existing metric can't support an intended insight" and "goal/owner
configuration incomplete" (see `RISKS.md`) are both live risks for this ticket.

# Validation

No test framework exists yet in this repo — establishing it (pytest +
fixtures for MCP responses) is in scope for this ticket.

# Work Log

(empty — not started)

# Completion Evidence

(empty — not started)
