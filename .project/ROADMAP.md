# Roadmap

Source of truth: `10_IMPLEMENTATION_PLAN_AND_ACCEPTANCE.md` §7. Milestones
below are that plan reframed as gates, not a separate invented roadmap.

## M0 — Week 1: Platform contracts and live data slice

Gate: laptop or Pi asks company health and receives live TH data with provenance.

- Voice/Conversation: Pi kits, raspOVOS + USB audio, Seleric skill skeleton, wake word, Conversation API + 2 intents
- Ontology/State: config schema + publish lifecycle, initial node/edge draft, first certified MCP metrics validated, MetricState/FounderBrief contracts, live company-health payload
- Meeting: meeting/audio/utterance/commitment/verification schemas, one internal recording, cloud+on-prem transcription spike

## M1 — Week 2: Physical voice loop and top-three engine

Gate: physical node answers health, priorities, and why using live TH data.

- Complete Pi wake/STT/backend/TTS loop, LEDs/mute/stop/watchdog/reconnect, full executive intent routing
- Admin CRUD for nodes/edges/bindings/goals, rolling/delta/velocity/volatility features, node health, intervention templates, eligibility, deterministic ranking + dedupe

## M2 — Week 3: Risk, opportunity, and admin hardening

Gate: all five executive intelligence questions pass golden tests and traces.

- Anomaly/change-point policies, optional backtested StatsForecast models, risk/opportunity pipelines, prerequisite handling
- Config validate/approve/publish/rollback, decision inspector + OTel traces, intent test corpus + failure tests

## M3 — Week 4: Meeting intelligence

Gate: a real one-to-one meeting becomes approved evidence-linked commitments.

- Recording spool + segmented upload, transcription/diarization adapters, ontology-derived vocabulary, spaCy/dateparser extraction, review screen, commitment approval/persist, one certified verification adapter

## M4 — Week 5: Closed loop and reliability

Gate: at least one commitment reaches VERIFIED, BREACHED, or UNVERIFIABLE from real evidence.

- Deadline workers + verification, breach feed into executive risk/attention, chaos tests (stale data, provider outage, duplicate uploads, worker crash), backup/restore drill, 10 end-to-end rehearsals, feature freeze 2026-09-26

## M5 — 2026-09-29/30: Demo lock

No new features. Final config publish, data reconciliation, acoustic tuning, backup device/audio path, acceptance script against target utterances.

## Post-V1 (explicitly deferred — see doc 10 §12 "must not block the prototype")

Custom hardware PCB, large causal models, TFT/autoencoder training, online
feature store, fully autonomous write actions, multi-agent planner,
enterprise graph database, multi-region HA.
