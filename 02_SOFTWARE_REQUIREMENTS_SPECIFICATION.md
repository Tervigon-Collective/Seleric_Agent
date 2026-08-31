# Software Requirements Specification

## Project: Seleric Voice Node V1

**Target delivery:** September 30, 2026  
**System type:** Physical executive voice interface, business-state and deterministic decision platform, meeting commitment verification system  
**Primary organization:** Tilting Heads  
**Document status:** Implementation baseline

---

# 1. Purpose

The Seleric Voice Node V1 must allow the founder to speak to the live state of Tilting Heads, receive a concise and evidence-backed executive briefing, obtain no more than three founder-level interventions, request an explanation of those interventions, hear material risks and opportunities, and initiate a meeting that is converted into reviewed commitments whose outcomes are later verified.

The system must be platform-first. New business nodes, metrics, goals, features, forecasting policies, anomaly policies, interventions, intents, templates and verification rules must be added through versioned configuration wherever safe. New code is required only for genuinely new algorithms, provider adapters or side-effect integrations.

# 2. Source requirement alignment

The source requirements define a deterministic and auditable executive node, physical Raspberry Pi hardware, a business DAG, derived features, predictive models, top-three intervention ranking, fixed executive voice intents and a meeting-to-verification loop. The second source describes voice streaming, MCP access, Cube/ClickHouse state and asynchronous verification. V1 retains every essential user outcome while replacing duplicate or premature infrastructure with open-source platforms and smaller service boundaries.

# 3. Stakeholders

| Stakeholder | Interest |
|---|---|
| Founder | Correct, concise, timely and actionable company-state briefing |
| Business leadership | Goal tracking, escalation and explanation |
| Data engineering | Certified definitions, freshness, lineage and reproducibility |
| ML/data science | Versioned features, models, backtests and monitoring |
| Engineering | Stable APIs, modular services and safe extensibility |
| Operations/department owners | Clear ownership, commitments and verification |
| Security/admin | Device control, access policy, audit and recording governance |

# 4. Goals

1. Deliver a physical tabletop prototype by September end.
2. Answer six target interactions from live TH data.
3. Maintain a configurable ontology of business nodes, dependencies, metrics, goals and owners.
4. Continuously compute useful state: target deviation, rolling state, delta, velocity, acceleration, volatility, forecast, anomaly, regime and health.
5. Generate only eligible, distinct and evidence-backed founder interventions.
6. Explain every priority with source metrics, baseline, freshness, policies and uncertainty.
7. Capture one-to-one meetings, extract reviewed structured commitments and verify outcomes.
8. Provide an admin system for configuration, validation, simulation, publication and rollback.
9. Support self-hosted/on-prem and low-cost Azure deployment without changing domain code.
10. Remain extensible without introducing unnecessary tools or runtime dependencies.

# 5. Non-goals for V1

1. Full autonomous execution of ad, finance or inventory actions.
2. Voice biometric authorization.
3. Free-form general assistant conversations.
4. A complete enterprise ontology of every department before the prototype.
5. Proven causal inference for every business relationship.
6. Online reinforcement learning or active inference.
7. Custom hardware/PCB or the final Orb enclosure.
8. Replacing existing Seleric MCP, Cube, ClickHouse or canonical metric logic.
9. Automatic publication of meeting commitments without review.
10. Guaranteed correctness of statistical predictions; instead, uncertainty and validation are explicit.

# 6. Guiding principles

- **Certified truth first:** Business values come from certified Seleric metric contracts.
- **Observed versus inferred separation:** Raw observations, derived state, predictions and decisions are stored separately.
- **No free-form business reasoning:** Decision outputs are computed by registered strategies and templates.
- **Configurable, not arbitrary:** Admins select and parameterize pre-registered capabilities; admin configuration cannot execute arbitrary code.
- **Version everything:** Configuration, features, models, policies, templates and extraction rules are versioned.
- **Human approval at side-effect boundaries:** Meeting commitments and future operational actions require authorization.
- **Graceful uncertainty:** Missing targets, stale data or low confidence reduce eligibility rather than being filled by assumptions.
- **Portable infrastructure:** Domain services use interfaces for storage, voice, models and identity.
- **Microservice discipline:** Split only when latency, scaling, security or lifecycle differs.

# 7. System context and target interactions

The system must support:

