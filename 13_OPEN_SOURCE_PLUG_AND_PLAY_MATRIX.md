# Open-Source and Plug-and-Play Adoption Matrix

## 1. Decision rule

A project is adopted only when it replaces meaningful engineering work, fits a clean adapter boundary, does not duplicate an existing Seleric capability, and can be removed without rewriting the domain model.

Status meanings:

```text
ADOPT       deploy/use in V1
EMBED       use as a library inside a Seleric service
ADAPTER     support behind an interface, provider selected by configuration
EVALUATE    use in a development lab or spike
DEFER       useful later; no V1 deployment
REJECT_V1   does not match the physical-room or deterministic V1 requirement
```

## 2. Voice and edge systems

| System | What it already provides | Decision | V1 integration | Why not build it ourselves / key caveat |
|---|---|---|---|---|
| OpenVoiceOS / raspOVOS | Pi-ready voice runtime, listener, skill framework, wake/STT/TTS plugin architecture | ADOPT | Edge runtime plus custom Seleric bridge skill and recorder | Replaces a custom assistant state machine and plugin framework; keep its message bus local-only |
| openWakeWord OVOS plugin | Local wake-word inference and configurable thresholds | ADOPT | “Hey Seleric” provider on the Pi | Avoids always-on cloud audio; custom model still needs office QA |
| Silero VAD OVOS plugin | Local voice activity detection | ADOPT | Listener/VAD profile | Avoids custom VAD; thresholds remain device/profile configuration |
| Pipecat | Frame-based open-source real-time voice orchestration | ADAPTER | Embed inside Voice Orchestrator only if managed streaming/barge-in profile needs it | Do not deploy it as an additional platform when OVOS local interaction is sufficient |
| LiveKit Agents | WebRTC rooms, turn handling, interruption and telephony integrations | DEFER | Future transport adapter | Adds a server/control plane not needed for one office device |
| Home Assistant Wyoming Satellite | Earlier satellite runtime | REJECT_V1 | None | Repository was archived in January 2026; do not start the platform on an archived runtime |
| Managed Deepgram/Azure speech | Low-setup streaming STT/TTS | ADAPTER | Credential-isolated provider behind Voice Orchestrator | Useful fallback/primary during prototype; not a source of business reasoning |
| Faster Whisper + Piper/phoonnx | Self-hosted speech stack | ADAPTER | OVOS STT/TTS server profile | Low recurring cost; benchmark office latency and vocabulary before making it default |

## 2a. Agent orchestration and swarm reasoning [new, 2026-08-31]

| System | Existing capability | Decision | V1 integration | Why not build it ourselves / key caveat |
|---|---|---|---|---|
| **LangGraph** | Graph-based agent orchestration, `Command`-based handoffs, PostgreSQL-backed checkpointing, subgraphs | **ADOPT** | Swarm Coordinator inside `insight-decision-service`; orchestrates agent turns, never gains standing MCP/DB/write credentials | Was REJECT_V1 in the pre-2026-08-31 baseline because the six intents were deterministic; that premise changed. Replaces what would otherwise be a bespoke handoff/checkpoint state machine — a meaningfully larger build than any other adopted dependency in this matrix. |
| **`pgvector` (PostgreSQL extension)** | Vector similarity search inside PostgreSQL | **ADOPT (embed)** | Case-embedding storage and `find_similar_cases` query for collective memory | Not a new datastore to operate/back up/secure separately — an extension on the existing PostgreSQL, same category of decision as NetworkX-over-PostgreSQL for the ontology. |
| **Google Agent2Agent (A2A) protocol** | Cross-organization agent discovery/communication standard | **DEFER — tracked future adoption** | None in V1 | Solves cross-business agent-to-agent communication; V1's swarm is entirely internal to one brand's Insight Decision Service instance. **Adoption trigger:** a concrete cross-business agent-to-agent use case exists. The internal Agent Registry is shaped (`exposure_scope` field) so this is additive later, not a rewrite (doc 01 §3a.10). |
| LLM reasoning provider (Azure OpenAI / Anthropic / self-hosted OpenAI-compatible) | Managed or self-hosted LLM completion API | **ADAPTER** | `ReasoningProvider` port, Governor-scoped per call, no standing credential to platform data (doc 04 §4a) | Provider-agnostic by design; never given MCP, database, or write access — same discipline as STT/TTS provider adapters. |

## 3. Time-series, ML and state tooling

