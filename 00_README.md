# Seleric Voice Node V1 - Platform Blueprint

**Status:** Architecture baseline for implementation (rewritten 2026-08-31 for agent-swarm reasoning)
**Target:** Working physical prototype by September 30, 2026
**Primary deployment:** Tilting Heads (`brand_id = 20`)
**Architecture style:** Configurable microservices, agent-swarm business reasoning with a non-recruitable Governor safety boundary, OOP domain model, open-source-first voice and meeting infrastructure

## 1. Executive decision

On 2026-08-31 the founder replaced V1's foundational reasoning model. Everything upstream and downstream of business reasoning is unchanged: the Seleric MCP is still the only trusted source of certified metrics, and voice/meeting infrastructure is untouched. What changed is what happens to a certified metric between "ingested" and "spoken to the founder": that step is no longer deterministic code. It is a swarm of LLM agents — the **Seleric Swarm Layer** — that notice problems, form hypotheses, recruit each other, debate, and propose actions, with a non-recruitable **Seleric Governor** enforcing every safety boundary above them.

The founder-facing guarantee changes accordingly:

- **Before:** deterministic, reproducible, evidence-traceable. Identical inputs and configuration always produced an identical brief.
- **Now:** agent-derived, evidence-grounded, confidence-scored, and fully audited — but not guaranteed reproducible. Every fact an agent cites still traces to a certified MCP query or a prior Blackboard artifact; nothing is fabricated. But the reasoning path that reaches a conclusion is not guaranteed identical on rerun. The full agent debate that produced any conclusion is permanently recorded and is the accountability mechanism in place of determinism.

The six-service split from the original architecture is preserved. The Seleric Swarm Layer lives inside `insight-decision-service` — the service that already owned business decision policy — rather than becoming a seventh service (see doc 03 §3.4 and §14 for the justification). The Seleric Governor is not a service and not a swarm participant; it is a policy authored in `control-plane-service` (versioned like every other config object) and enforced at the runtime boundary of `insight-decision-service`.

