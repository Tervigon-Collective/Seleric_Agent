# Technology Stack and Deployment Options

## 1. Selection criteria

A technology is selected for V1 only when it:

1. Directly serves a required outcome.
2. Has a stable API or a replaceable adapter boundary.
3. Can run self-hosted or has a low-cost managed equivalent.
4. Reduces code we would otherwise build.
5. Is auditable and configurable.
6. Does not introduce a second overlapping control plane.
7. Can be operated by the current engineering team.

## 2. Selected stack

| Layer | V1 selection | Why selected | Azure option | On-prem option |
|---|---|---|---|---|
| Edge OS | 64-bit Raspberry Pi OS / Debian | Stable Linux, broad USB/audio support | N/A | Same |
| Voice framework | OpenVoiceOS installer/core | Reuses listener, skill, audio and plugin architecture | Edge remains on Pi | Edge remains on Pi |
| Wake word | `ovos-ww-plugin-openwakeword` with custom model | Local, configurable ONNX/TFLite model and thresholds | Same | Same |
| VAD/listener | OVOS Dinkum Listener + Silero VAD plugin | Reuses wake/VAD/STT state machine | Same | Same |
| Microphone access | ALSA/PipeWire, ReSpeaker XVF3800 USB | Standard Linux audio and hardware DSP | Same | Same |
| STT primary profile | Provider adapter selected by config | Portability and fallback | Azure Speech or managed Deepgram | OVOS STT Server + Faster Whisper |
| TTS primary profile | Provider adapter selected by config | Portability and fallback | Azure Speech | OVOS TTS Server + phoonnx/Piper voices |
| Voice command bridge | Custom OVOS `SelericBridgeSkill` | Thin transcript/response bridge; no business reasoning on edge | Calls Azure services | Calls on-prem services |
| Intent parser | HassIL-style YAML grammar or equivalent internal deterministic parser | Dynamic sentence/slot/context configuration without LLM | Container App | Docker container |
| Closed-set command ASR (optional) | OHF Speech-to-Phrase adapter | Fast local recognition for the fixed command set; evaluate, do not require | Optional container | Optional Wyoming container |
| Backend language | Python 3.12 | ML/NLP ecosystem and existing data engineering fit | Same | Same |
| API framework | FastAPI + Pydantic v2 | Typed OpenAPI contracts, async I/O and validation | Container Apps | Docker Compose |
| Domain persistence | PostgreSQL 16 + SQLAlchemy 2 + Alembic | Config, audit, jobs, meetings and decision state | PostgreSQL Flexible Server | PostgreSQL |
| Analytics history | Existing ClickHouse | High-volume observations, state and prediction history | Existing/managed placement | Existing ClickHouse |
| Certified metrics | Existing Seleric MCP/Cube | Metric semantics, validation, provenance and access | Existing service | Existing service |
| Dataframe engine | Polars; pandas only where library requires | Efficient feature calculations | Same | Same |
| Forecasting | Robust baselines + StatsForecast; optional Merlion adapter | Statistical baselines, walk-forward CV; Merlion can unify anomaly/change-point experiments without becoming a separate service | Container/worker | Container/worker |
| Optional anomaly | scikit-learn / PyOD strategy | Selected multivariate use only | Same | Same |
| Runtime ontology | NetworkX | Simple directed graph and attributes | Same | Same |
| Causal plugin | DoWhy, disabled by default | Explicit causal assumptions and anomaly attribution when validated | Same | Same |
| NLG | Jinja2 | Reproducible and configurable spoken output | Same | Same |
| Task queue | Procrastinate over PostgreSQL | Periodic/delayed jobs, retries and locks without Redis | PostgreSQL-backed worker in Container Apps Jobs/app | Worker container |
| Meeting physical STT | WhisperX/Faster Whisper | Long-form ASR, word alignment, VAD | GPU VM/managed fallback | GPU/CPU server |
| Speaker diarization | pyannote.audio Community-1 | Self-hosted diarization and known-speaker-count support | GPU VM or premium adapter | GPU/CPU server |
| Meeting online capture | Vexa adapter, deferred/optional | Reuses bots and transcript API only when online-meeting auto-join becomes a real requirement | Hosted/self-hosted | Docker/Vexa |
| Semantic extraction | spaCy EntityRuler, Matcher, DependencyMatcher + date parser | Deterministic rules, controlled entities and evidence spans | Same | Same |
| Object storage | S3-compatible interface | Audio, transcripts, artifacts and checksums | Azure Blob Storage adapter | MinIO |
| Admin UI | Appsmith Community Edition | Rapid self-hosted internal tools and API integrations | Container App | Docker container |
| Identity | External OIDC provider | Avoid custom auth | Microsoft Entra ID | Authentik/Keycloak/existing IdP |
| Ingress | Managed ingress or Caddy | TLS, routing and policy | Azure Container Apps ingress | Caddy |
| Secrets | Secret-provider interface | No credentials in config DB/device | Azure Key Vault + managed identity | SOPS/age for V1; Vault if required |
| Observability | OpenTelemetry | Vendor-neutral trace/metric/log instrumentation | Application Insights/Log Analytics | OTel Collector + Grafana/Prometheus/Loki/Tempo |
| Container registry | OCI registry | Reproducible images and digest pins | Azure Container Registry | Harbor or GitHub Container Registry |
| CI/CD | GitHub Actions or existing CI | Tests, SBOM, scans and deployment | ACR/Container Apps deployment | Registry + Compose deployment |

