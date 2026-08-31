# Overengineering Validation and Open-Source Reuse Review

## 1. Review objective

The original requirements correctly demand a physical voice loop, business ontology, predictive state, top-three founder interventions, meeting extraction and closed-loop verification. They also propose several tools at once: Feast/Hopsworks, XGBoost/TFT, Isolation Forest, DoWhy/BBN, TOPSIS, Rasa, Jinja2, LiveKit/Pipecat, separate STT/TTS providers, Neo4j/PostgreSQL and Temporal.

For a reliable V1, features are retained but unnecessary infrastructure and duplicate algorithms are removed. The source SRS is the baseline for the scope, while this document revises implementation choices where the same outcome can be delivered with fewer moving parts.

## 2. Engineering corrections before architecture selection

### 2.1 “Zero hallucination” must be defined precisely

Removing generative reasoning prevents fabricated narrative from entering the decision path, but it does not make statistical models infallible. Forecasts can be miscalibrated, anomaly thresholds can drift, ontology edges can be wrong and meeting rules can miss commitments.

The enforceable V1 guarantee is:

> Every spoken business fact, score and intervention must be generated from a typed, versioned and reproducible computation. Unsupported facts are never generated. Uncertain conclusions are labeled with confidence and evidence status.

### 2.2 A dependency graph is not automatically a causal graph

An edge such as `checkout_conversion -> net_revenue` can represent a known business dependency. It does not by itself identify a causal effect or rule out confounding. Therefore V1 uses the terms:

- `DEPENDENCY`
- `INFLUENCES`
- `MEASURES`
- `OWNS`
- `TARGETS`
- `VERIFIED_CAUSAL` only after a documented causal analysis is approved

V1 root analysis combines graph direction, temporal precedence, anomaly coincidence, contribution magnitude and configured influence weights. It reports a **suspected root driver**, not causal proof. DoWhy is retained as a pluggable Phase-2 strategy for approved causal graphs and suitable datasets.

### 2.3 A fixed 3-sigma rule is not universally valid

Residuals may be skewed, heteroskedastic, autocorrelated or seasonal. V1 therefore prefers backtested empirical/prediction intervals. A z-score is allowed only when residual diagnostics and calibration gates pass. Every anomaly profile specifies its scoring strategy.

### 2.4 One advanced model per ontology node is unnecessary

Many business series are short, sparse or structurally unstable. TFT and autoencoders add training, monitoring and explainability cost without guaranteed improvement. V1 uses a model-selection ladder:

1. Seasonal naive / historic average
2. EWMA / rolling robust baseline
3. AutoETS, MSTL, Theta or AutoARIMA through StatsForecast
4. XGBoost only for selected series with sufficient exogenous data
5. Deep models only after benchmarked improvement and operating justification

### 2.5 Forecast residuals already provide the first anomaly detector

Running an Isolation Forest for every metric duplicates signal when a calibrated forecast interval already identifies deviation. V1 uses forecast residual/interval anomalies by default. PyOD is an optional strategy for multivariate patterns where it demonstrates additional precision.

### 2.6 TOPSIS is not required to rank three interventions

TOPSIS is useful when decision criteria and ideal/anti-ideal points are well defined, but it is not inherently more objective than a documented weighted policy. V1 uses:

- hard eligibility/veto rules first
- normalized monotonic factors second
- configurable weighted geometric or additive score
- deterministic tie-breaking
- root-cause deduplication
- full decision trace

TOPSIS remains an optional registered ranking strategy.

### 2.7 A feature store is not required yet

Seleric already uses ClickHouse/Cube and exposes certified metrics through MCP. V1 derived features can be materialized in ClickHouse and served by the Business State Service. Feast/Hopsworks becomes justified only when multiple online models and applications require consistent point-in-time features at low latency.

### 2.8 Neo4j is not required to execute the ontology

