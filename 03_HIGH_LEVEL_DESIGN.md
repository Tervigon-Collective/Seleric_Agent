# High-Level Design

## 1. Architectural objective

The platform must preserve the entire V1 feature set while avoiding duplicate orchestration, data and ML infrastructure. The chosen HLD has six logical services plus existing Seleric data systems. Each service owns a cohesive business capability and can be deployed, scaled and replaced independently.

The HLD intentionally separates:

- real-time voice interaction
- continuous state computation
- deterministic decisioning
- long-running meeting processing
- administrative configuration
- existing certified analytics

## 2. System context

```mermaid
flowchart LR
    Founder[Founder / Executive]
    Admin[Administrator / Analyst]
    Employee[Meeting Participant]

    subgraph Office[Office Edge]
        Device[Seleric Voice Node<br/>Raspberry Pi + OpenVoiceOS]
    end

    subgraph SelericPlatform[Seleric Voice Platform V1]
        Voice[Voice Orchestrator]
        State[Business State Service]
        Insight[Insight Decision Service]
        Meeting[Meeting Intelligence Service]
        Control[Control Plane Service]
        AdminUI[Appsmith Admin UI]
    end

    subgraph Existing[Existing Seleric Data Plane]
        MCP[Seleric MCP]
        Cube[Cube Semantic Layer]
        CH[(ClickHouse / Existing Marts)]
    end

    subgraph Shared[Shared Platform]
        PG[(PostgreSQL)]
        Obj[(S3 / MinIO / Azure Blob)]
        Queue[PostgreSQL Task Queue]
        IdP[Identity Provider]
        Obs[OpenTelemetry / Monitoring]
        VoiceProviders[STT and TTS Providers]
    end

    Founder <--> Device
    Employee --> Device
    Admin --> AdminUI
    AdminUI --> Control

    Device <--> Voice
    Device --> Meeting
    Voice <--> VoiceProviders

    Voice --> Insight
    Voice --> Meeting
    State --> MCP
    MCP --> Cube
    Cube --> CH
    Insight --> State
    Insight --> Control
    Meeting --> Control

    Voice --> PG
    State --> PG
    Insight --> PG
    Meeting --> PG
    Control --> PG
    Meeting --> Obj
    State --> Obj
    State --> Queue
    Meeting --> Queue
    Insight --> Queue

    IdP --> AdminUI
    IdP --> Control
    Obs -. telemetry .-> Voice
    Obs -. telemetry .-> State
    Obs -. telemetry .-> Insight
    Obs -. telemetry .-> Meeting
    Obs -. telemetry .-> Control
```

## 3. Service boundaries

### 3.1 Edge Voice Node

**Why it is separate:** It owns physical audio, wake word, buttons, LEDs, offline state and local recording. Its release and failure lifecycle differs from cloud services.

**Reused platform:** OpenVoiceOS.

**Custom code:** Seleric bridge skill, device enrollment client, LED/button integration, meeting recorder, config agent and health reporter.

### 3.2 Voice Orchestrator Service

**Why it is separate:** It is latency-sensitive and stateless apart from short dialogue references. It converts transcripts into allowlisted business commands and returns typed voice responses. It should not contain state-model training or meeting processing.

**Responsibilities:**

- device/session validation
- deterministic intent recognition
- slot and context resolution
- command-handler invocation
- `WHY` reference state
- voice-safe response envelope
- proactive notification stream

### 3.3 Business State Service

**Why it is separate:** It performs scheduled, CPU/data-intensive computations and has different scaling from real-time voice. It is the only service allowed to transform certified metrics into derived state and model outputs.

**Responsibilities:**

- active configuration resolution
- metric binding validation
- Seleric MCP queries
- feature calculation
- forecasting and anomaly strategies
- health computation
- state materialization
- model/backtest registry

### 3.4 Insight Decision Service

**Why it is separate:** It owns business decision policy, ontology traversal, candidate eligibility, founder prioritization and explanations. Keeping it separate prevents voice implementation from becoming the business brain.

**Responsibilities:**

- ontology graph load and validation
- unhealthy/positive node selection
- suspected root-driver attribution
- intervention template matching
- precondition and eligibility rules
- root-key consolidation
- deterministic ranking
- founder brief persistence
- evidence-backed explanation
- Jinja2 NLG
- proactive alert creation

### 3.5 Meeting Intelligence Service

**Why it is separate:** Audio files, batch transcription, diarization, extraction, review and deadlines are long-running and potentially GPU-backed. They should not affect interactive voice availability.

**Responsibilities:**

- meeting lifecycle API
- resumable audio ingest
- physical and online capture adapters
- transcript normalization
- diarization and participant resolution
- semantic extraction
- review queue
- commitment lifecycle
- verification scheduling and execution

