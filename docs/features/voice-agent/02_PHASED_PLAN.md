# Voice Agent — Phased Plan

**Status:** proposed, nothing built. Each phase has a Definition of Done gate
derived from `docs/27_DEFINITION_OF_DONE.md`. Do not start a phase until the
previous phase's gate passes.

## Blockers found in the current code

These are not risks; they are facts in the repo today that the plan must handle.
Resolve each one in the phase noted.

| # | Blocker | Evidence | Resolve in |
|---|---|---|---|
| B1 | `submit_message` rejects any `execution_mode` other than `"development"` with a 400 | `src/seleric_swarm/api/conversations.py:1563` | Phase 1 |
| B2 | `answer.delta` is in `CANONICAL_EVENT_TYPES` but nothing in `src/` emits it — there is no token streaming, so an answer arrives as one finished message | `src/seleric_swarm/conversations/event_mapping_v1.py:43`; no other hit in `src/` | Phase 1 accepts it; Phase 2 fixes it |
| B3 | `.env.example` ships `V3_AGENT_ENABLED=false`, but the code default is `v3_agent_enabled: bool = True` (a soak kill-switch, per `agent/__init__.py`), and `_dispatch_route()` is hardcoded to `"v3"`. The env file and the code disagree | `config/settings.py:171`; `api/conversations.py:1083`; `agent/__init__.py:5`; `.env.example` | Phase 1 — pin the value explicitly in the voice deployment; do not inherit |
| B4 | `SlidingWindowRateLimiter` is per-worker and keyed on api-key prefix; a shared worker key puts all voice users in one 60/min bucket | `src/seleric_swarm/api/security.py` | Phase 2 |
| B5 | No audio MIME types in `ATTACHMENT_ALLOWED_MIME_TYPES`; blobs stay quarantined until ClamAV returns `CLEAN`, and the default scanner backend is `unavailable` (fail-closed) | `.env.example` | Only if recording is ever wanted (Phase 3) |
| B6 | Identity-header trust (`trust_identity_headers`) is off by default and falls back to `default_workspace_id`/`default_user_id` — a misconfigured worker silently runs every user as the default principal | `src/seleric_swarm/api/security.py` | Phase 2, with security review |
| **B7** | **The V3 agent loop emits no progress events at all** — not batched, absent. `agent/runner.py` has zero `event`/`emit`/`record` call sites; the "events" list is synthesized after the fact from mission status, and every entry it produces is either dropped or already excluded. Net contribution to a V3 run: **zero events**. See the spike result below | `agent/runner.py`; `api/office/v3_adapter.py:48`; `api/conversations.py:1223` | **Phase 1 — hard prerequisite, and larger than first scoped** |
| **B8** | **V3 has no event vocabulary to document.** Resolved by the same spike: `CANONICAL_EVENT_TYPES` / `_EXACT_KIND_MAP` are legacy swarm-era kinds. `artifact.rejected` is emitted but is not in `CANONICAL_EVENT_TYPES` | `conversations/event_mapping_v1.py`; `docs/CURRENT_ARCHITECTURE.md` §8, §11 | Folded into B7 |

### B7 / B8 spike result — resolved 2026-09-24, and it is worse than assumed

The Phase 0 spike is done. **The V3 agent loop emits no progress events at
all.** Not "batched" — absent. Evidence, in order:

1. `agent/runner.py` contains **zero** occurrences of `event`, `emit` or
   `record`. The loop never reports a step.
2. The `raw["events"]` list that `ingest_mission_events` consumes is
   **synthesized after the fact** by `api/office/v3_adapter.py::_mission_events`
   from nothing but mission status. It fabricates at most three entries:
   `mission_created`, `task_wave_executed` (only when evidence already exists),
   and one terminal kind.
3. Of those three, `task_wave_executed` maps to `None` and is **dropped** — it
   is absent from `_EXACT_KIND_MAP` and matches no prefix rule. Verified:

   ```console
   $ python -c "from seleric_swarm.conversations.event_mapping_v1 import \
       event_type_for_mission_kind as f; print(f('task_wave_executed'))"
   None
   ```

4. The other two map to `run.started` / `run.completed`, and **both are in the
   `exclude_event_types` set** at the `ingest_mission_events` call site
   (`api/conversations.py:1223`).

