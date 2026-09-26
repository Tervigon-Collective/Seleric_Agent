# Voice Agent — Architecture

**Status:** proposed. Verified against the repo as of 2026-09-24; the LiveKit
API surface in §11 was verified against `livekit/agents` upstream on the same
date.

## 1. Component shape

```text
 Browser / phone                LiveKit SFU              seleric-voice worker         Seleric API
 ──────────────                 ───────────              ────────────────────         ───────────
 mic ──WebRTC audio────────►    room  ────────────►      AgentSession                 POST /v1/threads/{id}/messages
                                                          STT → voice LLM → TTS       GET  /v1/runs/{id}/events/stream
 speaker ◄──WebRTC audio───     room  ◄────────────      (fast, conversational)       POST /v1/runs/{id}/cancel
                                                              │                       POST /v1/voice/token  (new)
 transcript panel ◄── existing SSE ────────────────────────── │ ───────────────────►  (same durable mission path)
                                                    ask_seleric() tool → mission
                                                    narrator ← ActivityEvent stream
```

Four processes, three of which already exist:

| Process | New? | Role |
|---|---|---|
| `seleric-api` (FastAPI) | no | Unchanged mission/conversation surface, plus one new token-mint route |
| `seleric-recover` | no | Unchanged durable run worker — actually executes the mission |
| LiveKit SFU | yes | Audio transport. Cloud or self-hosted (see §9) |
| `seleric-voice` worker | yes | LiveKit agent process: turn-taking, STT/TTS, narration, mission tool call |

The voice worker lives at `src/seleric_swarm/voice/` and is deployed the way
`seleric-recover` already is: a long-lived non-HTTP worker with its own console
script and compose service.

## 2. The central decision: two loops, not one

A LiveKit `AgentSession` assumes a sub-second turn loop — the user stops
speaking, the LLM replies, TTS speaks. Seleric's mission loop is bounded by
`MISSION_TIMEOUT_S=600`, runs on a durable lease-based queue, and is built for
auditability rather than latency. **These cannot be the same loop.**

So:

- **Voice LLM (fast loop).** A small, cheap model owns the conversation:
  greeting, disambiguation, confirming what was heard, small talk, reading back
  a finished answer. It holds no analytical authority and has no Cube access.
- **Seleric mission (slow loop).** Invoked by the voice LLM through a single
  `function_tool` — `ask_seleric(question)` — which submits to the existing
  conversation endpoint and returns immediately with a `run_id`.
- **Narrator (bridge).** While the mission runs, a background task consumes
  `GET /v1/runs/{run_id}/events/stream` and speaks selected
  progress lines. On `run.completed` it speaks the answer summary.

This is the same split the repo already draws between "the LLM decides what to
investigate" and "the engines produce the evidence". The voice LLM is one more
non-authoritative decider.

### Rejected: speech-to-speech (Realtime API) as the primary path

A single speech-to-speech model would be lower latency for chit-chat, but:

- it produces no reliable intermediate text, and the evidenced text answer must
  still be persisted to the thread — we would end up transcribing anyway;
- it bypasses the Azure → Azure-2 → OpenRouter fallback chain that
  `.env.example` and `agent/model.py` already maintain;
- provider choice is narrow, which conflicts with the data posture in §7.

**Decision: STT → LLM → TTS pipeline.** Revisit in Phase 3 only if measured
turn latency turns out to be the top complaint.

## 3. Narration — what makes a 60-second wait tolerable

> **Update 2026-09-25 — implemented.** `voice/worker.py` now narrates from
> `agent.tool_started` / `agent.answering` events on the run SSE stream
> (`narration_line`, `VoiceTurnRunner`), speaks only `summary`, and speaks a
> fixed refusal for any `run.failed` (never the unvalidated text). The voice LLM
> (`ask_seleric` / `cancel_seleric` tools, `VOICE_INSTRUCTIONS`) and semantic turn
> detection are in. The note below describes the earlier state.
>
> **Prerequisite, verified in code (historical).** Narration as designed does not work
> today, because **there is nothing to narrate while the mission runs.**
> `api/conversations.py` emits `run.started`, then `await run_mission_job(...)`
> runs to completion, and only *then* does `sink.ingest_mission_events(...)`
> replay the mission's whole event list in one batch
> (`api/conversations.py:1179`, `:1194`, `:1223`). The SSE stream is therefore
> silent for the entire mission and floods at the end. Blocker **B7** in
> `02_PHASED_PLAN.md` covers this; incremental emission is a Phase 1
> prerequisite, not an optimisation. Everything below describes the target
> behaviour once events stream as they happen.

