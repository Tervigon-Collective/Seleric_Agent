```yaml
id: TICKET-001
title: Voice/Conversation Week 1 vertical slice
type: feature
status: ready
priority: critical
owner_agent: voice-conversation-agent
created: 2026-08-25
updated: 2026-08-25
depends_on: []
blocks: [TICKET-002]
related_files:
  - 04_TECH_STACK_AND_DEPLOYMENT_OPTIONS.md
  - 05_COMPONENT_CONTRACTS.md
  - 06_LOW_LEVEL_DESIGN_OOP.md
```

# Summary

Stand up the Voice/Conversation half of the Week 1 gate: a Pi (or laptop
stand-in) can run through wake → transcript → Conversation API → response,
using two real intents.

# Context

Per `10_IMPLEMENTATION_PLAN_AND_ACCEPTANCE.md` §7 Week 1 and the overall
gate: "Laptop or Pi asks company health and receives live TH data with
provenance." This ticket covers the device/conversation side of that gate;
TICKET-002 covers the data side they integrate with.

# Requirements

- Flash raspOVOS, validate USB audio input/output on target hardware (or a
  laptop dev stand-in if hardware isn't ready yet — see `BACKLOG.md`).
- Create `SelericBridgeSkill` skeleton per doc 04 §3 (thin transcript/response
  bridge, no business reasoning on the edge).
- Train/test local "Hey Seleric" wake word (`ovos-ww-plugin-openwakeword`).
- Implement `POST /v1/conversations`, `POST /v1/conversations/{id}/utterances`,
  `GET /v1/conversations/{id}` per doc 10 §3, backed by the Voice
  Orchestrator Service.
- Implement two intents from the deterministic intent grammar (doc 05/06)
  sufficient to ask for company health.

# Acceptance Criteria

- [ ] Wake word triggers reliably in a quiet office test (informal — formal
      acoustic acceptance numbers are Week 2+, see doc 10 §8)
- [ ] Conversation API round-trips a real utterance to a real response
- [ ] Two intents resolve correctly against test utterances
- [ ] OVOS message bus confirmed localhost-only (doc 04 §3 — no auth on the bus by design)
- [ ] No business logic (metric selection, ranking) lives in `SelericBridgeSkill`

# Technical Notes

Provider abstraction for STT/TTS must be the `Protocol` interfaces in doc 04
§3 — config selects a registered provider ID, never an import path.

# Dependencies

None to start; integrates with TICKET-002's live company-health payload to
close the full Week 1 gate.

# Risks

Far-field/wake reliability (see `RISKS.md`).

# Validation

No test framework exists yet in this repo — establishing pytest (or
equivalent) for this service is in scope for this ticket, not assumed.

# Work Log

(empty — not started)

# Completion Evidence

(empty — not started)
