```yaml
id: TICKET-003
title: Meeting schema and transcription spike
type: research
status: ready
priority: high
owner_agent: meeting-verification-agent
created: 2026-08-25
updated: 2026-08-25
depends_on: []
blocks: []
related_files:
  - 14_DATA_MODEL_AND_PERSISTENCE.md
  - 04_TECH_STACK_AND_DEPLOYMENT_OPTIONS.md
```

# Summary

Define meeting/audio/utterance/commitment/verification schemas, record one
internal sample meeting, and run a cloud + on-prem transcription spike to
de-risk Week 4 before committing to an adapter choice.

# Context

Doc 10 §7 Week 1 scopes this as a spike, not a full implementation — Week 4
is when Meeting Intelligence is actually built. The goal here is risk
reduction: confirm WhisperX/Faster Whisper + pyannote quality and latency
on real TH audio before the team commits.

# Requirements

- Define `meeting`, `audio_part`, `utterance`, `commitment`, `verification`
  schemas per doc 14.
- Record one internal one-to-one sample meeting (with consent).
- Run the sample through both a cloud transcription path and an on-prem
  WhisperX/Faster Whisper + pyannote path; compare quality/latency.

# Acceptance Criteria

- [ ] Schemas defined and reviewed against doc 14's current/history split, RLS, and retention rules
- [ ] One real recording captured with documented consent language
- [ ] Both transcription paths produce a transcript for the same sample
- [ ] Spike findings written up (quality, latency, cost) to inform the Week 4 adapter decision — add as an ADR in `DECISIONS.md` if it changes the doc 04 default

# Technical Notes

pyannote Community-1 requires accepting model conditions + a Hugging Face
token (doc 04 §13.5) — confirm access before the spike, not during.

# Dependencies

None.

# Risks

"Rule-based meeting extraction has low recall" and "GPU unavailable for
local transcription" (see `RISKS.md`) — this spike directly informs both.

# Validation

Spike — no production code path yet; findings document is the deliverable.

# Work Log

(empty — not started)

# Completion Evidence

(empty — not started)
