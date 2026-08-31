# Official Source Research: Open-Source and Plug-and-Play Systems

**Research date:** August 25, 2026  
**Decision lens:** Reuse mature plumbing; own the Seleric business-state, decision, configuration, and verification semantics.

## 1. Research conclusion

There is no single open-source platform that delivers the complete Seleric requirement. The fastest reliable route is a composable stack:

```text
Open-source voice shell
+ existing Seleric certified data access
+ small deterministic state/decision platform
+ open-source meeting capture/transcription
+ configurable admin/control plane
```

The project should not fork a complete voice assistant or meeting product and then force business intelligence into it. It should use their stable interfaces as adapters around Seleric-owned domain services.

## 2. Selection criteria

Every candidate was evaluated against:

1. Can it be self-hosted or replaced through an adapter?
2. Does it remove low-value plumbing rather than control Seleric business logic?
3. Is its runtime boundary clear?
4. Can it run on Raspberry Pi/Linux or ordinary containers?
5. Does it have a usable API, plugin, or protocol contract?
6. Can configuration be versioned and tested?
7. Can it operate without granting arbitrary database or code execution?
8. Is the license compatible with internal development?
9. Is the project active enough for an MVP dependency?
10. Can it be removed later without rewriting domain logic?

## 3. Voice edge and local assistant foundations

### 3.1 OpenVoiceOS

**Official sources**

- https://openvoiceos.org/
- https://github.com/OpenVoiceOS
- https://github.com/OpenVoiceOS/ovos-dinkum-listener
- https://github.com/OpenVoiceOS/ovos-stt-server
- https://github.com/OpenVoiceOS/ovos-tts-server

**Provides**

- Linux/Python voice-assistant runtime
- Microphone and audio-loop handling
- Wake-word and VAD plugin points
- STT/TTS plugin abstraction
- Skill/plugin model
- Raspberry Pi/Linux deployment paths
- HTTP microservices that can expose STT and TTS plugins behind stable APIs

The OVOS STT server can host Faster Whisper or cloud-provider plugins and exposes vendor-compatible endpoint shapes. The TTS server is a small stateless service capable of hosting Piper/other plugins and also exposes compatibility endpoints. This is valuable because Seleric can change a provider through configuration rather than rewrite the edge client.

**Adoption decision**

Selected as the V1 edge voice shell. Build only:

- `SelericBridgeSkill`
- Device enrollment and configuration
- LED/button integration
- Executive intent handoff
- Meeting-mode capture
- Seleric-specific telemetry

**Cautions**

- Bind the OVOS message bus to localhost; it is not an internet-facing authorization boundary.
- Pin plugin versions and test the exact audio device image.
- Treat the edge runtime as replaceable through the Seleric Edge API contract.

### 3.2 Open Home Foundation / Home Assistant local voice stack

**Official sources**

- https://www.home-assistant.io/voice_control/voice_remote_local_assistant/
- https://github.com/OHF-Voice/linux-voice-assistant
- https://github.com/rhasspy/wyoming
- https://github.com/rhasspy/wyoming-faster-whisper

**Provides**

- Proven local pipeline composition: wake/listen -> STT -> intent -> TTS
- Wyoming protocol for simple voice-service interoperability
- Whisper and Piper local service patterns
- Linux voice-satellite work for x64/Arm64

**Adoption decision**

Use as an interoperability/reference source, not as the core Seleric application. The Linux Voice Assistant repository describes itself as experimental and is coupled to Home Assistant/ESPHome protocol semantics. Wyoming-compatible speech services remain useful alternatives if the team prefers that protocol.

### 3.3 openWakeWord

**Official source**

- https://github.com/dscripka/openWakeWord

**Provides**

- Local ONNX/TFLite wake-word inference
- Linux/Arm support
- Configurable thresholds
- Custom wake-word training path

**Adoption decision**

Selected through an OVOS plugin. The custom “Hey Seleric” model, office acoustic test set, threshold, and false-positive monitoring are Seleric-owned.

### 3.4 Silero VAD

**Official source**

- https://github.com/snakers4/silero-vad

**Provides**

- Lightweight voice-activity detection suitable for real-time CPU use

**Adoption decision**

Selected as a VAD strategy where the edge listener supports it. Keep the interface replaceable; hardware DSP VAD may be used as an additional signal.

### 3.5 Pipecat and LiveKit Agents

**Official sources**

- https://github.com/pipecat-ai/pipecat
- https://docs.livekit.io/agents/