| System | Existing capability | Decision | V1 role | Adoption trigger / caveat |
|---|---|---|---|---|
| Polars/NumPy/SciPy | Window features and numeric primitives | EMBED | Rolling state, derivatives and robust dispersion | Core deterministic calculations |
| StatsForecast | Statistical baselines, intervals and time-series cross-validation | EMBED | Approved per-metric forecast profiles | Do not train every metric automatically; promote only after walk-forward backtest |
| Salesforce Merlion | Unified forecasting, anomaly, change-point, calibration, ensembles and evaluation | EVALUATE/ADAPTER | Optional strategy adapter and model-selection workbench | Useful to reduce model-library sprawl; not a separate production service |
| scikit-learn / PyOD | Standard/multivariate detectors | ADAPTER | Selected detector profiles only | Isolation Forest is not a universal default |
| Feast | Offline/online feature registry and materialization | DEFER | Future `FeatureProvider` adapter | Existing ClickHouse/state mart is sufficient until multiple online consumers need the same features |
| MLflow | Model lineage, versions, aliases, artifacts and serving | DEFER/ADAPTER | Future model-registry repository | V1 PostgreSQL registry + object store is enough for a small model set |
| DoWhy / pgmpy | Causal estimation and probabilistic graphs | DEFER | Only approved causal subgraphs after assumptions/data are validated | Declared dependency edges must not be presented as causal proof |
| TOPSIS/PyMCDM/scikit-criteria | MCDA algorithms | REJECT_V1 (unchanged) | None | The deterministic ranking formula this would have plugged into is itself retired in favor of agent-swarm prioritization (doc 01 §2.6) — adding a second numeric formula does not address why the founder replaced the first one. |

## 4. Ontology and data systems

| System | Existing capability | Decision | V1 role | Reason |
|---|---|---|---|---|
| Existing Seleric MCP + Cube | Certified metrics, dimensions, freshness, validation and provenance | ADOPT | Sole numerical source for executive state | Do not duplicate semantic definitions or expose raw SQL |
| PostgreSQL + NetworkX | Relational source of truth plus in-memory graph traversal | ADOPT | Ontology/config store and runtime graph | Meets current scale with no extra database |
| Apache AGE | openCypher graph extension inside PostgreSQL | DEFER/ADAPTER | Future graph repository | Add only when graph query complexity proves a need; avoids a separate Neo4j deployment |
| Neo4j | Dedicated graph database | REJECT_V1 | None | Duplicates storage/operations for a small declared ontology |
| Feast/Hopsworks | Feature platform | DEFER | None in V1 | State mart already gives point-in-time reproducibility |

## 5. Meeting systems

| System | Existing capability | Decision | V1 role | Reason / caveat |
|---|---|---|---|---|
| WhisperX / Faster Whisper | Long-form transcription, alignment and VAD | ADOPT | Meeting transcription adapter | Mature reusable processing core; overlapping speech remains imperfect |
| pyannote.audio | Speaker diarization | ADOPT | Diarization adapter | Participant identity is a separate resolution step |
| spaCy matchers/rulers + dateparser | Deterministic entity/pattern/deadline extraction | ADOPT | Evidence-linked semantic extraction | Human review remains mandatory for ambiguous commitments |
| Vexa | Self-hosted bots and real-time transcripts for Meet/Teams/Zoom/Jitsi | DEFER/ADAPTER | Future online-meeting capture | Does not solve a physical one-to-one meeting; do not deploy its agent layer |
| Meetily / MeetScribe / MeetMind | Local desktop capture/transcription/summaries | EVALUATE | Borrow capture/audio UX patterns | Desktop/system-audio assumptions and embedded summarizers do not match the Pi + structured verification architecture |
| zabt.ai | Dockerized self-hosted transcription/diarization/UI stack | EVALUATE | Potential rapid transcription spike | Bundles its own DB/object store/workers; deploying whole stack would duplicate Seleric platform services |

## 6. Admin, identity and operations

| System | Existing capability | Decision | V1 role | Caveat |
|---|---|---|---|---|
| Appsmith Community Edition | Self-hosted low-code admin/approval UI and API connectivity | ADOPT | Admin and meeting-review UI | Domain authorization and validation remain in Control Plane APIs; do not grant direct production-table writes |
| Directus | Data Studio, generated APIs, policies, revisions, versions and flows | DEFER | Alternative admin accelerator | Current source-available licensing and the risk of making CRUD schemas the business-rule source require legal/architecture review |
| Keycloak | OIDC/OAuth2/SAML, service accounts and centralized identity | ADOPT on-prem | Human/device/service identity | Use Entra ID where already available on Azure |
| Procrastinate | PostgreSQL-backed delayed/periodic tasks, retries and locks | ADOPT | State, meeting and verification workers | Wrapped behind `TaskQueue` so Temporal can replace it later |
| Temporal | Durable long-running workflow engine | DEFER | Future workflow adapter | Operationally unnecessary for a small number of versioned verification jobs |
| OpenTelemetry | Vendor-neutral traces, metrics and logs | ADOPT | End-to-end observability and decision trace correlation | Export to Grafana stack or Azure Monitor |
| Kafka/RabbitMQ/Redis | Broker/cache infrastructure | REJECT_V1 | None | PostgreSQL outbox/jobs and in-process bounded caches meet V1 load |
| Kubernetes/AKS | Container orchestration | REJECT_V1 | None | Container Apps or Docker Compose are sufficient |