## 3. OpenVoiceOS composition

### Edge packages

```text
ovos-core
ovos-dinkum-listener
ovos-audio
ovos-workshop / skill framework
ovos-ww-plugin-openwakeword
ovos-vad-plugin-silero
ovos-stt-plugin-server or selected STT plugin
ovos-tts-plugin-server or selected TTS plugin
SelericBridgeSkill
SelericDeviceAgent
SelericMeetingRecorder
```

OpenVoiceOS is treated as one edge platform, not a set of network-exposed microservices. Its message bus must remain localhost-only because the official bus client documentation states that the bus has no authentication and connected clients can control/read the assistant.

### Self-hosted STT

Use `ovos-stt-server` with `ovos-stt-plugin-fasterwhisper`. The server exposes a consistent HTTP service and can be protected behind internal ingress. The model is selected by voice profile and tested on TH vocabulary and office audio.

### Self-hosted TTS

Use `ovos-tts-server` with `ovos-tts-plugin-phoonnx` and a Piper-compatible voice. The older dedicated OVOS Piper plugin was archived and its own migration note directs users to phoonnx.

### Voice provider abstraction

```python
class SpeechToTextProvider(Protocol):
    async def transcribe(self, audio: AudioInput, context: STTContext) -> Transcript: ...

class TextToSpeechProvider(Protocol):
    async def synthesize(self, text: str, context: TTSContext) -> AudioOutput: ...
```

Configuration chooses a registered provider ID. It cannot specify arbitrary Python import paths.

## 4. Core Python stack

### API and contracts

- FastAPI for HTTP APIs.
- Pydantic v2 for request, response and configuration schemas.
- `httpx` for internal and MCP calls.
- SQLAlchemy 2 async for domain persistence.
- Alembic for migrations.
- `tenacity` or bounded internal retry utility for transient remote calls.

### Data/state calculations

- Polars for window features and transformations.
- NumPy/SciPy for numeric primitives.
- StatsForecast for approved statistical baselines and walk-forward validation.
- Merlion through an optional adapter when unified forecast/anomaly/change-point experiments are useful.
- scikit-learn/PyOD only through registered strategies.
- NetworkX for graph traversal.

### Meeting NLP

- WhisperX/Faster Whisper for transcript generation.
- pyannote.audio for diarization.
- spaCy `EntityRuler`, `Matcher` and `DependencyMatcher` for deterministic extraction.
- A timezone-aware date parser for explicit/relative deadlines.
- RapidFuzz for controlled entity resolution where exact aliases fail.

## 5. Why no feature-store platform in V1

Current Seleric capabilities already provide a semantic metric layer and ClickHouse-backed analytical facts. V1 needs a state mart, not a general online feature platform.

Use tables such as:

```text
metric_observation_snapshot
node_feature_snapshot
forecast_output
anomaly_event
node_health_snapshot
```

Every feature row includes `as_of_ts`, entity keys, config/version IDs and provenance. This gives point-in-time reproducibility without operating Feast/Hopsworks.

Feature-store adoption trigger:

- multiple independent online models consume the same features
- request-time feature lookup is required below approximately 100 ms
- training/serving skew becomes an observed problem
- point-in-time joins are repeatedly reimplemented

## 6. Why no graph database in V1

PostgreSQL stores graph configuration and NetworkX executes it in memory. This supports:

- node and edge attributes
- DAG/cycle validation
- ancestors/descendants
- shortest paths
- topological order
- centrality if later needed
- subgraph extraction

Graph database adoption trigger:

- millions of dynamic graph elements
- concurrent interactive graph queries become a primary workload
- temporal/knowledge graph patterns exceed relational usability
- many systems need direct graph access

## 7. Why no model-serving platform in V1

Forecast/state computations are scheduled and materialized. Voice requests read state; they do not invoke a large model fleet. The Business State Service worker can load pinned model artifacts and write outputs.

Model serving becomes justified if:

- request-time predictions are required
- independent applications call models directly
- autoscaling model endpoints are operationally necessary
- model count/traffic exceeds worker-based inference

## 8. Model registry decision

### V1

PostgreSQL registry:

```text
model_definition
model_version
model_artifact
model_validation_run
model_alias
```

Artifacts live in object storage and are referenced by checksum. Aliases include `champion`, `challenger`, `fallback` and `disabled`.

### Later

MLflow can replace the repository adapter when experiment volume requires its lineage, model versions, aliases and UI. The domain reads `ModelRegistry` interface, so migration does not alter state logic.

## 9. Task queue decision

Use Procrastinate through an internal `TaskQueue` interface. It stores jobs and locks in PostgreSQL and supports delayed/periodic tasks and retries. Queues:

```text
state_refresh
model_backtest
brief_generation
proactive_notification
meeting_transcription
meeting_extraction
commitment_verification
retention_cleanup
```

All handlers are idempotent and accept immutable IDs. A future Temporal/Prefect migration can replace the adapter when workflow complexity justifies it.

## 10. Azure low-cost mapping

| Capability | Azure service | Cost-control guidance |
|---|---|---|
| Microservices | Azure Container Apps | Use consumption/autoscaling; keep minimum replica only for latency-sensitive Voice/Insight services. Use jobs for batch workers. |
| PostgreSQL | Flexible Server | Burstable tier for dev; stop/start non-production; configure backup retention and private access. |
| Object storage | Blob Storage | Hot for active audio, cool/archive through lifecycle rules, delete by retention policy. |
| Secrets | Key Vault | Managed identities and least privilege. |
| Identity | Entra ID | Admin OIDC and groups. |
| Observability | Application Insights + Log Analytics | OpenTelemetry export and sampled traces with full retention for decision audit IDs. |
| Container images | ACR | Pin by image digest and enable scans/policies available to the organization. |
| Speech | Azure Speech | Optional configured STT/TTS provider; keep self-hosted fallback. |
| GPU batch | Azure VM/approved compute | Start only for meeting transcription windows, or use managed STT until volume justifies GPU. |

Azure Container Apps supports internal ingress, scheduled/on-demand jobs, revisions and scale rules, which is sufficient for the V1 microservices without AKS.

## 11. On-prem mapping

| Capability | On-prem component |
|---|---|
| Containers | Docker Compose or Podman Compose on one Linux host initially |
| Ingress | Caddy with TLS/mTLS and private DNS |
| Database | PostgreSQL 16 |
| Analytics | Existing ClickHouse/Cube/Seleric MCP |
| Object storage | Existing MinIO |
| STT | OVOS STT Server + Faster Whisper on CPU/GPU host |
| TTS | OVOS TTS Server + phoonnx/Piper |
| Identity | Existing IdP, Authentik or Keycloak |
| Secrets | SOPS/age encrypted environment for V1; Vault if dynamic secrets are needed |
| Observability | OTel Collector, Prometheus, Grafana, Loki and Tempo |
| Registry | Harbor or GHCR |

## 12. Hybrid recommended deployment

The most practical September path is hybrid:

- Pi/OpenVoiceOS in the office.
- Existing Seleric MCP/Cube/ClickHouse remain where they are.
- Five small backend services on the existing server or Azure Container Apps.
- PostgreSQL and existing MinIO/Azure Blob.
- Managed STT for interactive voice initially if self-hosted latency is insufficient.
- WhisperX/pyannote on a scheduled GPU/strong CPU host for meeting processing.
- Appsmith admin on the same application environment.

## 13. License and maintenance notes

1. Review code and model licenses separately.
2. Pin OVOS packages through a tested constraints/lock file.
3. Do not use the archived OVOS Piper plugin; use phoonnx or another maintained engine.
4. Do not expose OVOS STT/TTS compatibility endpoints without authentication at ingress.
5. pyannote Community-1 requires accepting model conditions and a Hugging Face token for download; cache approved weights internally.
6. WhisperX documents that diarization and overlapping speech are imperfect; human review is mandatory.
7. Vexa is not deployed for the physical-room V1; it remains a future online-meeting capture adapter, and its optional agent subsystem is outside the trusted decision path.
8. Appsmith writes only through the Control Plane API.
9. Procrastinate is wrapped to avoid coupling domain code to a queue library.
10. Every dependency is represented in an SBOM and upgrade-test cadence.

## 14. Official project references

- OpenVoiceOS: `https://github.com/OpenVoiceOS/OpenVoiceOS`
- OVOS Dinkum Listener: `https://github.com/OpenVoiceOS/ovos-dinkum-listener`
- OVOS openWakeWord plugin: `https://github.com/OpenVoiceOS/ovos-ww-plugin-openWakeWord`
- OVOS Faster Whisper plugin: `https://github.com/OpenVoiceOS/ovos-stt-plugin-fasterwhisper`
- OVOS STT server: `https://github.com/OpenVoiceOS/ovos-stt-server`
- OVOS TTS server: `https://github.com/OpenVoiceOS/ovos-tts-server`
- OHF Wyoming: `https://github.com/OHF-Voice/wyoming`
- StatsForecast: `https://github.com/Nixtla/statsforecast`
- Merlion: `https://opensource.salesforce.com/Merlion/latest/`
- PyOD: `https://pyod.dev/`
- NetworkX: `https://networkx.org/`
- DoWhy: `https://www.pywhy.org/dowhy/`
- WhisperX: `https://github.com/m-bain/whisperX`
- pyannote.audio: `https://github.com/pyannote/pyannote-audio`
- Vexa: `https://github.com/Vexa-ai/vexa`
- spaCy matching: `https://spacy.io/usage/rule-based-matching`
- Appsmith: `https://docs.appsmith.com/`
- Procrastinate: `https://github.com/procrastinate-org/procrastinate`