**Provide**

- Frame-based or WebRTC-based real-time voice orchestration
- Turn taking, interruption, provider integrations, and multi-participant/media features

**Adoption decision**

Not a V1 dependency. They become justified when Seleric needs telephony, WebRTC rooms, browser participants, or complex streaming-provider orchestration. Adding either alongside OVOS in V1 duplicates conversation plumbing.

## 4. STT and TTS

### 4.1 Faster Whisper

**Official source**

- https://github.com/SYSTRAN/faster-whisper

**Provides**

- Efficient Whisper inference using CTranslate2
- CPU/GPU deployment choices
- Word timestamps and batching options

**Adoption decision**

Selected as the default on-prem batch/local STT engine behind an OVOS or direct adapter. Managed STT remains an optional provider profile for lower infrastructure effort or stronger streaming quality.

### 4.2 WhisperX

**Official source**

- https://github.com/m-bain/whisperX

**Provides**

- Whisper transcription with word-level alignment
- Speaker diarization integration
- Faster Whisper base

**Adoption decision**

Selected for physical-room post-meeting transcription when word alignment and speaker reconciliation are required. Not needed for every short voice command.

### 4.3 pyannote.audio

**Official source**

- https://github.com/pyannote/pyannote-audio

**Provides**

- Speaker diarization pipelines and embeddings

**Adoption decision**

Selected as the replaceable diarization adapter for physical meetings. Diarization labels speakers; Seleric still needs participant resolution through attendee metadata, explicit introductions, or review.

### 4.4 Piper / phoonnx and OVOS TTS Server

**Official sources**

- https://github.com/rhasspy/piper
- https://github.com/OpenVoiceOS/ovos-tts-server

**Provides**

- Fast local neural TTS
- Stateless HTTP serving through OVOS
- Provider-compatible endpoint shapes

**Adoption decision**

Selected as the low-cost on-prem TTS profile. A managed TTS provider can be configured when voice quality or streaming latency is more important.

## 5. Intent recognition and deterministic language

### 5.0 Speech-to-Phrase

**Official source**

- https://github.com/OHF-Voice/speech-to-phrase

**Provides**

- Fast local recognition of a known phrase set rather than general transcription
- Custom sentence/list input
- Finite-state language model generation and fuzzy correction
- Wyoming container packaging in its standard Home Assistant deployment

**Adoption decision**

Run a short adapter spike for the six fixed executive commands. It may provide lower latency and fewer unintended intents than open-ended STT. Do not make it a V1 dependency until the team proves that Seleric custom sentence compilation can be operated without coupling the business platform to Home Assistant. Meeting transcription still uses an open-ended engine.

### 5.1 HassIL

**Official source**

- https://github.com/home-assistant/hassil

**Provides**

- Sentence-template intent recognition used by Home Assistant Assist
- Slot/list expansion and deterministic matching

**Adoption decision**

Strong candidate for V1 bounded executive intents. Use a configuration-defined intent catalogue and examples. Add a small statistical fallback classifier only for paraphrases not covered by templates.

### 5.2 Rasa Open Source

**Official source**

- https://github.com/RasaHQ/rasa

**Provides**

- NLU, intents/entities, dialogue policies, and custom actions

**Adoption decision**

Not selected initially. Its dialogue/runtime surface is larger than needed for six bounded executive commands. Reconsider if the conversational domain becomes broad, multi-turn, and entity-heavy.

### 5.3 scikit-learn / fastText

**Official sources**

- https://scikit-learn.org/
- https://fasttext.cc/

**Provide**

- Lightweight text classification

**Adoption decision**

Use as an optional fallback intent strategy. The classifier must return calibrated confidence and route low-confidence text to clarification/failure, not to the closest action automatically.

### 5.4 Jinja2

**Official source**

- https://jinja.palletsprojects.com/

**Provides**

- Deterministic parameterized text rendering

**Adoption decision**

Selected. Templates receive typed, validated facts. They cannot query data, change rankings, or add unsupported causal claims.

## 6. Forecasting, anomaly detection, and model lifecycle

### 6.1 StatsForecast

**Official source**

- https://nixtlaverse.nixtla.io/statsforecast/
- https://nixtlaverse.nixtla.io/statsforecast/docs/tutorials/anomalydetection.html

**Provides**

- Fast statistical forecasting models
- Seasonal-naive, AutoETS, AutoARIMA, Theta, MSTL and related methods
- Probabilistic prediction intervals
- Cross-validation/backtesting
- Time-series anomaly detection through fitted intervals