**Therefore `ingest_mission_events` contributes exactly zero events to a V3
run.** The entire narratable stream is `run.started` → (silence) →
`artifact.created` / `answer.completed` → terminal.

**Consequences:**

- Option **(b) is dead.** Polling `GET /v1/missions/{id}/events` yields the same
  three synthetic entries. There is no incremental store to poll.
- B7 is not "make a batch incremental." It is **"instrument the V3 agent loop,"**
  which does not currently report tool calls, plan steps, or evidence
  acquisition to anything. That is real work in `agent/runner.py` and the
  toolsets, not a plumbing change.
- B8 is answered, and the answer is that there is no vocabulary to document yet.
  The `NarrationPolicy` table in `01_ARCHITECTURE.md` §3 stays **illustrative**
  until the loop is instrumented, and it should be treated as a *specification
  for what to emit* rather than a description of what exists.
- This is not a voice bug. The typed Office UI has exactly the same dead
  stream — `docs/CURRENT_ARCHITECTURE.md` §8 already flags the V3 event
  vocabulary as unresolved. Voice is the forcing function, not the cause.

**Revised recommendation.** Instrument the agent loop as a **separate,
independently valuable piece of work** (`tool.started` / `tool.completed` /
`evidence.added` / `plan.created` emitted from `agent/runner.py` as they
happen), and treat it as a hard dependency of voice Phase 1 rather than part of
it. Until it lands, voice Phase 1 can only ship in the **(c) degraded shape**:
generic timed holding lines over a silent stream. That is demoable but it is
not the product, and the phase gate should say so rather than quietly passing.

**B1 decision (needed on day one of Phase 1).** Voice goes through the same
`POST /v1/threads/{id}/messages` endpoint, so it hits this check. Two options:

- **(a) Send `execution_mode="development"`.** Zero code change, ships now.
  Correct if `"development"` is genuinely the only supported mode today.
- **(b) Add a `"voice"` mode** to the accepted set, so voice runs are
  distinguishable in the run record for analytics, budgets, and rate limiting.

**Recommendation: (a) for Phase 0–1, (b) in Phase 2**, once there is a reason to
tell voice runs apart. Confirm with whoever owns that check why the allow-list
is a single value before changing it — it may be a deliberate gate on an
unfinished production mode, in which case (b) is not a one-line change.

**B2 decision.** Without `answer.delta`, a voice session goes: question →
narration → *silence* → one long finished answer. Phase 1 accepts this, and
narration (`01_ARCHITECTURE.md` §3) is precisely what makes it bearable. Phase 2
emits `answer.delta` from the agent loop so TTS can begin on the first sentence
rather than the last. That is the single largest perceived-latency win available
and should not be treated as polish.

---

## Phase 0 — Transport proof (no Seleric integration)

> **Status: built, partially verified (2026-09-24).**
>
> | Item | State |
> |---|---|
> | `POST /v1/voice/token` (`voice/token.py`) | **done, tested** — 22 tests green |
> | `voice/worker.py` echo agent | **written, unverified** — needs LiveKit credentials |
> | `seleric-voice` script, `voice` / `voice-api` extras | done |
> | `VOICE_*` settings + startup guards | done, tested |
> | compose `voice` service (profile `voice`) | done, not run |
> | browser join page | **not started** — needs credentials to be worth writing |
> | B7/B8 spike | **done — see below, the result is bad** |
>
> `livekit-api` is installed in `.venv`; `livekit-agents` is **not**. The echo
> worker cannot be run until both that and a LiveKit project exist.
>
> **Dependency finding (B9).** `livekit-agents` constrains `openai<3`. Because
> `uv.lock` is one universal resolution, declaring it in `pyproject.toml`
> downgraded `openai` 3.7.0 → 2.54.0 for *every* install, including the plain
> API — silently changing the stack under `llm/adapters`. Resolved by declaring
> only `livekit-api` (extra `voice-api`, used by the token route) and installing
> `livekit-agents` in a dedicated `voice-runtime` Dockerfile stage. `uv.lock` is
> regenerated and `openai` stays at 3.7.0. **Do not move `livekit-agents` into
> `pyproject.toml` without re-checking that.**

**Goal:** prove audio in and out, and prove the auth hop, before any of the hard
parts. If the SFU, the browser, or the token mint is going to fight us, find out
here.