1. **“Hey Seleric, how are we doing?”**
2. **“What are the three things I need to do today?”**
3. **“Why?”**
4. **“What are you worried about?”**
5. **“What opportunity are we missing?”**
6. **“Start this meeting.”**

Supported paraphrases are configured in the intent catalogue. Unsupported requests return a safe response and may be logged as candidates for future intent coverage.

# 8. Functional requirements

## 8.1 Edge device and audio

| ID | Requirement |
|---|---|
| EDGE-001 | The device shall run on Raspberry Pi 5 or an equivalent Linux host. |
| EDGE-002 | The device shall use a USB microphone array with AEC/noise-processing support and a powered speaker. |
| EDGE-003 | The device shall detect “Hey Seleric” locally without streaming continuous office audio. |
| EDGE-004 | Wake-word model, threshold, verifier and cooldown shall be configurable per device profile. |
| EDGE-005 | The device shall expose visible states: muted, idle, listening, processing, speaking, meeting-recording, offline and error. |
| EDGE-006 | A physical mute control shall disable upstream audio transmission and indicate the state. |
| EDGE-007 | A physical meeting-stop control shall terminate meeting capture even when network services fail. |
| EDGE-008 | The device shall spool unsent meeting audio locally with encryption and upload resumably after connectivity returns. |
| EDGE-009 | The edge runtime shall restart automatically after power loss or process failure. |
| EDGE-010 | The device shall periodically report heartbeat, software version, config revision, audio-device health and storage capacity. |
| EDGE-011 | Only one trusted local audio layer shall own or broker microphone access so listener and recorder do not conflict. |
| EDGE-012 | The OVOS message bus shall remain bound to localhost and never be exposed as a network API. |

## 8.2 Voice runtime

| ID | Requirement |
|---|---|
| VOICE-001 | The edge shall use a replaceable STT provider selected from an active voice profile. |
| VOICE-002 | The edge shall use a replaceable TTS provider selected from an active voice profile. |
| VOICE-003 | V1 shall support a self-hosted voice profile and at least one managed/low-cost cloud profile. |
| VOICE-004 | The voice loop shall support user interruption while TTS is playing. |
| VOICE-005 | The system shall acknowledge wake detection before a long-running backend operation. |
| VOICE-006 | Transcripts sent to backend services shall include device, user, session, locale, timestamps and STT confidence where available. |
| VOICE-007 | The system shall retain only the minimum conversational context necessary to resolve follow-ups. |
| VOICE-008 | The voice response shall contain a typed payload plus `speech_text`; TTS shall not receive raw database output. |
| VOICE-009 | When a configured provider fails, the system shall apply the profile’s fallback sequence or return an audible degraded-service message. |
| VOICE-010 | Provider credentials shall not be embedded in source code or device images. |

## 8.3 Intent and dialogue

| ID | Requirement |
|---|---|
| INTENT-001 | Intent routing shall be deterministic using configured sentence patterns, slots and context. |
| INTENT-002 | The active intent revision shall be publishable without modifying edge code. |
| INTENT-003 | The intent router shall map utterances to allowlisted command handlers only. |
| INTENT-004 | `WHY` shall resolve to the most recent brief/intervention reference in the current session. |
| INTENT-005 | Ambiguous follow-ups shall ask a bounded clarification rather than select an arbitrary object. |
| INTENT-006 | The router shall expose match confidence, matched pattern ID and extracted slots for audit. |
| INTENT-007 | Unsupported utterances shall not be passed to a free-form business agent in V1. |
| INTENT-008 | Intent test fixtures shall cover exact phrases, paraphrases, negative examples and multi-turn references. |

## 8.4 Certified metric access

| ID | Requirement |
|---|---|
| DATA-001 | All business observations shall be retrieved through the existing Seleric MCP or an explicitly approved canonical adapter. |
| DATA-002 | A metric request shall use metric IDs, supported dimensions and certified aggregation rules rather than natural-language SQL generation. |
| DATA-003 | Every observation set shall preserve query ID, metric catalogue version, time range, filters, timezone, refresh timestamp and warnings. |
| DATA-004 | The state service shall reject or downgrade observations with unsupported dimensionality, failed validation or stale freshness. |
| DATA-005 | Ratio metrics shall be recomputed from certified components where the metric contract requires ratio-of-aggregates semantics. |
| DATA-006 | Event-date and placement-date metrics shall not be silently combined. |
| DATA-007 | Provisional and final financial values shall be distinguished in all state and response objects. |
| DATA-008 | Metric bindings shall be validated against the current Seleric MCP catalogue before publication. |

