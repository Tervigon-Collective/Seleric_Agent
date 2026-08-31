---
name: implement-service
description: Standard workflow for implementing or extending one of the six Seleric Voice Node services (voice-orchestrator, business-state-service, insight-decision-service, meeting-intelligence-service, control-plane-service, admin-ui) against its already-written contract. Use when a ticket says "implement X in service Y".
---

# Implement a service against its contract

This project's services are already fully specified in docs 05/06 — this
skill is about implementing against that spec, not designing it.

1. **Read the contract first.** Find the component in
   `05_COMPONENT_CONTRACTS.md` (purpose, inputs, outputs, config, failure
   behavior) and its class/API/schema in `06_LOW_LEVEL_DESIGN_OOP.md`. Do
   not start writing code before this — the design decisions are already made.
2. **Check the tech stack doc.** `04_TECH_STACK_AND_DEPLOYMENT_OPTIONS.md`
   pins exact libraries (FastAPI + Pydantic v2, SQLAlchemy 2 async +
   Alembic, Polars, etc.) — use what's specified, don't substitute.
3. **If this is the first code in the service**, establish minimal
   scaffolding only: package layout, FastAPI app entrypoint, SQLAlchemy
   base, Alembic init, a test runner (pytest). No premature abstraction
   beyond what the contract requires.
4. **Implement the narrowest slice the ticket actually asks for.** Don't
   implement adjacent contract sections "while you're in there" — that's a
   separate ticket.
5. **Every state/feature row must carry `as_of_ts`, entity keys, and
   config/version IDs** (doc 04 §5) if it touches business state.
6. **Write the one test that would fail if the logic broke** — golden
   utterance test, golden brief test, or an extraction regression case, per
   whichever acceptance metric in doc 10 §8 applies.
7. **Update the ticket's Work Log and Completion Evidence** — list files
   touched, commands run, and actual results. Don't claim "done" without
   evidence per `CLAUDE.md`'s Completion Policy.
