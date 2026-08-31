# Backlog

Actionable items not yet promoted to tickets. Empty except for genuine open
questions found during spec review — do not pad with speculative work.

## Open questions blocking Week 1 start

- **Repo layout**: monorepo (all 6 services in one repo, shared libs) vs.
  per-service repos. Not specified in docs 03/04/06. Affects CI, versioning,
  and the very first scaffolding ticket. Ask the user before TICKET-001/002/003 start.
- **MCP catalogue currency**: design references Seleric MCP catalogue
  version `47f987dbb82d` (`00_README.md` §4). Confirm this is still the
  live catalogue before building the adapter — metric IDs/definitions may
  have changed since the blueprint was written.
- **Hardware kits**: doc 10 Week 1 says "assemble or order two hardware
  kits" (Pi 5 + ReSpeaker XVF3800 array) — physical procurement, not a
  coding task, but blocks the voice workstream's hardware validation step.