## 8.5 Business ontology

| ID | Requirement |
|---|---|
| ONT-001 | The control plane shall manage versioned business nodes and typed directed edges. |
| ONT-002 | Initial node types shall include GOAL, VALUE_STREAM, FUNCTION, PROCESS, SYSTEM, PRODUCT, CHANNEL, METRIC, RISK, CONTROL, PERSON, ROLE and COMMITMENT. |
| ONT-003 | Initial edge types shall include CONTAINS, DEPENDS_ON, INFLUENCES, MEASURED_BY, TARGETS, OWNED_BY, AFFECTS, VERIFIED_BY and VERIFIED_CAUSAL. |
| ONT-004 | The graph shall be validated for invalid references, prohibited cycles by edge type and missing required bindings before publication. |
| ONT-005 | PostgreSQL shall store graph configuration; the runtime may load an active revision into NetworkX for traversal. |
| ONT-006 | Each monitored node shall support zero or more metric bindings, goals, owners and intervention templates. |
| ONT-007 | Edge semantics and confidence shall be explicit; dependency edges shall not be presented as proven causal effects. |
| ONT-008 | Graph revisions shall support draft, validate, simulate, publish and rollback. |

## 8.6 Goals and state

| ID | Requirement |
|---|---|
| STATE-001 | Each active goal shall define metric, entity scope, target type, target value/range, effective dates, criticality, owner and escalation policy. |
| STATE-002 | The state engine shall compute configured rolling windows, deltas and baselines from certified observations. |
| STATE-003 | Supported derived features shall include current, target deviation, comparison delta, velocity, acceleration, volatility and data completeness. |
| STATE-004 | Regime classification shall be configured as a registered strategy with explicit thresholds or change-point method. |
| STATE-005 | The state engine shall support metric-specific calculation profiles rather than one global formula. |
| STATE-006 | Each state snapshot shall be immutable and reference observation, feature, graph, goal and configuration revisions. |
| STATE-007 | A health score shall include direct goal attainment, data confidence and optional dependency contribution according to a versioned policy. |
| STATE-008 | A node with no valid target shall be `UNSCORED`, not automatically healthy or unhealthy. |
| STATE-009 | A node with stale or failed data shall be `UNKNOWN` or `DATA_ISSUE` according to policy. |
| STATE-010 | State snapshots shall support daily and hourly grains where the underlying certified metrics support them. |

## 8.7 Forecasting and anomaly detection

| ID | Requirement |
|---|---|
| ML-001 | Forecasting shall be optional per metric/node profile and shall not block non-forecast state computation. |
| ML-002 | Each forecasting profile shall specify candidate models, history requirement, horizon, seasonal frequency, backtest windows and acceptance thresholds. |
| ML-003 | V1 candidate models shall prioritize naive, seasonal-naive, EWMA and StatsForecast statistical models. |
| ML-004 | A model shall be promoted only after backtest metrics and interval coverage pass configured gates. |
| ML-005 | Forecast output shall include point estimate, lower/upper interval, horizon, model/version, scored time and validation status. |
| ML-006 | Default anomalies shall be detected through prediction/empirical interval breach or robust residual score. |
| ML-007 | Multivariate anomaly strategies such as Isolation Forest shall be registered only for selected profiles and validated separately. |
| ML-008 | Anomaly severity shall retain raw residual, normalized score, threshold and method. |
| ML-009 | Failed or poorly calibrated models shall fall back to the approved baseline and mark the forecast confidence accordingly. |
| ML-010 | Model artifacts shall be stored in object storage and referenced by immutable checksum. |

## 8.8 Health and root-driver analysis

| ID | Requirement |
|---|---|
| RCA-001 | The system shall separate observed anomaly, downstream impact and suspected root driver. |
| RCA-002 | V1 root-driver scoring shall combine graph ancestry, temporal precedence, anomaly concurrence, configured influence weight and contribution magnitude. |
| RCA-003 | The system shall consolidate multiple downstream symptoms under one root-driver key. |
| RCA-004 | Explanations shall disclose when the result is dependency-based rather than causally identified. |
| RCA-005 | A DoWhy strategy may be enabled only for graph revisions marked causal-approved and datasets passing validation. |
| RCA-006 | Alternative drivers considered and excluded shall be retained in the decision trace. |