### 3.6 Control Plane Service

**Why it is separate:** It is the only configuration-write boundary and must enforce schema, version, approval and audit policies. Runtime services consume published immutable revisions.

**Responsibilities:**

- typed configuration CRUD
- graph and reference validation
- metric-catalogue validation
- historical simulation
- approval and publication
- effective-date resolution
- rollback
- device registry
- audit/event outbox

### 3.7 Appsmith Admin UI

**Why it is separate:** It accelerates admin implementation while keeping business rules in the Control Plane API. The UI is replaceable and never becomes a source of truth.

## 4. Logical service architecture

```mermaid
flowchart TB
    subgraph Edge[Edge Deployment]
        Mic[Mic Array]
        AudioBroker[ALSA dsnoop / PipeWire Audio Broker]
        Listener[OVOS Dinkum Listener]
        Wake[openWakeWord Plugin]
        VAD[Silero VAD Plugin]
        Skill[Seleric Bridge Skill]
        Player[OVOS Audio / TTS]
        Recorder[Meeting Recorder]
        DeviceAgent[Device Config and Health Agent]
        LED[LED / Buttons]

        Mic --> AudioBroker
        AudioBroker --> Listener
        AudioBroker --> Recorder
        Listener --> Wake
        Listener --> VAD
        Listener --> Skill
        Skill --> Player
        DeviceAgent --> Skill
        LED <--> DeviceAgent
        LED <--> Recorder
    end

    subgraph Runtime[Backend Microservices]
        VO[Voice Orchestrator]
        BS[Business State Service]
        ID[Insight Decision Service]
        MI[Meeting Intelligence Service]
        CP[Control Plane Service]
        AU[Appsmith Admin]
    end

    subgraph Data[Data and Integration]
        SMCP[Seleric MCP]
        PG[(PostgreSQL)]
        CLICK[(ClickHouse)]
        OBJECT[(Object Storage)]
        TASKS[Procrastinate Workers]
        STT[STT Provider]
        TTS[TTS Provider]
    end

    Skill <--> VO
    VO <--> STT
    VO <--> TTS
    Recorder --> MI
    DeviceAgent <--> CP

    VO --> ID
    VO --> MI
    ID --> BS
    BS --> SMCP
    BS --> CLICK
    BS --> PG
    ID --> PG
    MI --> PG
    MI --> OBJECT
    MI --> TASKS
    BS --> TASKS
    ID --> TASKS
    CP --> PG
    AU --> CP
```

## 5. Synchronous and asynchronous paths

### Synchronous paths

- device heartbeat/config fetch
- transcript -> intent -> command
- latest health/brief retrieval
- explanation retrieval
- meeting start/stop acknowledgement
- admin reads and validation preview

### Asynchronous paths

- metric/state refresh
- forecast training/backtesting
- state recomputation after config publish
- founder brief refresh
- proactive notification delivery
- audio upload finalization
- transcription/diarization
- extraction
- commitment verification
- retention/deletion

All asynchronous paths use the PostgreSQL task queue. An API request returns a job/reference rather than holding a long HTTP connection.

## 6. Data ownership

| Data | Authoritative owner | Read consumers |
|---|---|---|
| Certified business metrics | Existing Seleric MCP/Cube/ClickHouse | State Service |
| Ontology, goals and policies | Control Plane/PostgreSQL | State, Insight, Voice, Meeting |
| Observation snapshots | Business State Service/ClickHouse | State, Insight, Admin |
| Derived states and forecasts | Business State Service/ClickHouse + metadata in PostgreSQL | Insight, Voice, Admin |
| Intervention candidates/briefs/traces | Insight Decision Service/PostgreSQL | Voice, Admin, Meeting verification |
| Voice dialogue references | Voice Orchestrator/PostgreSQL with short retention | Voice only |
| Audio/transcript artifacts | Meeting Service/Object Storage | Meeting review and authorized audit |
| Meetings/commitments/verification | Meeting Service/PostgreSQL | Insight, Voice, Admin |
| Config revisions and audit | Control Plane/PostgreSQL | All runtimes |

No service writes directly into another service’s owned tables. Cross-service changes use APIs and outbox/task events.

## 7. Runtime trust zones