1. **OpenVoiceOS Edge Node** - microphone, wake word, VAD, STT/TTS adapters, speaker, buttons, meeting capture. *(unchanged)*
2. **Voice Orchestrator Service** - deterministic intent routing, session references, response orchestration, proactive device notifications. *(unchanged — still deterministic; it reads the swarm's latest published brief, it does not run agents)*
3. **Business State Service** - certified metric retrieval through Seleric MCP, derived features, lightweight forecasts, anomalies, health snapshots. *(unchanged — still deterministic; it is the swarm's evidence source, not a swarm participant)*
4. **Insight Decision Service** - now hosts the Seleric Swarm Layer: Blackboard, Coordinator, Agent Registry, the seven initial agents, task market, and the runtime enforcement point for Governor policy.
5. **Meeting Intelligence Service** - audio/transcript processing, diarization, deterministic semantic extraction, review, commitments and verification. *(unchanged)*
6. **Control Plane Service + Appsmith Admin UI** - versioned configuration, validation, simulation, publish, rollback, audit — now also the authoring/versioning home for Governor policy.

The existing Seleric MCP remains the trusted metric access layer. PostgreSQL stores configuration, operational state, and the swarm Blackboard (no new datastore — see doc 01 §3a). Existing ClickHouse stores high-volume observations and state history. S3-compatible object storage stores audio, transcript and model artifacts. A PostgreSQL-backed worker queue handles scheduled and retryable work without Kafka, Redis or Temporal in V1. LangGraph is adopted for swarm orchestration, persistent state, and agent handoffs (doc 01 §3a.1, doc 04).

## 2. What is reused rather than built

| Area | Reused foundation | Seleric-specific code |
|---|---|---|
| Edge voice assistant | OpenVoiceOS installer/core/listener/audio/skill framework | `SelericBridgeSkill`, device enrollment, LED/button integration, meeting mode |
| Wake word and VAD | OVOS openWakeWord and Silero VAD plugins | Custom "Hey Seleric" model, thresholds and QA |
| STT/TTS | OVOS STT/TTS server adapters, Faster Whisper, phoonnx/Piper; managed provider adapters optional | Provider selection policy, vocabulary and fallback configuration |
| Forecasting | Robust built-ins + StatsForecast; optional Merlion adapter | Metric-specific profiles, walk-forward backtests, promotion gates and publication rules |
| Anomaly detection | Prediction intervals/residual rules; PyOD only for selected multivariate cases | Severity policy and Blackboard evidence integration |
| Ontology execution | PostgreSQL + NetworkX | Seleric node/edge types, metric bindings, health policies — now also the swarm's shared model of reality (agent hypotheses must cite real node/edge IDs) |
| Causal analysis | DoWhy plugin, later and only for validated causal graphs | Causal assumptions, datasets, validation and approval |
| **Agent orchestration** | **LangGraph — swarm/handoff pattern, PostgreSQL-backed checkpointing** | **Blackboard schema, Agent Registry, the 7 agent roles, task-market bidding, Governor enforcement point** |
| **Case similarity search** | **`pgvector` PostgreSQL extension** | **Case-embedding pipeline, retrieval ranking** |
| Meeting transcription | WhisperX/Faster Whisper + pyannote | Physical-room capture, participant resolution, TH vocabulary and evidence-linked extraction; Vexa remains an optional online-meeting adapter |
| Commitment extraction | spaCy EntityRuler/DependencyMatcher + date parsing | Controlled patterns, ontology resolution, confidence and review policy |
| Admin system | Appsmith Community Edition | Seleric control-plane forms, graph editor, simulations, approvals, and Governor policy editor |
| Background jobs | Procrastinate PostgreSQL task queue | State-refresh, transcription, verification and notification tasks |
| Observability | OpenTelemetry and Grafana stack / Azure Application Insights | Business freshness, model quality, agent-debate audit, and Governor-decision dashboards |

## 3. Architecture principle

```text
Certified facts (Seleric MCP — unchanged, sole source of truth)
-> versioned business objects and goals
-> derived state and uncertainty (Business State Service — still deterministic)
-> Seleric Blackboard case opened
-> agent swarm: observe -> hypothesize -> recruit -> debate -> propose action
   (Governor enforces tool/spend/PII/write/spawn/iteration limits throughout)
-> confidence-scored, evidence-grounded conclusion + full debate audit trail
-> controlled execution (Governor-gated; human-approval gate where policy requires)
-> outcome returns -> swarm/reputation learns
-> evidence-backed template response
-> action/commitment
-> subsequent outcome verification
```

No LLM was permitted in the V1 business-reasoning path in the original design; that rule is now explicitly reversed for metric/candidate selection, health assessment, ranking, and root-cause diagnosis, which run through the agent swarm under Governor control. What is still true: the swarm cannot invent a business fact — it can only reason over Seleric-MCP-derived state and prior Blackboard evidence — and it cannot execute a production write, spend money, access PII, or communicate externally without an explicit Governor grant.

## 4. Verified Seleric data boundary

Unchanged. The Seleric MCP catalogue inspected for this design was version `47f987dbb82d`. It already exposes certified finance, commerce, attribution, paid-media, Amazon, product and session-funnel metrics, including hourly Meta/Google delivery metrics and rich campaign/ad/device/geo/landing-page dimensions. It also exposes metric definitions, formulas, freshness, access policies, validation tests and query provenance.

Not currently exposed through the inspected MCP catalogue are company goals, founder escalation rules, ontology health, derived state, forecasts, anomaly outputs, inventory readiness, meetings, commitments, verification state or executable business actions. These remain explicit V1 platform objects. The swarm reasons over these platform objects plus MCP-derived state; it does not gain any metric-access path MCP does not already provide.

## 5. Deliverables in this package

- `01_OVERENGINEERING_AND_REUSE_REVIEW.md` - architectural corrections, plug-and-play evaluation, and the swarm-stack adoption rationale.
- `02_SOFTWARE_REQUIREMENTS_SPECIFICATION.md` - complete V1 SRS, reframed around agent-driven reasoning.
- `03_HIGH_LEVEL_DESIGN.md` - HLD, service boundaries (including the swarm-layer placement decision), trust zones and deployment.
- `04_TECH_STACK_AND_DEPLOYMENT_OPTIONS.md` - selected stack including LangGraph, Azure and on-prem mappings.
- `05_COMPONENT_CONTRACTS.md` - purpose, necessity, input, output, configuration and failure behavior for every component, including Blackboard/Coordinator/Registry/Governor/agents.
- `06_LOW_LEVEL_DESIGN_OOP.md` - OOP domain model, agent/message/case-record classes, APIs, schemas, patterns and idempotency.
- `07_WORKFLOW_AND_DATA_FLOW.md` - voice, state, swarm-insight, proactive and meeting flows.
- `08_ADMIN_AND_CONFIGURATION_CONTROL_PLANE.md` - dynamic configuration, admin specification, and Governor policy administration.
- `09_SECURITY_OBSERVABILITY_AND_OPERATIONS.md` - security, Governor enforcement, monitoring, retention, backup and runbooks.
- `10_IMPLEMENTATION_PLAN_AND_ACCEPTANCE.md` - ownership, phased plan toward September 30, 2026, acceptance gates and risk register.
- `11_OFFICIAL_SOURCE_RESEARCH.md` - official/open-source reference catalogue including LangGraph and (future) A2A.
- `12_SYSTEM_SPEC_SHEET.md` - concise system specification and acceptance summary.
- `13_OPEN_SOURCE_PLUG_AND_PLAY_MATRIX.md` - researched reuse/adopt/defer/reject decisions, including LangGraph adoption and A2A's future trigger.
- `14_DATA_MODEL_AND_PERSISTENCE.md` - service-owned schemas including the Blackboard, agent registry and reputation schema, current/history split, event/outbox, retention, RLS, indexes and ER model.
- `diagrams/*.mmd` - standalone Mermaid sources for HLD, LLD, data, workflow, deployment and sequences.

## 6. Final V1 deployment count

### Office edge

- One Raspberry Pi deployment running OpenVoiceOS plus the Seleric bridge and recorder. *(unchanged)*

### Backend services

- `voice-orchestrator`
- `business-state-service`
- `insight-decision-service` (now hosts the Seleric Swarm Layer)
- `meeting-intelligence-service`
- `control-plane-service` (now hosts Governor policy authoring)
- `admin-ui` (Appsmith)

Still six services. The swarm did not earn a seventh — see doc 03 §3.4 and §14 for the explicit justification any future reader should re-check before proposing to split it out.

### Shared platform dependencies

- Existing Seleric MCP/Cube/ClickHouse
- PostgreSQL (now also backing the Blackboard, agent registry/reputation, and LangGraph checkpoints, via the `pgvector` extension for case similarity)
- S3-compatible object storage
- PostgreSQL task workers
- Identity provider, ingress and observability stack

This is still the minimum service split that preserves different latency, security, scaling and lifecycle characteristics without creating a distributed-system maze.