PostgreSQL remains the authoritative configurable store for nodes, edges, goals and policies. NetworkX loads an active graph revision for traversal and validation. A graph database is introduced only when graph scale, temporal graph querying, multi-hop exploration or independent graph consumers make it materially valuable.

### 2.9 Temporal is unnecessary for the initial verification volume

V1 has a small number of state refreshes, transcript jobs and commitment deadlines. A PostgreSQL task queue provides durability, retries, periodic tasks and locks without another control plane. Temporal remains a future migration target for high-volume, long-lived, multi-step workflows.

### 2.10 Full voice-to-voice latency under 650 ms is not a reliable end-to-end requirement

Wake acknowledgement and barge-in can be sub-second. A complete remote STT -> service call -> evidence retrieval -> NLG -> TTS interaction has network and processing latency. V1 uses stage-level SLOs and precomputed briefs rather than promising an unrealistic single number.

## 3. Open-source reuse assessment

| System | What it can replace | Decision | Reason and boundary |
|---|---|---|---|
| OpenVoiceOS | Custom listener, wake/VAD/STT/TTS plugin manager, skills, audio playback, Pi packaging | **Adopt** | Strongest plug-and-play edge foundation; installable on Linux/Pi and modular. Keep its unauthenticated message bus bound to localhost only. |
| Open Home Foundation Linux Voice Assistant | Custom voice satellite | Alternative | Useful satellite implementation, but its primary integration is Home Assistant/ESPhome; not selected as the Seleric platform baseline. |
| Wyoming protocol | Custom voice-service wire protocol | Alternative | Simple JSONL + PCM protocol and mature voice components. Do not mix it into the primary OVOS path unless a provider adapter requires it. |
| OVOS openWakeWord plugin | Custom wake-word loop | **Adopt** | Loads custom ONNX/TFLite wake models and exposes thresholds/verifiers. |
| OVOS Dinkum Listener + Silero VAD | Custom VAD, end-of-turn and listener state machine | **Adopt** | Provides configurable wake, VAD, STT, silence and hybrid-listen behavior. |
| OVOS STT Server + FasterWhisper plugin | Custom self-hosted STT API | **Adopt as on-prem option** | Stable HTTP service and replaceable engine. Cloud provider remains a selectable adapter. |
| OVOS TTS Server + phoonnx/Piper voices | Custom self-hosted TTS API | **Adopt as on-prem option** | Stateless HTTP service and provider-compatible endpoints. The old OVOS Piper plugin is archived; use the documented phoonnx migration. |
| HassIL | Deterministic intent parser | Adoptable | YAML sentences, slots and context are a clean fit. V1 may use HassIL inside Voice Orchestrator even though the edge runtime is OVOS. |
| OHF Speech-to-Phrase | Closed-set local speech recognition for known commands | Optional spike | Can compile custom sentence templates into a fast local recognizer; useful for the six executive intents, but its standard packaging is Home Assistant-oriented. Keep behind the STT adapter rather than make it mandatory. |
| OvoScope | Custom end-to-end voice-skill test harness | **Adopt** | Deterministic multi-turn intent/skill tests without audio or network. |
| LiveKit Agents | WebRTC rooms, telephony and real-time agent orchestration | Defer | Valuable for multi-user rooms/telephony; unnecessary for one physical device using OVOS. |
| Pipecat | Custom frame-based streaming voice pipeline | Defer | Useful if OVOS/provider plugins fail latency or barge-in requirements; do not run both initially. |
| StatsForecast | Forecast model implementations, intervals, cross-validation | **Adopt** | Fast statistical baselines, probabilistic intervals and anomaly workflows under a common API. |
| PyOD | Multivariate anomaly algorithms | Optional plugin | Use only for profiles that outperform forecast-residual rules. Do not deploy 60 algorithms indiscriminately. |
| NetworkX | Runtime DAG representation and traversal | **Adopt** | Directed graphs with node/edge attributes are sufficient for V1 scale. PostgreSQL remains source of truth. |
| DoWhy | Validated causal attribution, interventions, counterfactuals | Phase 2 plugin | Requires explicit assumptions, suitable data and validation; not the default root-driver engine. |
| MLflow Model Registry | Model lineage, versions, aliases and tags | Defer/optional | Useful once model count and training workflows justify a separate service. V1 can use a PostgreSQL model registry and object artifacts. |
| Vexa | Online Meet/Teams/Zoom bot, transcript API and UI | **Adopt for online meetings only** | Avoids building browser bots. Do not deploy its agent layer; use capture/transcript primitives. |
| WhisperX | Batch ASR, word alignment and speaker labels | **Adopt for physical meetings** | Provides aligned long-form transcripts and integrates pyannote. Diarization limitations require review. |
| pyannote.audio Community-1 | Self-hosted diarization | **Adopt** | Open pipeline for local speaker diarization; participant identity remains a separate resolution step. |
| spaCy EntityRuler/DependencyMatcher | Rule-based participant, action and relationship extraction | **Adopt** | Supports deterministic rules and later hybrid statistical components. |
| Appsmith Community Edition | Custom admin frontend | **Adopt** | Self-hosted internal/admin UI, API integration and Git versioning. It must call the Control Plane API, not write domain tables directly. |
| Procrastinate | Redis/RabbitMQ/Celery/Temporal for V1 jobs | **Adopt** | PostgreSQL-backed async and periodic tasks, locks and retries with no new broker. Encapsulate behind `TaskQueue` interface. |
| Prefect | Data workflow UI/orchestration | Defer | Existing orchestration plus service workers is enough. Add only when cross-service data workflows need a dedicated control plane. |
| DeepSeek Harness | Production agent runtime | Lab only | Good for replay and tool evaluation but a developer-preview runtime is not a production dependency. |
| NVIDIA OO Agents | Production reasoning engine | Lab only | Useful for typed experiments; generated code must never run with production credentials. |