## 7. Agent-development accelerators

**Changed 2026-08-31:** LangGraph is no longer grouped with the rejected runtime frameworks below — it is production-adopted (§2a) as the Seleric Swarm Layer's orchestration engine, since the trigger for that adoption (a real agent-swarm decision path) is now met. Dify and Flowise remain out of scope; they are visual/no-code agent builders aimed at a different problem than a code-owned, Governor-controlled internal swarm.

| System | Decision | Use |
|---|---|---|
| DeepSeek Harness | EVALUATE only | Replay voice scenarios, compare optional language models, generate tests and inspect trajectories; never a production dependency while it remains a developer preview |
| NVIDIA labs OO Agents | EVALUATE / design reference | Typed object-oriented agent experiments and extraction prototypes; generated-code execution stays isolated and credential-free |
| Dify / Flowise (no-code agent builders) | REJECT_V1 | Aimed at visually-assembled agent flows for general use cases; the Seleric swarm needs code-owned agent roles, a typed Blackboard, and a Governor enforcement point that these tools do not provide as a primitive |
| LangGraph | **See §2a — ADOPT, production dependency** | Moved out of this rejected group on 2026-08-31; retained here only as a pointer so this section's history is legible |

## 8. Final reuse composition

```text
raspOVOS/OpenVoiceOS
  + openWakeWord/Silero
  + provider adapters for STT/TTS
  + FastAPI/Pydantic microservices
  + existing Seleric MCP/Cube/ClickHouse
  + PostgreSQL/NetworkX
  + Polars + robust rules + StatsForecast (selected metrics)
  + LangGraph (swarm orchestration) + pgvector (case retrieval)      [new, 2026-08-31]
  + WhisperX/pyannote/spaCy/dateparser
  + Procrastinate
  + Appsmith
  + Keycloak/Entra
  + OpenTelemetry
```

The custom Seleric core is intentionally limited to ontology/goals/configuration, state policies, the Blackboard/agent-role definitions and Governor policy, meeting semantics and commitment verification. Those are the differentiating business objects that no off-the-shelf project can supply correctly for Tilting Heads — and, as of 2026-08-31, the Governor and the ontology-grounded agent-communication design specifically are Seleric's stated differentiator versus a generic agent framework (doc 01 §3a.7, §3a.11).

## 9. Official references

- OpenVoiceOS technical manual: https://openvoiceos.github.io/ovos-technical-manual/
- raspOVOS: https://openvoiceos.github.io/raspOVOS/
- Pipecat: https://docs.pipecat.ai/
- LiveKit Agents: https://docs.livekit.io/agents/
- Merlion: https://opensource.salesforce.com/Merlion/latest/
- StatsForecast: https://nixtlaverse.nixtla.io/statsforecast/
- Feast: https://docs.feast.dev/
- MLflow: https://mlflow.org/docs/latest/ml/model-registry/
- Apache AGE: https://age.apache.org/
- WhisperX: https://github.com/m-bain/whisperX
- pyannote.audio: https://github.com/pyannote/pyannote-audio
- Vexa: https://github.com/Vexa-ai/vexa
- Meetily: https://github.com/Zackriya-Solutions/meetily
- zabt.ai: https://github.com/afeef/zabt-ai
- Appsmith: https://docs.appsmith.com/
- Directus: https://docs.directus.io/
- Keycloak: https://www.keycloak.org/documentation
- Procrastinate: https://procrastinate.readthedocs.io/en/stable/
- OpenTelemetry: https://opentelemetry.io/docs/
- DeepSeek Harness: https://github.com/deepseek-ai/DeepSeek-Harness
- NVIDIA labs OO Agents: https://github.com/NVIDIA-NeMo/labs-OO-Agents
- LangGraph: https://langchain-ai.github.io/langgraph/
- LangGraph swarm pattern: https://github.com/langchain-ai/langgraph-swarm-py
- pgvector: https://github.com/pgvector/pgvector
- Agent2Agent (A2A) protocol (deferred): https://github.com/google-a2a/A2A
