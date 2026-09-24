# Voice Agent (LiveKit) — planning

**Status:** planning only. No code exists yet. Nothing in this folder has been
built or verified against a running deployment.

**Goal:** speak a business question to Seleric and hear the answer, without
weakening the evidence/provenance guarantees that make the text answer
trustworthy.

**Decision in one line:** LiveKit runs a **separate, fast conversational voice
layer**; Seleric stays the slow, durable, evidence-bound mission engine and is
reached from that layer as an **async tool call**, never as the LLM node inside
the voice turn loop.

## Documents in this folder

| Doc | Purpose |
|---|---|
| [01_ARCHITECTURE.md](01_ARCHITECTURE.md) | Component shape, the latency mismatch and how we resolve it, narration, auth, data policy, config, testing |
| [02_PHASED_PLAN.md](02_PHASED_PLAN.md) | Phases 0 → 3 with a Definition of Done gate per phase, and the blockers found in the current code |

## Why this is not "just add a mic to the UI"

Three facts about the current system (`docs/CURRENT_ARCHITECTURE.md`) shape
every decision below:

1. **Missions are asynchronous and slow.** `MISSION_TIMEOUT_S=600`. The
   conversational path (`api/conversations.py`) returns `202 Accepted` and
   completes on a durable run queue. A voice turn must answer in well under a
   second.
2. **There is no token streaming.** `answer.delta` is declared in
   `CANONICAL_EVENT_TYPES` (`conversations/event_mapping_v1.py:43`) but nothing
   in `src/` emits it. Today an answer arrives as one finished message.
3. **Numbers are evidence-bound.** `REQUIRE_PROVENANCE_FOR_NUMERIC=true`, and
   every numeric claim maps to a traceable evidence id. Speech cannot carry an
   evidence id, so speech must not become the system of record.

The intended answer to (1) and (2) is to **narrate the wait aloud** from the
existing `ActivityEvent` stream, whose `title`/`summary` fields were already
written for UI display.

> **But that stream is not live yet.** Reading the code closely: `run.started`
> is emitted, the mission runs to completion, and only then is the entire event
> list replayed into the stream in one batch. Nothing arrives mid-mission. This
> is blocker **B7** in [02_PHASED_PLAN.md](02_PHASED_PLAN.md) and it is a hard
> prerequisite for Phase 1, not a nice-to-have. It is also a gap the typed
> Office UI already has — voice just makes it impossible to ignore.

## Golden rules for this feature

1. Voice is a **transport**, never an authority. The evidenced text answer in
   the thread stays the system of record.
2. The voice layer never invents a number. It speaks numbers only as they
   appear in a completed, validated `MissionResult`.
3. Every voice turn lands in the same thread/message/event tables as a typed
   turn — same workspace and owner scoping, same audit trail.
4. Raw audio is treated as conversation content under
   `docs/47_CONVERSATION_DATA_POLICY.md`, not as incidental telemetry.
5. The feature ships behind `VOICE_ENABLED=false` and is removable by flag.