**Adoption decision**

Selected as the primary V1 forecasting library. Model selection is metric-profile configuration plus walk-forward backtesting. The deterministic baseline is always retained as a fallback.

### 6.2 XGBoost

**Official source**

- https://xgboost.readthedocs.io/

**Provides**

- Gradient-boosted tree models for supervised forecasting/classification with engineered features

**Adoption decision**

Optional for selected high-value metrics only after it beats a simple baseline out of sample. Do not train one XGBoost model for every ontology node by default.

### 6.3 PyOD

**Official source**

- https://pyod.readthedocs.io/

**Provides**

- A broad collection of outlier-detection algorithms under common APIs

**Adoption decision**

Optional strategy for selected multivariate/state cases. V1 default anomalies come from robust thresholds, change detection, and forecast residual/prediction intervals.

### 6.4 MLflow

**Official source**

- https://mlflow.org/docs/latest/ml/model-registry/

**Provides**

- Model metadata, versioning, aliases, lineage, and promotion lifecycle

**Adoption decision**

Not required for the first few models. Begin with a small model-version table and object artifacts. Introduce MLflow when model count, promotion frequency, or multiple teams justify it.

### 6.5 Feast / Hopsworks

**Official sources**

- https://docs.feast.dev/
- https://docs.hopsworks.ai/

**Provide**

- Offline/online feature definitions, retrieval, and serving

**Adoption decision**

Not selected for V1. Seleric already has ClickHouse/Cube and only needs scheduled executive-state features. Use materialized state tables and versioned feature definitions. Add an online feature store when multiple low-latency models need consistent point-in-time feature reuse.

## 7. Ontology and causal analysis

### 7.1 PostgreSQL plus NetworkX

**Official sources**

- https://www.postgresql.org/docs/
- https://networkx.org/documentation/stable/reference/classes/digraph.html

**Provides**

- Durable versioned node/edge/config data in PostgreSQL
- Directed graph objects, node/edge attributes, traversal, topological checks, and path algorithms in NetworkX

**Adoption decision**

Selected. PostgreSQL is source of truth; each published configuration compiles to an immutable in-memory NetworkX graph for runtime traversal. This avoids a graph-database dependency while preserving extension to new node/edge types.

### 7.2 Neo4j

**Official source**

- https://neo4j.com/docs/

**Adoption decision**

Not selected for V1. Introduce only when graph-native querying, multi-hop interactive exploration, graph algorithms, or temporal relationship volume demonstrates a material advantage over PostgreSQL plus NetworkX.

### 7.3 DoWhy

**Official source**

- https://www.pywhy.org/dowhy/
- https://www.pywhy.org/dowhy/user_guide/causal_tasks/root_causing_and_explaining/

**Provides**

- Causal effect estimation, graph validation/diagnosis, structural causal models, interventions, counterfactuals, and anomaly attribution when a causal graph and mechanisms are justified

**Adoption decision**

Optional later strategy. A dependency edge is not proof of causality. V1 emits evidence-backed `RootCauseHypothesis` objects using declared dependencies, timing, anomaly concurrence, and completeness. It calls them “suspected drivers.” DoWhy can be enabled per validated causal subgraph after assumptions and training data are reviewed.

## 8. Intervention ranking

### 8.1 Configurable weighted ranking

Implement as small Seleric domain code using explicit normalized components:

```text
severity
financial_exposure
urgency
evidence_confidence
data_confidence
founder_leverage
```

Hard eligibility rules run before scoring. Root-cause deduplication runs before the final top-three limit.

### 8.2 TOPSIS / MCDA libraries

Candidate projects:

- https://scikit-criteria.quatrope.org/
- https://pymcdm.readthedocs.io/

**Adoption decision**

Optional strategy plugin, not a platform dependency. TOPSIS does not solve missing goals, bad normalization, candidate eligibility, owner state, or duplicate root causes. A transparent weighted policy is more auditable for V1.

## 9. Meeting capture and intelligence

### 9.1 Vexa

**Official source**

- https://github.com/Vexa-ai/vexa
- https://docs.vexa.ai/

**Provides**

- Open-source/self-hosted meeting bots for Google Meet, Microsoft Teams, Zoom, and Jitsi
- Real-time transcripts and recordings
- REST APIs, dashboard, scoped tokens, modular services, and MCP support
- Docker/Compose and Kubernetes deployment paths

**Adoption decision**

