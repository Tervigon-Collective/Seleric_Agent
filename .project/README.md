# Project management system

Lightweight tracker for the Seleric Voice Node V1 build. Read order for a
fresh session: `CLAUDE.md` (root) → `STATE.md` → `PROGRESS.md` →
`HANDOFF.md` → active ticket → source.

- `STATE.md` — what's true right now
- `PROGRESS.md` — ticket dashboard
- `ROADMAP.md` — the five-week plan from doc 10, as milestones
- `BACKLOG.md` — actionable findings not yet tickets
- `DECISIONS.md` — ADRs for decisions made *during implementation* (the
  pre-implementation architecture decisions already live in
  `01_OVERENGINEERING_AND_REUSE_REVIEW.md` — don't duplicate them here)
- `RISKS.md` — mirrors doc 10 §11 risk register, updated as risks materialize/resolve
- `HANDOFF.md` — end-of-session continuity notes
- `tickets/` — one file per ticket, `TICKET-NNN.md`

This system starts nearly empty because implementation hasn't started. Don't
pad it with speculative tickets — add a ticket when work is actually about
to begin.