## 8.9 Intervention generation and ranking

| ID | Requirement |
|---|---|
| DEC-001 | Interventions shall originate from versioned templates bound to node types, states or anomalies. |
| DEC-002 | Templates shall define action text, default owner, founder-required policy, preconditions, expected impact method and verification method. |
| DEC-003 | Candidate eligibility shall evaluate freshness, confidence, materiality, goal relevance, actionability, ownership, current status and founder leverage. |
| DEC-004 | Ineligible candidates shall be stored with rejection reasons for audit. |
| DEC-005 | Candidate scoring shall use a configured ranking strategy and normalized factors. |
| DEC-006 | Default factors shall include severity, financial exposure, urgency, evidence confidence, data confidence and founder leverage. |
| DEC-007 | Hard vetoes shall execute before ranking. |
| DEC-008 | Candidate ranking shall be deterministic for identical inputs and configuration. |
| DEC-009 | Root-driver duplicates shall be consolidated before selecting the top list. |
| DEC-010 | The founder brief shall contain zero to three eligible interventions; the system shall never pad the list. |
| DEC-011 | Each selected intervention shall include expected impact range or explicitly state that impact is unavailable. |
| DEC-012 | Recommendations dependent on unavailable inventory/procurement state shall contain an unmet precondition and shall not be stated as immediately executable. |

## 8.10 Executive responses

| ID | Requirement |
|---|---|
| RESP-001 | Response text shall be produced from versioned Jinja2 templates and typed data objects. |
| RESP-002 | Templates shall support locale, tone, maximum duration and compact/expanded modes. |
| RESP-003 | “How are we doing?” shall summarize company status, strongest/weakest monitored areas and freshness. |
| RESP-004 | “What should I do today?” shall state only selected founder interventions and no unrelated dashboard metrics. |
| RESP-005 | “Why?” shall include observed change, baseline, suspected driver, impact, confidence, freshness and founder requirement. |
| RESP-006 | “What are you worried about?” shall separate observed deterioration, forecast risk, commitment risk and data-quality risk. |
| RESP-007 | “What opportunity are we missing?” shall return eligible positive-variance candidates and operational preconditions. |
| RESP-008 | No template shall suppress material uncertainty, stale data or missing prerequisites. |

## 8.11 Proactive notifications

| ID | Requirement |
|---|---|
| ALERT-001 | Alert policies shall define severity, quiet hours, deduplication window, audience and delivery channel. |
| ALERT-002 | The insight service shall create a notification only after a candidate passes proactive eligibility rules. |
| ALERT-003 | The voice node shall receive notifications through authenticated SSE/WebSocket or bounded polling. |
| ALERT-004 | The device shall not speak sensitive alerts when muted, in a meeting or outside the configured presence policy. |
| ALERT-005 | All delivered, acknowledged, suppressed and expired alerts shall be audited. |

## 8.12 Meeting capture and transcription

| ID | Requirement |
|---|---|
| MTG-001 | “Start this meeting” shall create a meeting record and switch the edge into visible recording mode. |
| MTG-002 | Recording shall be stoppable by physical control and configured voice intent. |
| MTG-003 | The edge shall store audio in timestamped segments with checksums and resumable upload state. |
| MTG-004 | Physical-room transcription shall use a replaceable batch transcriber and diarizer. |
| MTG-005 | Online meetings may use a Vexa adapter and shall enter the system at the normalized transcript contract. |
| MTG-006 | The transcript shall preserve word/segment timestamps, speaker labels, confidence and source audio references where available. |
| MTG-007 | Diarization labels shall not be treated as participant identities until resolved. |
| MTG-008 | One-to-one meetings shall allow expected speaker count and preselected participant to improve resolution. |
| MTG-009 | Unresolved participants shall remain unresolved and enter review. |

## 8.13 Semantic extraction and review

| ID | Requirement |
|---|---|
| EXT-001 | V1 extraction shall use deterministic rules, controlled entity catalogues, dependency patterns and deadline parsing. |
| EXT-002 | Extracted types shall include participant, decision, commitment, owner, action, deadline, expected outcome, target object/metric, dependency, follow-up and open question. |
| EXT-003 | Every extracted object shall reference supporting transcript spans. |
| EXT-004 | Missing owner, deadline or target shall remain null and be flagged for review. |
| EXT-005 | Confidence shall be calculated by field and object. |
| EXT-006 | Low-confidence or conflicting objects shall enter review and shall not write operational commitments automatically. |
| EXT-007 | Admin-managed extraction patterns shall be versioned and testable against a labelled corpus. |
| EXT-008 | Future statistical/LLM extractors shall implement the same typed interface and remain subject to evidence and review policies. |