Selected as an optional online-meeting adapter. Do not deploy its full agent runtime for the physical table prototype. Use only the meeting-capture/transcript APIs when virtual meeting auto-join becomes required.

### 9.2 Physical-room capture

No meeting-bot product replaces local microphone capture. The Pi Meeting Recorder writes segmented audio with checksums and uploads it to object storage. WhisperX/pyannote process the audio asynchronously.

### 9.3 spaCy

**Official source**

- https://spacy.io/usage/rule-based-matching

**Provides**

- EntityRuler, Matcher, PhraseMatcher, and DependencyMatcher
- Custom trainable NER/classification later

**Adoption decision**

Selected for V1 controlled extraction. Rules and dictionaries recognize owner references, commitment language, decision language, deadlines, products, people, and certified metric aliases. Human review resolves ambiguity. Train NER only after enough corrected examples exist.

### 9.4 dateparser / Duckling

**Official sources**

- https://dateparser.readthedocs.io/
- https://github.com/facebook/duckling

**Provide**

- Relative and natural-language date/time parsing

**Adoption decision**

Use behind a `DeadlineParser` adapter. Store the original text, parsed timestamp, timezone, parser version, and confidence/ambiguity state.

## 10. Durable background work

### 10.1 Procrastinate

**Official source**

- https://procrastinate.readthedocs.io/en/stable/

**Provides**

- Open-source PostgreSQL-based Python task processing
- Future/periodic jobs, retries, priorities, locks, and async workers

**Adoption decision**

Selected for V1 state refresh, transcription, extraction, verification, and notification jobs. It reuses PostgreSQL and avoids Redis/Kafka/Temporal operations.

### 10.2 Temporal

**Official source**

- https://docs.temporal.io/

**Adoption decision**

Not required for the initial volume and simple commitment state machine. Reconsider when workflows span many services/days, require complex compensation, high-scale signals, or strict durable replay beyond the PostgreSQL job/outbox design.

## 11. Admin/control plane

### 11.1 Appsmith

**Official source**

- https://docs.appsmith.com/getting-started/setup/installation-guides

**Provides**

- Self-hosted internal application builder
- Docker and Kubernetes deployment
- Forms, tables, workflow/API integration, authentication, and Git-based app versioning options

**Adoption decision**

Selected to accelerate the V1 admin and review surfaces. Appsmith calls the Control Plane and Meeting APIs; it does not connect directly to production tables for configuration writes.

### 11.2 ToolJet / React Admin / Directus

**Official sources**

- https://docs.tooljet.com/
- https://marmelab.com/react-admin/
- https://docs.directus.io/

**Adoption decision**

Valid alternatives. React Admin offers more product-code control but requires more frontend work. Directus is useful for CRUD but should not become the business-rule source. Appsmith is chosen for speed and can later be replaced because admin behavior is behind APIs.

## 12. API, domain, and configuration foundation

### 12.1 FastAPI and Pydantic

**Official sources**

- https://fastapi.tiangolo.com/
- https://docs.pydantic.dev/

**Provide**

- Typed request/response models, OpenAPI, validation, async APIs, and dependency injection patterns

**Adoption decision**

Selected for all Python services.

### 12.2 SQLAlchemy and Alembic

**Official sources**

- https://docs.sqlalchemy.org/
- https://alembic.sqlalchemy.org/

**Adoption decision**

Selected for PostgreSQL persistence adapters and migrations. Domain objects remain independent from ORM models.

### 12.3 OpenTelemetry

**Official source**

- https://opentelemetry.io/docs/

**Adoption decision**

Selected for traces, metrics, and log correlation. Export to Azure Application Insights or the on-prem Grafana stack.

## 13. Agent-development accelerators

### 13.1 DeepSeek Harness

**Official source**

- https://github.com/deepseek-ai/deepseek-harness

**Provides**

- Open-source plugin-oriented agent harness and web workbench

**Official status concern**

The repository identifies itself as a developer preview and warns of compatibility-breaking changes.

**Adoption decision**

Use only in the Seleric Agent Lab for:

- Replaying intent and tool-selection cases
- Building adapters/plugins against test environments
- Comparing optional models
- Generating test cases
- Inspecting agent trajectories

Do not place it in the production deterministic decision path.

### 13.2 NVIDIA-labs OO Agents (NOOA)

**Official source**

- https://github.com/NVIDIA-NeMo/labs-OO-Agents

**Provides**

- Python object-oriented agent definitions with typed state, methods, and contracts
- Deterministic Python method bodies alongside optional LLM-driven methods

