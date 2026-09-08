# 45. Ticket Tracker — Robustness / Scalability / Production / Adaptability

> Live status register for the backlog defined in `44_ROBUSTNESS_SCALABILITY_ADAPTABILITY_BACKLOG.md`.
> That file holds the rationale/evidence per ticket and does not change once written;
> **this file is the one to edit** as work starts, moves, and finishes. Update the
> Status/Owner/Notes columns in place — do not duplicate rows.

Status legend (matches `SELERIC_SWARM_PROJECT_TASK_SHEET.md`): ⬜ NOT STARTED ·
🟦 IN PROGRESS · 🟨 REVIEW · 🟥 BLOCKED · 🟩 DONE · ⏸ DEFERRED · ❌ CANCELLED.
Priority: P0 blocking/critical · P1 required for MVP · P2 important post-MVP · P3 optimization/future.

Last updated: 2026-09-08.

## A. Robustness / correctness

| ID | Title | Priority | Status | Owner | Notes |
|---|---|---|---|---|---|
| ROB-001 | Fix hardcoded `direction_bad="up"` in real MCP provider | P1 | ⬜ NOT STARTED | — | |
| ROB-002 | Wire real observation data into swarm_v2 causal estimator | P1 | ⬜ NOT STARTED | — | |
| ROB-003 | Load-test multi-domain metric comparisons | P2 | ⬜ NOT STARTED | — | |
| ROB-004 | Resolve outstanding test failures from commit `0e10033` | P1 | ⬜ NOT STARTED | — | 6 failing as of 2026-09-08: see backlog doc for full list |
| ROB-005 | Per-mission LLM-call budget/circuit-breaker for orchestration loops | P2 | ⬜ NOT STARTED | — | |

## B. Scalability

| ID | Title | Priority | Status | Owner | Notes |
|---|---|---|---|---|---|
| SCL-001 | Make diagnostic ontology data-driven | P1 | ⬜ NOT STARTED | — | Same root fix as ADP-001 |
| SCL-002 | Move causal-graph wiring into config | P2 | ⬜ NOT STARTED | — | |
| SCL-003 | Bound and parallelize per-mission LLM call chain | P2 | ⬜ NOT STARTED | — | Same root cause as PRD-002 |
| SCL-004 | Formalize "add a new domain" checklist as tested workflow | P2 | ⬜ NOT STARTED | — | Blocked on SCL-001/002 |

## C. Production-grade hardening

| ID | Title | Priority | Status | Owner | Notes |
|---|---|---|---|---|---|
| PRD-001 | Retry/backoff + graceful degradation around external MCP dependency | P1 | ⬜ NOT STARTED | — | |
| PRD-002 | LLM-call cost/latency tracking + per-mission budget | P1 | ⬜ NOT STARTED | — | Same root cause as SCL-003 |
| PRD-003 | Close M12 observability gap (OTel/SLOs/deploy) | P2 | ⬜ NOT STARTED | — | Tracked in root task sheet as M12, already 🟦 IN PROGRESS there |
| PRD-004 | Require branch protection + CI status checks before merge | P1 | ⬜ NOT STARTED | — | Process change, not code |
| PRD-005 | Enforce numeric-fabrication audit on every LLM prose call site | P2 | ⬜ NOT STARTED | — | |

## D. Adaptability

| ID | Title | Priority | Status | Owner | Notes |
|---|---|---|---|---|---|
| ADP-001 | Diagnosability should scale with metric catalogue | P1 | ⬜ NOT STARTED | — | Same root fix as SCL-001 |
| ADP-002 | Confirm `ConstraintStore` is genuinely pluggable per tenant | P2 | ⬜ NOT STARTED | — | |
| ADP-003 | Audit Autonomy Levels 0-6 as config flip vs. rearchitecture | P2 | ⬜ NOT STARTED | — | |
| ADP-004 | Document vocabulary/language assumptions in prompts | P3 | ⬜ NOT STARTED | — | |

---

## How to update this tracker

- Flip a row's Status as work starts/finishes; fill Owner with the person's name/handle.
- Use Notes for blockers, PR links, or partial-completion detail — keep it to one line.
- When a ticket is done, leave the row in place with 🟩 DONE rather than deleting it —
  this file is the audit trail, not just a live view.
- If a ticket's scope changes materially, update the rationale in
  `44_ROBUSTNESS_SCALABILITY_ADAPTABILITY_BACKLOG.md` too, so the two files stay consistent.