Scope:

- `POST /v1/voice/token` on `seleric-api`: authenticate normally, derive the
  `Principal`, mint a LiveKit JWT scoped to a room derived from a thread id.
- `src/seleric_swarm/voice/worker.py`: an echo agent. STT → repeat back via TTS.
  No LLM, no Seleric call.
- `seleric-voice` console script; `voice` optional-dependency group; pinned
  `livekit-agents` version recorded in the plan.
- A minimal browser page (can live in `office-ui/` behind a route) that fetches a
  token and joins.
- LiveKit Cloud project; credentials in `.env.example` as empty defaults.

**DoD gate:**

- [ ] Speak into the browser, hear it echoed back, round trip under ~1.5 s.
- [ ] Token route rejects an unauthenticated caller.
- [ ] **B7/B8 investigation done** (this is a read-only spike, safe to run here):
      confirm whether `GET /v1/missions/{id}/events` exposes progress mid-run,
      and enumerate the event kinds one real V3 mission emits. Both answers land
      in this doc and in `01_ARCHITECTURE.md` §3 before Phase 1 starts.
- [ ] Worker starts and stops cleanly in compose alongside `recovery`.
- [ ] `VOICE_ENABLED=false` fully disables the route and the worker.
- [ ] Pinned LiveKit version written into `01_ARCHITECTURE.md` §11, and §11's
      "to verify" list resolved against that version.

**Explicitly out of scope:** narration, missions, summaries, barge-in tuning.

---

## Phase 1 — One-shot voice question, narrated

**Goal:** ask a real business question aloud and hear a real, evidenced answer.
This is the phase that proves the two-loop design.

Scope:

- Voice LLM in the session with an `ask_seleric(question)` `function_tool` that
  submits to `POST /v1/threads/{id}/messages` and returns a `run_id` immediately.
- B1 resolved (send `"development"` unless told otherwise).
- B3 resolved: pin `V3_AGENT_ENABLED` explicitly in the voice deployment rather
  than inheriting a value the env file and the code disagree about.
- **B7 resolved: incremental event emission.** Without this the narrator has no
  input and Phase 1 degrades to silence-then-monologue. Do this first.
- **B8 resolved: V3's event vocabulary enumerated** and `01_ARCHITECTURE.md` §3
  rewritten from illustrative to specified.
- Narrator: consume `/v1/runs/{run_id}/events/stream`, apply `NarrationPolicy`,
  speak throttled progress. Idle-hold line after 20 s of silence.
- **Deterministic** spoken summary on `run.completed`
  (`VOICE_SUMMARY_MODE=deterministic`): headline + warnings + "full breakdown is
  in the thread."
- `INSUFFICIENT_EVIDENCE` spoken as a plain refusal.
- Barge-in: user speech stops TTS and suppresses narration; a new question
  cancels the in-flight run via the existing cancel route.
- Spoken summary persisted as its own `MessagePart`.
- `VOICE_MISSION_TIMEOUT_S` — voice gives up and says so before the mission's own
  600 s bound.

**DoD gate:**

- [ ] End-to-end: spoken question → narrated wait → spoken summary, with the
      full evidenced answer visible in the thread transcript.
- [ ] Narration never speaks a `payload` field or an `INTERNAL`/`ADMIN` event.
- [ ] Replay test in `tests/replay/` passes with `STT_PROVIDER=fake` /
      `TTS_PROVIDER=fake` — CI needs no vendor keys.
- [ ] Adversarial test: `INSUFFICIENT_EVIDENCE` is refused aloud, not filled in.
- [ ] Adversarial test: injection text in a tool `summary` does not alter
      narration.
- [ ] Barge-in cancels the run; the cancellation appears in the event trail.
- [ ] Failure and timeout behaviour documented and tested (mission failure,
      stream disconnect, TTS vendor outage).
- [ ] Rollback: `VOICE_ENABLED=false` disables everything with no residue.

---

## Phase 2 — Make it feel fast, and make it safe for more than one user

**Goal:** close the latency gap and the multi-tenant gaps. This is where the
prerequisite work from B2, B4 and B6 lands.

Scope:

- **Emit `answer.delta`** from the agent loop so TTS starts on the first
  sentence. Biggest perceived-latency win available (B2).