**Adoption decision**

Use its OOP concepts and optionally test typed meeting-extraction/summarization in a sandbox. Production V1 domain methods remain deterministic ordinary Python. Generated-code execution is not allowed against production credentials or databases.

## 14. Azure low-cost services and on-prem equivalents

| Capability | Azure | On-prem/open-source |
|---|---|---|
| Container runtime | Azure Container Apps | Docker/Podman Compose |
| Periodic/on-demand worker | Container Apps Jobs or worker app | Procrastinate worker |
| PostgreSQL | Azure Database for PostgreSQL Flexible Server | PostgreSQL |
| Object storage | Azure Blob Storage | MinIO |
| Identity | Microsoft Entra ID | Keycloak/existing OIDC |
| Secrets | Azure Key Vault | SOPS/Vault |
| Telemetry | Application Insights/Log Analytics via OTel | OTel Collector + Prometheus/Loki/Grafana |
| Image registry | Azure Container Registry | Harbor/GitHub Container Registry/private registry |
| TLS/ingress | Container Apps ingress/Application Gateway as needed | Caddy/Traefik |
| STT/TTS | Azure Speech or another provider adapter | OVOS STT/TTS + Faster Whisper/Piper |

Azure Container Apps supports internal ingress, service discovery, revisions, jobs, and scale-to-zero patterns. PostgreSQL Flexible Server supports burstable tiers, stop/start cost controls, backups, encryption, and private networking. The service architecture does not depend on Azure-specific domain APIs, so both deployment profiles use the same service contracts.

## 15. Final build-versus-reuse boundary

### Reuse

- Voice capture/listener/plugin shell
- Wake-word and VAD models
- STT/TTS engines and servers
- Statistical forecasting algorithms
- Generic anomaly libraries
- Graph traversal library
- Meeting-bot capture for online calls
- Transcription/diarization libraries
- NLP matching infrastructure
- PostgreSQL task queue
- Admin UI builder
- Authentication, object storage, telemetry, and container runtime

### Build and retain as proprietary Seleric platform

- Certified metric adapter and provenance rules
- Business ontology object model
- Goal, ownership, and escalation registry
- Versioned derived-state definitions
- Node-health policies
- Evidence-backed root-driver hypotheses
- Intervention templates and prerequisites
- Founder eligibility and priority ranking
- Decision traces and deterministic response contracts
- Meeting business semantics
- Commitment and verification rules
- Configuration validation/simulation/publish/rollback
- Admin domain APIs

This boundary delivers the target quickly without allowing a third-party framework to become the source of business truth or decision policy.


# 15. Additional plug-and-play evaluations

## 15.1 Directus

Official documentation:

- https://docs.directus.io/user-guide/user-management/permissions
- https://docs.directus.io/reference/system/flows
- https://docs.directus.io/guides/headless-cms/content-versioning
- https://directus.com/resources/directus-v12-license-change

Directus provides a generated Data Studio, APIs, policy-based permissions, revisions/content versions and event/scheduled flows. It could accelerate CRUD configuration screens. It is not selected for the V1 control plane because Seleric still needs domain validation, historical simulation, approval, runtime bundle compilation and adapter validation, and Directus v12 licensing is source-available with organization/usage conditions that need legal review. It remains a viable UI/data-management alternative behind the same domain APIs.

## 15.2 Meetily, MeetScribe, MeetMind and zabt.ai

Project references:

- https://github.com/Zackriya-Solutions/meetily
- https://github.com/pretyflaco/meetscribe
- https://github.com/openintelligence-labs/meetmind
- https://github.com/afeef/zabt-ai

These projects demonstrate reusable local capture, Whisper/Parakeet transcription, pyannote diarization, local storage and meeting UI patterns. They are evaluated as implementation references or transcription spikes, not deployed wholesale: their desktop/system-audio assumptions and summarization-centric data models do not replace Seleric's Pi capture, ontology linking, human review, typed commitments and evidence verification. `zabt.ai` is closest to a deployable transcription stack but would duplicate PostgreSQL/object storage/workers already required by Seleric.

## 15.3 Archived Wyoming Satellite caution

The Home Assistant `wyoming-satellite` repository was archived on January 27, 2026 and points users to newer Linux Voice Assistant/ESPHome paths. It is therefore not selected as the base for a new September prototype. The standalone Wyoming openWakeWord service can still be used as an adapter if needed, but OpenVoiceOS/raspOVOS is the selected edge platform.