## 8.14 Commitments and verification

| ID | Requirement |
|---|---|
| COM-001 | Approved commitments shall define owner, action, deadline, expected outcome, target objects, source evidence and verification rule. |
| COM-002 | Commitment statuses shall include DRAFT, REVIEW_REQUIRED, APPROVED, IN_PROGRESS, VERIFIED, BREACHED, UNVERIFIABLE and CANCELLED. |
| COM-003 | Approval shall record approver, time and reviewed source spans. |
| COM-004 | Verification rules shall be registered adapters; arbitrary generated SQL is prohibited. |
| COM-005 | Supported V1 verification types shall include certified metric query, API/event check, document evidence, task status and human confirmation. |
| COM-006 | Verification jobs shall be durable, retryable and idempotent. |
| COM-007 | A verification result shall include evidence, evaluated condition, time window, rule version and status reason. |
| COM-008 | Material breached commitments shall become candidate risks for the next founder brief according to policy. |

## 8.15 Control plane and admin

| ID | Requirement |
|---|---|
| ADM-001 | Appsmith shall provide the admin UI; all writes shall go through Control Plane APIs. |
| ADM-002 | Configurable objects shall include devices, voice profiles, intents, ontology nodes/edges, metric bindings, goals, feature profiles, forecast/anomaly profiles, health policies, intervention templates, ranking policies, response templates, extraction rules, verification rules and alert policies. |
| ADM-003 | Each configurable object shall have schema validation, status, version, effective dates and audit metadata. |
| ADM-004 | Configuration lifecycle shall be DRAFT -> VALIDATED -> SIMULATED -> APPROVED -> PUBLISHED -> RETIRED. |
| ADM-005 | A published revision shall be immutable; changes create a new revision. |
| ADM-006 | Admin shall support rollback to a previous compatible revision. |
| ADM-007 | Publication shall execute referential, graph, metric-catalogue and test-fixture validations. |
| ADM-008 | Simulation shall show state/brief changes on historical data before publication where applicable. |
| ADM-009 | Roles shall restrict who can edit, approve, publish and view sensitive meeting records. |
| ADM-010 | Admin activity shall be written to an append-only audit stream. |

## 8.16 Security and identity

| ID | Requirement |
|---|---|
| SEC-001 | Each device shall have a unique enrolled identity and revocation state. |
| SEC-002 | Device-to-service requests shall use mTLS or short-lived signed tokens bound to device ID. |
| SEC-003 | Human admin authentication shall use an external identity provider and role/scoped authorization. |
| SEC-004 | Service-to-service access shall use managed identity/mTLS or short-lived service credentials. |
| SEC-005 | No raw warehouse credentials shall exist on the device. |
| SEC-006 | Secrets shall be stored in Azure Key Vault or an approved on-prem secret mechanism. |
| SEC-007 | Audio and transcript access shall be restricted and audited. |
| SEC-008 | Recording retention and deletion shall be configurable by meeting type. |
| SEC-009 | Voice shall not authorize consequential writes. |
| SEC-010 | Open-source services with unauthenticated internal buses/endpoints shall be bound to localhost/private network and protected by ingress policy. |

## 8.17 Observability and audit

| ID | Requirement |
|---|---|
| OBS-001 | All services shall emit OpenTelemetry traces, metrics and structured logs with correlation IDs. |
| OBS-002 | A voice turn trace shall link wake event, transcript, intent match, service calls, brief, response and TTS completion. |
| OBS-003 | A business decision trace shall retain observations, features, models, graph revision, candidates, exclusions, scores and selected outputs. |
| OBS-004 | A meeting trace shall link audio parts, transcription, extraction, review, commitment and verification. |
| OBS-005 | Dashboards shall monitor freshness, queue age, service latency, error rate, wake false activation, intent accuracy, forecast calibration, anomaly precision, extraction accuracy and verification backlog. |
| OBS-006 | Critical service and data-quality failures shall alert engineering separately from business alerts. |

# 9. Non-functional requirements

## 9.1 Latency SLOs