## 4. Chosen minimal platform

### Primary open-source voice path

```text
ReSpeaker USB microphone
-> OpenVoiceOS Dinkum Listener
-> OVOS openWakeWord plugin
-> OVOS Silero VAD
-> selected STT provider
-> Seleric Bridge Skill
-> Voice Orchestrator
-> selected TTS provider
-> OVOS Audio / speaker
```

The STT/TTS provider is configuration, not architecture:

- self-hosted: OVOS STT Server + Faster Whisper; OVOS TTS Server + phoonnx/Piper
- low-cost Azure: Azure Speech adapters
- managed fallback: Deepgram or another registered adapter

### Core decision path

```text
Seleric MCP observations
-> feature definitions
-> StatsForecast/robust baseline
-> anomaly and health policies
-> PostgreSQL ontology loaded into NetworkX
-> candidate templates
-> deterministic eligibility and ranking
-> Jinja2 response template
```

### Meeting path

```text
Physical meeting WAV
-> WhisperX/Faster Whisper
-> pyannote diarization
-> participant resolver
-> spaCy rules + deadline parser
-> review queue
-> commitment record
-> registered verification adapter
```

Online meetings can plug into Vexa and enter at the normalized transcript boundary.

## 5. Components explicitly not included in V1

- LLM business reasoning or free-form action generation
- Direct LLM-to-SQL or arbitrary SQL tools
- Kafka, RabbitMQ or Redis
- Kubernetes/AKS
- Neo4j
- Feast/Hopsworks
- Temporal
- distributed model-serving platform
- online reinforcement learning or active inference
- voice biometric authorization
- automatic campaign/budget write actions
- automatic commitment publication without review
- custom PCB/final enclosure

## 6. Open-source adoption controls

Every adopted project must pass:

1. License review, including model-weight licenses separately from code.
2. Pinned release/container digest and SBOM.
3. Vulnerability scan.
4. Local integration tests.
5. Performance test on selected hardware.
6. Data-egress review.
7. Replacement adapter or documented fallback.
8. Explicit owner and update cadence.

The platform never relies on an unversioned public community endpoint for production STT/TTS.
