# Seleric Voice Node V1 - Platform Blueprint

**Status:** Architecture baseline for implementation  
**Target:** Working physical prototype by September 30, 2026  
**Primary deployment:** Tilting Heads (`brand_id = 20`)  
**Architecture style:** Configurable microservices, deterministic decisioning, OOP domain model, open-source-first voice and meeting infrastructure

## 1. Executive decision

The V1 platform should not be a collection of independent AI tools. It should be a small set of stable services with explicit contracts:

1. **OpenVoiceOS Edge Node** - microphone, wake word, VAD, STT/TTS adapters, speaker, buttons, meeting capture.
2. **Voice Orchestrator Service** - deterministic intent routing, session references, response orchestration, proactive device notifications.
3. **Business State Service** - certified metric retrieval through Seleric MCP, derived features, lightweight forecasts, anomalies, health snapshots.
4. **Insight Decision Service** - ontology traversal, candidate generation, root-driver consolidation, founder eligibility, deterministic ranking, template NLG.
5. **Meeting Intelligence Service** - audio/transcript processing, diarization, deterministic semantic extraction, review, commitments and verification.
6. **Control Plane Service + Appsmith Admin UI** - versioned configuration, validation, simulation, publish, rollback and audit.

The existing Seleric MCP remains the trusted metric access layer. PostgreSQL stores configuration and operational state. Existing ClickHouse stores high-volume observations and state history. S3-compatible object storage stores audio, transcript and model artifacts. A PostgreSQL-backed worker queue handles scheduled and retryable work without Kafka, Redis or Temporal in V1.

## 2. What is reused rather than built

| Area | Reused foundation | Seleric-specific code |
|---|---|---|
| Edge voice assistant | OpenVoiceOS installer/core/listener/audio/skill framework | `SelericBridgeSkill`, device enrollment, LED/button integration, meeting mode |
| Wake word and VAD | OVOS openWakeWord and Silero VAD plugins | Custom “Hey Seleric” model, thresholds and QA |
| STT/TTS | OVOS STT/TTS server adapters, Faster Whisper, phoonnx/Piper; managed provider adapters optional | Provider selection policy, vocabulary and fallback configuration |
| Voice intents | Configurable deterministic intent grammar | Seleric intent catalogue and command handlers |
| Forecasting | Robust built-ins + StatsForecast; optional Merlion adapter | Metric-specific profiles, walk-forward backtests, promotion gates and publication rules |
| Anomaly detection | Prediction intervals/residual rules; PyOD only for selected multivariate cases | Severity policy and business-state integration |
| Ontology execution | PostgreSQL + NetworkX | Seleric node/edge types, metric bindings, health and root-driver policies |
| Causal analysis | DoWhy plugin, later and only for validated causal graphs | Causal assumptions, datasets, validation and approval |
| Meeting transcription | WhisperX/Faster Whisper + pyannote | Physical-room capture, participant resolution, TH vocabulary and evidence-linked extraction; Vexa remains an optional online-meeting adapter |
| Commitment extraction | spaCy EntityRuler/DependencyMatcher + date parsing | Controlled patterns, ontology resolution, confidence and review policy |
| Admin system | Appsmith Community Edition | Seleric control-plane forms, graph editor, simulations and approvals |
| Background jobs | Procrastinate PostgreSQL task queue | State-refresh, transcription, verification and notification tasks |
| Observability | OpenTelemetry and Grafana stack / Azure Application Insights | Business freshness, model quality and decision-trace dashboards |

## 3. Architecture principle

```text
Certified facts
-> versioned business objects and goals
-> derived state and uncertainty
-> eligible intervention candidates
-> root-driver consolidation
-> deterministic founder-priority ranking
-> evidence-backed template response
-> action/commitment
-> subsequent outcome verification
```

No LLM is required in the V1 business-reasoning path. A future optional language adapter may summarize already-validated structured output, but it may not select metrics, calculate health, rank actions or create unsupported facts.

## 4. Verified Seleric data boundary

The Seleric MCP catalogue inspected for this design was version `47f987dbb82d`. It already exposes certified finance, commerce, attribution, paid-media, Amazon, product and session-funnel metrics, including hourly Meta/Google delivery metrics and rich campaign/ad/device/geo/landing-page dimensions. It also exposes metric definitions, formulas, freshness, access policies, validation tests and query provenance.

Not currently exposed through the inspected MCP catalogue are company goals, founder escalation rules, ontology health, derived state, forecasts, anomaly outputs, inventory readiness, meetings, commitments, verification state or executable business actions. These are therefore explicit V1 platform objects rather than assumed existing capabilities.

## 5. Deliverables in this package

- `01_OVERENGINEERING_AND_REUSE_REVIEW.md` - architectural corrections and plug-and-play evaluation.
- `02_SOFTWARE_REQUIREMENTS_SPECIFICATION.md` - complete V1 SRS.
- `03_HIGH_LEVEL_DESIGN.md` - HLD, service boundaries, trust zones and deployment.
- `04_TECH_STACK_AND_DEPLOYMENT_OPTIONS.md` - selected stack, Azure and on-prem mappings.
- `05_COMPONENT_CONTRACTS.md` - purpose, necessity, input, output, configuration and failure behavior for every component.
- `06_LOW_LEVEL_DESIGN_OOP.md` - OOP domain model, APIs, schemas, patterns, idempotency and service internals.
- `07_WORKFLOW_AND_DATA_FLOW.md` - voice, state, insight, proactive and meeting flows.
- `08_ADMIN_AND_CONFIGURATION_CONTROL_PLANE.md` - dynamic configuration and admin specification.
- `09_SECURITY_OBSERVABILITY_AND_OPERATIONS.md` - security, monitoring, retention, backup and runbooks.
- `10_IMPLEMENTATION_PLAN_AND_ACCEPTANCE.md` - ownership, weekly plan, test gates and risk register.
- `11_OFFICIAL_SOURCE_RESEARCH.md` - official/open-source reference catalogue and adoption notes.
- `12_SYSTEM_SPEC_SHEET.md` - concise system specification and acceptance summary.
- `13_OPEN_SOURCE_PLUG_AND_PLAY_MATRIX.md` - researched reuse/adopt/defer/reject decisions for open-source systems.
- `14_DATA_MODEL_AND_PERSISTENCE.md` - service-owned schemas, current/history split, event/outbox, retention, RLS, indexes and ER model.
- `diagrams/*.mmd` - standalone Mermaid sources for HLD, LLD, data, workflow, deployment and sequences.

## 6. Final V1 deployment count

### Office edge

- One Raspberry Pi deployment running OpenVoiceOS plus the Seleric bridge and recorder.

### Backend services

- `voice-orchestrator`
- `business-state-service`
- `insight-decision-service`
- `meeting-intelligence-service`
- `control-plane-service`
- `admin-ui` (Appsmith)

### Shared platform dependencies

- Existing Seleric MCP/Cube/ClickHouse
- PostgreSQL
- S3-compatible object storage
- PostgreSQL task workers
- Identity provider, ingress and observability stack

This is the minimum service split that preserves different latency, security, scaling and lifecycle characteristics without creating a distributed-system maze.
