# Project State

**Last updated:** 2026-08-25
**Current milestone:** M0 — Week 1 (Platform contracts and live data slice)
**Overall status:** Pre-implementation. Spec baseline is complete and locked
(15 docs + diagrams). No source code exists yet in this repository.

## Current goal

Stand up the Week 1 vertical slice per `10_IMPLEMENTATION_PLAN_AND_ACCEPTANCE.md`:
a laptop or Pi can ask company health and receive live Tilting Heads data
with provenance, across all three workstreams simultaneously.

## Active tickets

None started. See `tickets/` — TICKET-001 through TICKET-003 are READY
(Week 1 deliverables per workstream) but not yet IN_PROGRESS.

## Blockers

None recorded yet — nothing has been attempted.

## Recently completed

- Architecture blueprint (docs 00–14 + diagrams) — done before this session.
- Claude project infrastructure (CLAUDE.md, `.project/`, agents, skills) —
  this session, 2026-08-25.

## Important architectural state

- No repo scaffolding (no `pyproject.toml`, no service directories, no CI)
  exists yet. The first ticket to touch code will also need to establish
  the Python project layout per doc 04/06 — this is not yet decided in
  detail (single monorepo vs. per-service repos is NOT specified in the
  docs; check with the user before choosing).
- Seleric MCP catalogue version referenced by the design: `47f987dbb82d`
  (see `00_README.md` §4). Verify this is still current before building the
  MCP adapter — metric catalogues drift.

## Next recommended action

See `PROGRESS.md` → "Next Up". Before writing any code, resolve the open
question above (monorepo vs. per-service repo layout) since it affects
every subsequent ticket.