```mermaid
flowchart LR
    subgraph UntrustedPhysical[Physical / Semi-trusted Zone]
        Pi[Enrolled Raspberry Pi]
    end

    subgraph Ingress[Ingress Zone]
        Gateway[Caddy / Azure Container Apps Ingress]
    end

    subgraph App[Application Zone]
        Voice2[Voice Orchestrator]
        State2[State Service]
        Insight2[Insight Service]
        Meeting2[Meeting Service]
        Control2[Control Plane]
    end

    subgraph DataZone[Protected Data Zone]
        PG2[(PostgreSQL)]
        Obj2[(Object Storage)]
        MCP2[Seleric MCP]
    end

    subgraph External[Optional External Providers]
        STT2[Managed STT/TTS]
        IdP2[Identity Provider]
    end

    Pi -- mTLS / short JWT --> Gateway
    Gateway --> Voice2
    Gateway --> Meeting2
    Gateway --> Control2
    Voice2 --> Insight2
    Insight2 --> State2
    State2 --> MCP2
    Voice2 --> PG2
    Insight2 --> PG2
    State2 --> PG2
    Meeting2 --> PG2
    Meeting2 --> Obj2
    Control2 --> PG2
    Pi -. selected profile .-> STT2
    IdP2 --> Gateway
```

## 8. Communication contracts

| Connection | Protocol | Notes |
|---|---|---|
| Edge -> Control Plane | HTTPS JSON | Enrollment, config, heartbeat, revocation |
| Edge -> Voice Orchestrator | HTTPS JSON; optional WebSocket for notifications | Transcript and typed response, not raw SQL |
| Edge -> Meeting Service | HTTPS multipart/resumable upload | Content hash and idempotency key per audio part |
| OVOS -> STT/TTS | OVOS plugin API/HTTP provider adapter | Profile-selected local/on-prem/cloud provider |
| Voice -> Insight | Internal HTTPS JSON | Latest brief/health/explanation APIs |
| Insight -> State | Internal HTTPS JSON | Immutable state snapshot references |
| State -> Seleric MCP | MCP transport through approved adapter | Certified metrics only |
| Services -> task queue | PostgreSQL | At-least-once, idempotent handlers |
| Admin -> Control Plane | HTTPS JSON | No direct database writes |
| Runtime -> observability | OTLP | Traces, metrics and logs |

## 9. HLD sequence: executive query

```mermaid
sequenceDiagram
    participant F as Founder
    participant E as OVOS Edge
    participant STT as STT Provider
    participant V as Voice Orchestrator
    participant I as Insight Service
    participant S as State Service
    participant TTS as TTS Provider

    F->>E: "Hey Seleric, what should I do today?"
    E->>E: Local wake + VAD
    E->>STT: Speech audio
    STT-->>E: Transcript + confidence
    E->>V: VoiceTurn(transcript, device, session)
    V->>V: Deterministic intent + context
    V->>I: GET latest founder brief
    alt brief is current
        I-->>V: Brief with 0..3 interventions
    else brief missing or expired
        I->>S: Get latest valid state snapshot
        S-->>I: State references
        I->>I: Generate candidates, filter, rank, render
        I-->>V: New brief
    end
    V-->>E: Typed response + speech_text + brief_id
    E->>TTS: speech_text
    TTS-->>E: Audio
    E-->>F: Spoken top interventions
```

## 10. HLD sequence: state refresh

```mermaid
sequenceDiagram
    participant Q as PostgreSQL Task Queue
    participant S as Business State Service
    participant C as Control Plane
    participant M as Seleric MCP
    participant CH as ClickHouse
    participant I as Insight Service

    Q->>S: refresh_state(config_revision, scope)
    S->>C: Resolve published config
    C-->>S: Nodes, bindings, goals, profiles
    S->>M: Certified metric queries
    M-->>S: Observations + provenance + freshness
    S->>S: Features, forecasts, anomalies, health
    S->>CH: Append immutable state history
    S->>Q: Enqueue generate_brief(snapshot_id)
    Q->>I: generate_brief(snapshot_id)
    I->>I: Graph traversal, candidates, filters, ranking
    I->>CH: Read referenced state details
    I->>I: Persist brief and trace
```

## 11. HLD sequence: meeting loop

```mermaid
sequenceDiagram
    participant F as Founder
    participant E as Edge Node
    participant M as Meeting Service
    participant O as Object Storage
    participant Q as Task Queue
    participant T as Transcription Adapter
    participant R as Reviewer/Admin
    participant V as Verification Adapter
    participant I as Insight Service

    F->>E: "Start this meeting"
    E->>M: Create meeting
    M-->>E: meeting_id + policy
    E->>E: Red LED + segmented capture
    loop audio segments
        E->>M: Upload part + checksum
        M->>O: Store immutable part
    end
    F->>E: Stop meeting / button
    E->>M: Finalize meeting
    M->>Q: Enqueue transcription
    Q->>T: Transcribe + diarize
    T-->>M: Normalized transcript
    M->>M: Participant resolution + deterministic extraction
    M-->>R: Review queue
    R->>M: Approve/correct commitments
    M->>Q: Schedule verification at deadline
    Q->>V: Execute registered rule
    V-->>M: Evidence + result
    M->>I: Material breach/opportunity event
```