- **Principal propagation hardened** (B6): identity headers trusted only on the
  worker path; test asserting a cross-workspace thread id is rejected rather
  than silently defaulted. Security review sign-off required.
- **Rate limiting** (B4): per-session keys, or exempt the worker path and limit
  inside the voice worker. Narration polling must not starve typed users.
- **`"voice"` execution mode** (B1 option b) so voice runs are distinguishable
  in the run record.
- **LLM spoken summary** behind `VOICE_SUMMARY_MODE=llm`, gated on a new eval
  suite in `eval/suites/` that checks every spoken number against the source
  `MissionResult`. Ships only if the eval passes; otherwise deterministic stays
  the default.
- **Voice preferences** via the existing `memories/preference` endpoint.
- **Multi-turn context**: follow-ups ("and last month?") reuse the thread. Note
  `docs/CURRENT_ARCHITECTURE.md` §11 — follow-up time/metric inheritance was
  deleted as dead code and has no live implementation. Voice makes this
  noticeably worse than typing does, because people speak in follow-ups. Treat
  it as a dependency, not an assumption.

**DoD gate:**

- [ ] Time-to-first-spoken-word of the answer measurably improved, with numbers
      recorded in this doc.
- [ ] Security review complete for the trusted-header path.
- [ ] Two concurrent voice sessions in different workspaces cannot see each
      other's threads — tested, not assumed.
- [ ] Rate-limit behaviour under two concurrent voice sessions documented.
- [ ] Eval suite for spoken-number fidelity exists and passes.
- [ ] Observability: a voice session is traceable end to end by `trace_id`.

---

## Phase 3 — Production posture

**Goal:** everything that makes this deployable to people who are not us.

Scope:

- **Data-policy ADR** for STT/TTS vendors: no-logging / ZDR posture, mirroring
  the OpenRouter note in `.env.example`. Provider choice recorded as a decision.
- **Recording** — only if actually wanted. Requires all of B5: audio MIME types,
  a scanner decision for audio, a retention window, and deletion of recordings
  when a thread is deleted.
- **Self-hosted SFU** decision, if egress policy demands it. Note this does not
  resolve the vendor-audio question on its own.
- **Accessibility**: live captions in the UI alongside audio; keyboard
  push-to-talk; a fully equivalent typed path. See `docs/office-ui/14_ACCESSIBILITY.md`.
- **Write actions by voice**: the propose → validate → preview → confirm →
  commit → audit flow (`conversations/phase7.py`). **Strong recommendation: do
  not allow voice confirmation of a write action.** Speech recognition errors on
  a confirmation step are a category of risk this system is otherwise built to
  avoid. Voice may *propose*; confirmation stays typed.
- **Cost model**: STT + TTS + voice LLM per minute, and a per-workspace budget,
  since voice makes it trivially easy to run many missions quickly.
- **Load and failure drills**: SFU outage, STT vendor outage, Postgres failover
  mid-narration.

**DoD gate:**

- [ ] Security review complete for the whole feature.
- [ ] Data-policy ADR merged; retention and deletion cover transcripts and any
      recordings.
- [ ] Accessibility parity verified — nothing is voice-only.
- [ ] Cost per voice minute measured and a budget enforced.
- [ ] Rollback/disable switch verified under load.

---

## Sequencing notes

- **Phase 0 before anything.** The transport and auth hop are the most likely
  source of a surprise, and they are the cheapest to test in isolation. Run the
  B7/B8 spike in the same window — it is read-only and it decides how big
  Phase 1 actually is.
- **B7 and B2 are the same shape of work** (event emission from the agent loop)
  and both benefit the typed Office UI, not just voice. B7 is a Phase 1
  prerequisite; B2 is a Phase 2 improvement. If they are being funded together,
  doing B2 alongside B7 is much cheaper than doing it a phase later.
- **The honest risk to this plan:** if B7 turns out to be expensive, Phase 1
  either slips or ships as silence-then-monologue. Decide which *before*
  starting, not halfway through. A voice agent that goes quiet for a minute is
  worse than no voice agent, because users will talk over it and cancel runs.
- **Do not let voice become a second answer path.** Every phase gate above has
  at least one check asserting the evidenced text answer is still the record.
  That is the property most likely to erode quietly.