| Stage | Target |
|---|---:|
| Local wake acknowledgement P95 | <= 300 ms |
| Barge-in stop P95 | <= 350 ms |
| STT result after end of utterance P95 | <= 1.5 s using selected primary profile |
| Precomputed company/brief API P95 | <= 400 ms |
| Fresh bounded analysis API P95 | <= 3 s |
| First audible response for precomputed query P95 | <= 2.5 s |
| Meeting start acknowledgement P95 | <= 1 s |
| Post-meeting transcript for 30-minute recording | <= 10 minutes on selected compute profile |

## 9.2 Availability and resilience

- Core executive APIs: target 99.5% during office hours for V1.
- Device shall reconnect automatically and preserve last known non-sensitive config.
- Business responses shall never use cached state beyond the policy’s maximum age without disclosure.
- Meeting audio upload shall be resumable and idempotent.
- Background tasks shall use at-least-once delivery and idempotent handlers.

## 9.3 Data quality

- Metric freshness and catalogue revision are mandatory fields.
- A failed metric binding blocks the affected state but not unrelated nodes.
- State and brief generation must be partial-failure aware.
- Model forecasts require calibration status.
- Meeting extraction metrics must be measured on a labelled internal corpus.

## 9.4 Portability

Domain services shall run unmodified on:

- Docker Compose/Podman on Linux with PostgreSQL, MinIO and existing Seleric services.
- Azure Container Apps with PostgreSQL Flexible Server, Blob Storage, Key Vault and Application Insights.

Provider-specific behavior shall be isolated behind adapters.

# 10. Core data objects

1. Device and VoiceProfile
2. IntentDefinition and DialogueSession
3. BusinessNode and BusinessEdge
4. MetricBinding and ObservationSet
5. Goal and EscalationPolicy
6. FeatureDefinition and DerivedState
7. ForecastProfile, ModelVersion and ForecastOutput
8. AnomalyProfile and AnomalyEvent
9. HealthPolicy and NodeHealthSnapshot
10. InterventionTemplate and InterventionCandidate
11. FounderBrief and DecisionTrace
12. ResponseTemplate and Notification
13. Meeting, AudioPart, Participant and TranscriptSegment
14. ExtractedDecision, CommitmentDraft and Commitment
15. VerificationRule, VerificationAttempt and VerificationResult
16. ConfigurationRevision and AuditEvent

# 11. Traceability matrix for target interactions

| Interaction | Primary service | Required data/objects | Failure response |
|---|---|---|---|
| How are we doing? | Voice Orchestrator -> Insight Decision | Latest company health summary, freshness, strongest/weakest nodes | State unavailable/stale with exact scope |
| Three things today | Voice Orchestrator -> Insight Decision | Latest eligible founder brief, goals, candidates, ranking trace | Zero-to-three valid items; never fabricate |
| Why? | Voice Orchestrator -> Insight Decision | Current dialogue reference, decision trace, evidence | Ask which item when reference is ambiguous |
| Worried about? | Insight Decision | Negative state, forecast risk, breached commitments, data risks | Separate unavailable categories |
| Missing opportunity? | Insight Decision | Positive variance candidates, preconditions, goal relevance | State operational prerequisites explicitly |
| Start meeting | Voice Orchestrator -> Meeting Service + Edge recorder | Device/session, participant context, recording policy | Refuse/announce failure if recording cannot be guaranteed |

# 12. V1 acceptance criteria

1. Physical node reliably detects “Hey Seleric” at normal table distance under agreed office noise.
2. The six interactions run against real TH data/configuration, not mocked business responses.
3. Each spoken business response is linked to an immutable response payload and decision trace.
4. Founder priorities contain no more than three distinct root-driver interventions.
5. Stale/provisional data is clearly identified.
6. `WHY` references the prior selected intervention correctly in multi-turn tests.
7. Admin can create and publish a new metric binding, goal, intervention template and response template without service code change.
8. One active ontology revision loads and validates successfully.
9. At least one forecast profile passes backtesting and produces a monitored output; other nodes can use deterministic baselines.
10. One 30-minute one-to-one meeting is captured, transcribed, diarized and reviewed.
11. At least one approved commitment is subsequently VERIFIED, BREACHED or UNVERIFIABLE through a registered rule.
12. Device/service/config/decision/meeting audit records are queryable.
13. An on-prem Docker deployment and an Azure deployment mapping are documented and reproducible.