`ActivityEvent` carries `title`, `summary`, `event_type` and `actor_id` as
top-level fields written to be shown to a person. We reuse them.

**The vocabulary below is illustrative, not verified.** The canonical type list
lives in `conversations/event_mapping_v1.py`, whose own docstring scopes it to
*legacy* mission kinds — it normalizes old persisted swarm-era records, and it
is also what `ingest_mission_events` runs the current mission's event list
through. `docs/CURRENT_ARCHITECTURE.md` §8 and §11 state plainly that V3's
actual emitted event kinds are undocumented and that the Office UI mapping
still needs updating for them. Directly-emitted types confirmed in the live
path today are `run.started`, `answer.completed`, `artifact.created`,
`artifact.rejected`, `run.completed`, `run.failed`, `run.cancelled` — note
`artifact.rejected` is not in `CANONICAL_EVENT_TYPES`. **Enumerate what V3
actually emits before writing `NarrationPolicy`** (blocker B8).

A `NarrationPolicy` maps event types to speech, with a minimum gap between
utterances so the agent does not chatter:

| Event type | Spoken? | Example |
|---|---|---|
| `run.started` | once | "Looking into that now." |
| `intent.resolved` | once | "Okay — gross sales, last 30 days." |
| `plan.created` | yes | "I'll compare it against the prior period." |
| `tool.started` | throttled | "Pulling the numbers." |
| `evidence.added` | throttled, count only | "Got three results back." |
| `warning.created` | yes | "One caveat coming — the data's a bit thin." |
| `tool.failed` | yes | "That lookup failed, trying another way." |
| `run.completed` | yes | speak the answer summary (§4) |
| `run.failed` / `run.cancelled` | yes | plain failure sentence |
| everything else | no | — |

Rules:

- Never speak raw `payload` fields — speak only `title`/`summary`. These are
  promoted to top-level `ActivityEvent` fields and are deliberately *excluded*
  from `_safe_payload`'s `_SAFE_SOURCE_FIELDS` copy (`key not in {"title",
  "summary"}`) precisely because they are the display-facing pair. Narration
  additionally honours the `EventVisibility.USER` gate, so `INTERNAL` and
  `ADMIN` events are never spoken.
- Minimum 4 s between narration utterances; collapse bursts into one line.
- Narration is interruptible — user speech wins immediately (§6).
- Narration is a **separate TTS stream from the answer**, and can use a
  quieter/faster voice so a listener can tell progress from conclusion.
- If the stream is silent for 20 s, speak a generic holding line rather than
  inventing progress.

## 4. Speaking an evidenced answer

`REQUIRE_PROVENANCE_FOR_NUMERIC=true` and the golden rule mean the full answer
carries evidence ids that are meaningless aloud. Policy:

1. The **complete, evidenced answer is persisted to the thread** exactly as it
   is today — no change to `_answer_parts()`, no voice-specific truncation of
   the record.
2. The voice layer speaks a **spoken summary**: the headline finding, at most
   two or three numbers, and any `warning.created` caveat.
3. The spoken summary always ends by pointing at the record — "the full
   breakdown with sources is in the thread."
4. If the mission returns `INSUFFICIENT_EVIDENCE`, the agent says so plainly.
   It never fills the gap conversationally. This is the single most important
   behavioural test in the suite.
5. The spoken summary is persisted as its own `MessagePart` so an auditor can
   see what was said aloud versus what was written. Otherwise the audit trail
   does not cover the voice channel at all.

**Open decision.** Generating the summary with the voice LLM has real failure
modes (it can round, re-rank, or invent). The conservative alternative is a
deterministic summariser: headline sentence plus warnings, extracted from the
`MissionResult`. Phase 1 ships deterministic; Phase 2 evaluates the LLM variant
behind `VOICE_SUMMARY_MODE`, gated on an eval suite in `eval/suites/` that
checks number fidelity against the source result.

## 5. Auth and principal propagation

Two auth systems meet here, and getting this wrong silently runs every voice
user as the default principal.

- **LiveKit** needs a server-minted JWT. The API key/secret never reach the
  browser.
- **Seleric** authenticates via `ApiSecurityMiddleware`: `x-api-key` plus
  optional trusted identity headers, falling back to `default_workspace_id` /
  `default_user_id`.

Design:

1. New route `POST /v1/voice/token` on `seleric-api`. It authenticates the
   caller normally, derives the `Principal`, creates or reuses a thread, derives
   a room name from the thread id, and returns `{ url, token, room, thread_id }`.
2. Room participant metadata carries `workspace_id`, `user_id` and `thread_id`.
   The worker reads them from the job context and uses them for every Seleric
   call.
3. The worker calls Seleric with its own service `x-api-key` **plus** identity
   headers for the real principal. `trust_identity_headers` is enabled only for
   the worker's network path — a trusted-proxy decision that needs security
   review sign-off before Phase 2.
4. `_owned_thread` / `_owned_run` then behave identically to the typed path.
   Any path that falls back to the default principal is a bug, and there is a
   test asserting a cross-workspace thread id is rejected.

**Rate-limiting caveat.** `SlidingWindowRateLimiter` is per-worker, in-process,
60/min, keyed on the api-key prefix. One shared worker key puts every voice user
in a single bucket, and narration polling is chattier than typing. Either mint
per-session keys or exempt the worker's network path and rate-limit inside the
voice worker. Decide before Phase 2.

## 6. Barge-in and cancellation

When the user speaks while the agent is speaking:

1. Stop TTS immediately (`AgentSession` handles this; confirm the setting name
   at implementation time).
2. Suppress narration for that run.
3. If the utterance is a correction or a new question, call the existing
   `POST /v1/runs/{run_id}/cancel`. We do **not** invent a voice
   cancellation path — the cooperative cancellation surface already exists and
   is already durable.
4. A cancelled run still leaves its event trail, and the transcript shows the
   cancellation.

## 7. Data policy for audio

Audio is conversation content, and it contains business metrics spoken aloud.
`docs/47_CONVERSATION_DATA_POLICY.md` applies unchanged.

- **Vendor posture.** STT/TTS providers receive raw audio. Mirror the
  no-logging / zero-data-retention requirement that `.env.example` already flags
  for OpenRouter. Provider selection is a data-policy decision, not only a
  quality one — record it in an ADR.
- **Default: do not persist audio.** Persist the transcript, which the existing
  retention and deletion rules already cover. Recording is off by default.
- **If recording is ever enabled**, note that `ATTACHMENT_ALLOWED_MIME_TYPES`
  contains no audio types and blobs stay quarantined until the malware scanner
  returns `CLEAN` (`MALWARE_SCANNER_BACKEND=unavailable` is fail-closed by
  default). Enabling recording therefore requires adding `audio/*` types, a
  scanner decision for audio, a retention window, and a deletion path that
  includes recordings when a thread is deleted. That is a separate approved
  change, not a config tweak.
- **Traces.** Transcripts are raw user content and stay excluded from traces by
  default, same as prompts and tool results.

## 8. Configuration

New settings on `config/settings.py`, all with safe defaults:

```ini
VOICE_ENABLED=false
LIVEKIT_URL=
LIVEKIT_API_KEY=
LIVEKIT_API_SECRET=

# fake | <vendor>. `fake` is the CI default, mirroring LLM_PROVIDER=fake.
STT_PROVIDER=fake
TTS_PROVIDER=fake
VOICE_LLM_MODEL=                 # small/fast model for the conversational loop
VOICE_TTS_VOICE=

VOICE_NARRATION_ENABLED=true
VOICE_NARRATION_MIN_GAP_S=4
VOICE_NARRATION_IDLE_HOLD_S=20
VOICE_MISSION_TIMEOUT_S=180      # <= MISSION_TIMEOUT_S; voice gives up earlier
VOICE_RECORDING_ENABLED=false
VOICE_SUMMARY_MODE=deterministic # deterministic | llm
```

`seleric-voice = "seleric_swarm.voice.worker:main"` goes in `[project.scripts]`,
and the LiveKit dependencies go in a `voice` optional-dependency group so the
API image does not grow for deployments that do not use voice.

Per-user voice preferences (voice id, narration on/off, speech rate) have a
natural home in the existing `GET`/`PUT /v1/memories/preference`
rather than a new store.

## 9. Deployment

- **SFU: LiveKit Cloud for Phases 0–2.** Self-hosting an SFU is a real
  operational commitment (TURN, TLS, scaling) and is not what we are trying to
  learn early. Revisit at Phase 3 if audio egress policy requires it — noting
  that a self-hosted SFU does not by itself resolve the vendor-audio question,
  since STT/TTS remain third parties.
- **Worker: a new compose service** modelled on `recovery` — same `build: .`,
  same Postgres/Redis env, plus LiveKit and STT/TTS credentials. It is stateless
  and horizontally scalable; one worker process handles many rooms.
- **Health and tracing.** The worker is not an HTTP service; expose liveness the
  way `seleric-recover` does, and emit the same structured log fields
  (`thread_id`, `run_id`, `mission_id`, `trace_id`) so a voice session is
  traceable end to end.

## 10. Testing

Mirroring the existing `LLM_PROVIDER=fake` pattern so CI needs no vendor keys
and no LiveKit account:

| Layer | Test |
|---|---|
| `tests/unit/` | Narration policy: event sequence → expected utterances; throttling; never speaks `payload` |
| `tests/unit/` | Spoken-summary fidelity: every number spoken appears in the source `MissionResult` |
| `tests/replay/` | Recorded `ActivityEvent` sequence through the narrator with `STT_PROVIDER=fake` / `TTS_PROVIDER=fake`, asserting the full spoken script |
| `tests/contract/` | `POST /v1/voice/token` returns a well-formed grant and preserves the principal |
| `tests/api/` | A cross-workspace thread id in room metadata is rejected, not silently defaulted |
| `tests/adversarial/` | `INSUFFICIENT_EVIDENCE` is spoken as a refusal, never filled in |
| `tests/adversarial/` | Prompt injection carried in a tool result's `summary` does not change spoken narration |

The last two matter most: they are the tests that stop voice from quietly
becoming a channel where the evidence policy does not apply.

## 11. Reference: verified LiveKit API surface

Checked against `livekit/agents` upstream on 2026-09-24. **Pin the exact version
at implementation time** — this API has churned across releases, and everything
below should be re-verified against the pinned version before it is copied.

```python
from livekit.agents import Agent, AgentServer, AgentSession, JobContext, cli, inference
from livekit.agents.llm import function_tool

server = AgentServer()

@server.rtc_session()
async def entrypoint(ctx: JobContext):
    session = AgentSession(stt=..., llm=..., tts=...)
    await session.start(agent=Agent(instructions="..."), room=ctx.room)
    await session.generate_reply(instructions="greet the user")

if __name__ == "__main__":
    cli.run_app(server)
```

Token minting (`livekit-api`):

```python
from livekit import api

token = (
    api.AccessToken()
    .with_identity(principal.user_id)
    .with_grants(api.VideoGrants(room_join=True, room=room_name))
    .to_jwt()
)
```

Environment: `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`.
Install shape: `pip install "livekit-agents[<stt>,<tts>,<llm>]"`, plus
`livekit-api` for the token route.

**To verify at implementation time:**

- the exact interruption/barge-in setting name;
- whether narration is best delivered via `session.say()` or a separate audio
  source;
- whether an async `function_tool` handler can return control before the
  underlying work completes. It should — if it cannot, the narrator owns the
  entire wait, which is the documented fallback design and still works;
- **the transcription hook used by the Phase 0 echo agent.** `voice/worker.py`
  currently guesses `session.on("user_input_transcribed")` with `event.is_final`
  / `event.transcript`, and assumes a sync handler may call `session.say()`.
  None of that is verified — it is written against the churn-prone API and has
  never been run. Treat it as a placeholder until a live session confirms it.

**Dependency note.** `livekit-agents` is deliberately *not* declared in
`pyproject.toml`. It constrains `openai<3`, and because `uv.lock` is a single
universal resolution, adding it downgrades `openai` 3.7.0 → 2.54.0 for every
install — including the plain API, silently changing the stack under
`llm/adapters`. Only `livekit-api` (JWT minting, no such constraint) is
declared, as the `voice-api` extra. The worker image installs `livekit-agents`
in its own Dockerfile stage (`voice-runtime`).

`livekit-plugins-silero` must be installed alongside `livekit-agents` — it is
a separate package, not bundled — because `worker.py::build_server()` calls
`silero.VAD.load()` unconditionally. Without it the worker raises
`ModuleNotFoundError` on startup, before ever joining a room. The
`voice-runtime` Dockerfile stage installs both; a local (non-Docker) worker
venv needs `uv pip install livekit-agents livekit-plugins-silero` too.

Sources: [LiveKit Agents repo](https://github.com/livekit/agents),
[Agent session docs](https://docs.livekit.io/agents/logic/sessions/),
[Access tokens & grants](https://docs.livekit.io/frontends/authentication/tokens/).