## 12. Azure deployment HLD

```mermaid
flowchart TB
    PiA[Office Raspberry Pi]

    subgraph Azure[Azure Resource Group]
        ACA[Azure Container Apps Environment]
        VOCA[voice-orchestrator]
        SCA[business-state]
        ICA[insight-decision]
        MCA[meeting-intelligence]
        CCA[control-plane]
        AUI[Appsmith]
        PGF[(Azure PostgreSQL Flexible Server)]
        Blob[(Azure Blob Storage)]
        KV[Azure Key Vault]
        AI[Application Insights / Log Analytics]
        ACR[Azure Container Registry]

        ACA --- VOCA
        ACA --- SCA
        ACA --- ICA
        ACA --- MCA
        ACA --- CCA
        ACA --- AUI
        VOCA --> PGF
        SCA --> PGF
        ICA --> PGF
        MCA --> PGF
        CCA --> PGF
        MCA --> Blob
        SCA --> Blob
        KV --> VOCA
        KV --> SCA
        KV --> ICA
        KV --> MCA
        KV --> CCA
        AI -. telemetry .-> ACA
        ACR --> ACA
    end

    Seleric[Seleric MCP / Existing Data Plane]
    Speech[Azure Speech or Managed Voice Provider]

    PiA --> VOCA
    PiA --> MCA
    PiA <--> Speech
    SCA --> Seleric
```

## 13. On-prem deployment HLD

```mermaid
flowchart TB
    PiO[Office Raspberry Pi]

    subgraph Host[Linux Host / Small VM - Docker Compose]
        Caddy[Caddy Ingress]
        VO2[voice-orchestrator]
        BS2[business-state]
        ID2[insight-decision]
        MI2[meeting-intelligence]
        CP2[control-plane]
        AS2[Appsmith]
        PGO[(PostgreSQL)]
        MINIO[(MinIO)]
        STTO[OVOS STT Server + Faster Whisper]
        TTSO[OVOS TTS Server + phoonnx]
        OTEL[OTel Collector]
        GRAF[Grafana / Prometheus / Loki / Tempo]

        Caddy --> VO2
        Caddy --> MI2
        Caddy --> CP2
        Caddy --> AS2
        VO2 --> PGO
        BS2 --> PGO
        ID2 --> PGO
        MI2 --> PGO
        CP2 --> PGO
        MI2 --> MINIO
        BS2 --> MINIO
        OTEL --> GRAF
    end

    Seleric2[Seleric MCP / ClickHouse / Cube]

    PiO --> Caddy
    PiO <--> STTO
    PiO <--> TTSO
    BS2 --> Seleric2
```

## 14. Why this is the minimum viable microservice split

| Potential merge | Why not merge now |
|---|---|
| Voice + State | Voice is low-latency and stateless; state runs scheduled data/model jobs. A failed training job must not affect voice availability. |
| State + Insight | State owns factual transformations; Insight owns business policy. Independent validation and release cycles are important. |
| Meeting + Voice | Meeting is file/GPU/long-job heavy and handles sensitive artifacts; voice is interactive. |
| Control Plane + runtime service | Configuration writers require stronger authorization and immutable publication boundaries. |
| Admin UI + direct database | Direct writes would bypass validation, simulation, audit and referential integrity. |

A later scale event may split worker processes from their APIs without changing domain contracts. Conversely, local development may run all services in one Compose project; microservice contracts remain intact.

## 15. Critical failure behavior

| Failure | Required behavior |
|---|---|
| Wake word unavailable | Device enters visible degraded mode; physical meeting button remains functional. |
| STT unavailable | Use configured fallback; otherwise announce speech service unavailable. |
| Control Plane unavailable | Device may use last signed non-sensitive config until expiry. |
| Seleric MCP unavailable | Serve only a still-valid precomputed brief and disclose its age; otherwise refuse business answer. |
| State job partly fails | Persist valid node states, mark failed nodes unknown and prevent affected candidates. |
| Forecast model fails | Fall back to approved baseline strategy and mark model status degraded. |
| Insight generation fails | Return latest valid brief if within TTL; never synthesize priorities in Voice Service. |
| TTS unavailable | Use local fallback voice or show/emit text response through companion admin; do not lose trace. |
| Audio upload interrupted | Keep encrypted parts locally and resume by checksum. |
| Diarization uncertain | Send to review with unresolved speakers. |
| Verification integration unavailable | Retry; after policy threshold mark verification pending/system-error, not breached. |
