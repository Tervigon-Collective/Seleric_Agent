# Component Contracts

This document defines what each V1 component exists to do, why it is explicitly needed, how it works, its inputs/outputs, configuration, failure behavior and extension points.

# 1. Component summary

| Component | Explicitly needed? | Primary outcome |
|---|---:|---|
| Edge hardware/audio broker | Yes | Reliable far-field capture, playback, controls and shared audio access |
| OpenVoiceOS edge runtime | Yes | Reuses wake/VAD/STT/TTS/skills/audio rather than custom voice framework |
| Seleric Bridge Skill | Yes | Thin connection between voice transcript and approved backend commands |
| Device Agent | Yes | Enrollment, signed config, heartbeat, updates and revocation |
| STT/TTS provider layer | Yes | Replaceable local/on-prem/cloud speech services |
| Voice Orchestrator | Yes | Deterministic intent/session/response routing |
| Business State Service | Yes | Turns certified metrics into reproducible current state |
| Seleric MCP adapter | Yes | Enforces certified metric semantics and provenance |
| Feature engine | Yes | Rolling state, velocity, acceleration and volatility |
| Forecast/anomaly engine | Yes, lightweight | Expected range, risk horizon and statistical deviation |
| Ontology runtime | Yes | Understands business parts, dependencies, owners and goals |
| Health evaluator | Yes | Consistent node status with uncertainty and freshness |
| Insight Decision Service | Yes | Candidate generation, root-driver consolidation and top-three ranking |
| NLG renderer | Yes | Reproducible spoken output without generative reasoning |
| Proactive notification channel | Yes | Enables actionable alerts without manual query |
| Meeting capture/transcription | Yes | Converts 1:1 audio into reviewable evidence |
| Semantic extractor | Yes | Decisions, commitments, owners, deadlines and outcomes |
| Review workflow | Yes | Prevents incorrect operational records |
| Commitment verifier | Yes | Closes the promise-to-evidence loop |
| Control Plane | Yes | Makes platform configuration dynamic, versioned and safe |
| Appsmith Admin UI | Yes | Rapid operator interface without custom frontend build |
| PostgreSQL task queue | Yes | Durable scheduled/retryable jobs without extra broker |
| PostgreSQL, ClickHouse, object store | Yes | Appropriate control, analytical and artifact persistence |
| Identity/secrets | Yes | Device/admin/service authorization and secret isolation |
| Observability/audit | Yes | Reliability, lineage, debugging and governance |

# 2. Edge hardware and audio broker

## Purpose

Own physical audio capture/playback and provide one stable audio source to listening and meeting components.

## Why needed

A microphone array, echo cancellation, speaker placement and shared capture are physical/runtime problems. Without an audio broker, the assistant listener and meeting recorder may contend for the ALSA device.

## Working

- ReSpeaker XVF3800 USB exposes processed microphone audio.
- ALSA `dsnoop` or PipeWire provides shared capture.
- OVOS listener reads the shared source.
- Meeting recorder reads the same source only in meeting mode.
- Speaker output is routed through OVOS audio/TTS.
- GPIO/USB controls drive mute, stop and LED states.

## Inputs

- PCM audio frames
- physical button events
- playback audio
- signed device configuration

## Outputs

- audio stream to listener/recorder
- speaker playback
- hardware state events
- audio diagnostics

## Configurable fields

```text
capture_device
playback_device
sample_rate
channels
input_gain
output_volume
aec_profile
meeting_segment_seconds
local_spool_limit_mb
led_mapping
button_mapping
```

## Failure behavior

- Missing microphone: error LED; no voice capture; meeting start rejected.
- Speaker failure: retain text response and raise device health fault.
- Local storage pressure: stop new meeting safely before data corruption.
- Audio contention: health check fails deployment readiness.

## Extension

A future custom Orb implements the same `AudioDevice` and `HardwareIndicator` interfaces.

# 3. OpenVoiceOS edge runtime

## Purpose

Provide the complete edge voice shell: wake word, VAD, STT plugin, skills, TTS and playback.

## Why needed

Building the listener state machine, plugin manager, audio service, skills and lifecycle from scratch adds substantial risk without differentiating Seleric.

## Working

```text
microphone
-> Dinkum Listener
-> openWakeWord plugin
-> Silero VAD
-> STT plugin/provider
-> Seleric Bridge Skill
-> TTS plugin/provider
-> Audio Service
```

## Inputs

- shared microphone frames
- OVOS configuration
- installed plugin set
- Seleric Bridge responses

## Outputs

- recognized utterance
- wake/listen/speak state events
- rendered speech
- local edge telemetry

## Configuration

OVOS configuration is generated from an approved `VoiceProfile`. Local overrides are restricted to hardware calibration values.

## Security

The OVOS message bus remains at `127.0.0.1`. It is not used as a remote API because it has no built-in authentication.

## Failure behavior

Systemd restarts failed processes. A watchdog verifies listener and audio services. The Device Agent reports degraded plugin state.

# 4. Seleric Bridge Skill

## Purpose

Forward recognized utterances to Voice Orchestrator and speak the returned `speech_text`.

## Why needed

It is the only custom skill needed in V1 and keeps all business logic off the device.

## Responsibilities

- create/maintain `dialogue_session_id`
- attach device/user/locale metadata
- send transcript and confidence
- apply request timeout and idempotency key
- map backend status to OVOS state
- speak typed response
- handle `start_meeting`/`stop_meeting` edge commands
- acknowledge long-running operations

## Input

```json
{
  "utterance": "what are the three things I need to do today",
  "stt_confidence": 0.94,
  "device_id": "dev_office_01",
  "dialogue_session_id": "dlg_...",
  "locale": "en-IN",
  "captured_at": "2026-09-15T10:00:00+05:30"
}
```

## Output consumed

```json
{
  "turn_id": "turn_...",
  "intent_id": "FOUNDER_PRIORITIES",
  "speech_text": "First ... Second ... Third ...",
  "payload_type": "founder_brief",
  "payload_id": "brief_...",
  "freshness": "CURRENT",
  "follow_up_context": {
    "brief_id": "brief_...",
    "last_rank": 3
  }
}
```

## Failure behavior

- Timeout: “I could not reach Seleric’s intelligence service.”
- Stale response: speak only if within policy and disclose age.
- Invalid schema: do not speak the body; report integration error.

# 5. Device Agent

## Purpose

Manage the physical node as an enrolled platform device.

## Responsibilities

- bootstrap enrollment
- authenticate with device certificate/token
- fetch signed active device/voice config
- heartbeat and health
- update channel and software version
- revocation and remote disable
- clock/timezone validation
- local log upload under policy

## APIs

```text
POST /v1/devices/enroll
POST /v1/devices/{id}/heartbeat
GET  /v1/devices/{id}/configuration
POST /v1/devices/{id}/diagnostics
```

## Inputs

Hardware identity, enrollment code, current software/config version and diagnostics.

## Outputs

Device token/certificate metadata, signed configuration, update instruction and policy.

## Configuration

`device_profile`, `voice_profile`, `meeting_policy`, `notification_policy`, `update_channel`.

# 6. Speech provider layer

## Purpose

Make speech recognition and synthesis replaceable without changing voice/business code.

## Interfaces

```python
class SpeechToTextProvider(Protocol):
    async def transcribe(self, audio: AudioInput, context: STTContext) -> Transcript: ...
    async def health(self) -> ProviderHealth: ...

class TextToSpeechProvider(Protocol):
    async def synthesize(self, request: SynthesisRequest) -> AudioOutput: ...
    async def health(self) -> ProviderHealth: ...
```

## Registered V1 providers

- `ovos_faster_whisper_server`
- `ovos_whispercpp_edge_fallback`
- `azure_speech_stt`
- optional `deepgram_stt`
- `ovos_phoonnx_tts_server`
- `azure_speech_tts`
- `ovos_espeak_emergency_tts`

## Inputs

Audio/text, locale, vocabulary/keyterms, timeouts, voice and quality profile.

## Outputs

Transcript with confidence/segments or audio artifact/stream metadata.

## Configuration

Provider chain, endpoint, model, locale, timeout, retry, vocabulary, voice, speed and fallback conditions.

## Failure behavior

Provider failover follows a bounded ordered chain. Provider errors never cause the business service to retry unboundedly.

# 7. Voice Orchestrator Service

## Purpose

Provide deterministic dialogue orchestration and the single voice-facing business API.

## Why needed

The edge should not know service topology or business logic. This service maps intents to command handlers and manages the narrow reference context required for follow-ups.

## Internal modules

```text
authentication
intent_router
slot_resolver
dialogue_context
command_registry
response_validator
notification_stream
audit_adapter
```

## Primary API

### `POST /v1/voice/turns`

Input:

```json
{
  "device_id": "dev_office_01",
  "user_id": "person_founder",
  "dialogue_session_id": "dlg_123",
  "utterance": "why",
  "locale": "en-IN",
  "stt_confidence": 0.96,
  "client_turn_id": "edge_uuid"
}
```

Output:

```json
{
  "turn_id": "turn_456",
  "intent": {
    "id": "EXPLAIN_PRIORITY",
    "pattern_id": "why_contextual_01",
    "confidence": 1.0,
    "slots": {"rank": 1}
  },
  "speech_text": "The first priority was selected because ...",
  "payload": {"type": "priority_explanation", "id": "exp_789"},
  "evidence_ids": ["evidence_1", "evidence_2"],
  "freshness": "CURRENT",
  "response_template_version": "exec_why_v3"
}
```

## Command handlers

```text
GET_COMPANY_HEALTH
GET_FOUNDER_PRIORITIES
EXPLAIN_PRIORITY
GET_RISKS
GET_OPPORTUNITIES
START_MEETING
STOP_MEETING
```

Handlers are allowlisted code objects. Configuration binds intents to existing handler IDs; it cannot create new executable handlers.

## Storage

Short-lived `dialogue_session`, `dialogue_turn`, `context_reference` and audit records in PostgreSQL.

## Failure behavior

- No intent match: deterministic unsupported response.
- Missing context for “Why?”: ask which priority.
- Downstream unavailable: latest valid response within TTL or explicit unavailable response.
- Duplicate client turn: return prior idempotent result.

# 8. Intent Registry

## Purpose

Allow sentence patterns, slots and context to change without edge/backend redeployment.

## Input object

```yaml
intent_id: FOUNDER_PRIORITIES
handler_id: GET_FOUNDER_PRIORITIES
locale: en-IN
sentences:
  - "what are the three things I need to do today"
  - "what should I focus on today"
  - "give me my top {limit} priorities"
slots:
  limit:
    type: integer
    default: 3
    min: 1
    max: 3
context_requirements: []
priority: 100
```

## Output

`IntentMatch(intent_id, handler_id, slots, pattern_id, score, context)`.

## Validation

- duplicate/ambiguous patterns
- handler exists
- slot schema valid
- test fixtures pass
- negative utterances remain unmatched

# 9. Seleric MCP Adapter

## Purpose

Provide a typed anti-corruption layer between Business State Service and the existing Seleric MCP.

## Why needed

It prevents internal services from depending on raw MCP payload shapes and enforces metric contract/freshness/provenance requirements.

## Interface

```python
class MetricProvider(Protocol):
    async def get_metric_definition(self, metric_id: str) -> MetricDefinition: ...
    async def validate_binding(self, binding: MetricBinding) -> ValidationResult: ...
    async def query(self, request: MetricQuery) -> ObservationSet: ...
    async def explain(self, query_id: str) -> MetricExplanation: ...
```

## Inputs

Metric IDs, time range, grain, supported dimensions, filters, comparison and timezone.

## Outputs

Typed observations plus:

```text
query_id
catalogue_version
metric_definitions
time_range
filters
timezone
freshness
warnings
source_view
generated_at
```

## Failure behavior

Unsupported metric/dimension or catalogue mismatch becomes a binding/config error, not a fallback raw SQL query.

# 10. Business State Service

## Purpose

Transform certified observations into immutable, reproducible node state.

## Service API

```text
POST /v1/state-refresh-jobs
GET  /v1/state-snapshots/latest
GET  /v1/state-snapshots/{snapshot_id}
GET  /v1/nodes/{node_id}/state
POST /v1/model-backtests
GET  /v1/model-versions/{id}
```

## Workflow

1. Resolve active config revision.
2. Validate metric bindings against MCP catalogue.
3. Query observations in bounded batches.
4. Persist observation snapshot and provenance.
5. Calculate configured features.
6. Execute approved forecast strategy if eligible.
7. Execute anomaly strategy.
8. Evaluate goal/health policy.
9. Append state snapshot/history.
10. Enqueue brief refresh.

## Inputs

- active nodes/edges/bindings/goals
- feature/model/anomaly/health profiles
- certified observations
- prior state/model versions

## Outputs

- observation snapshot
- node feature snapshot
- forecast output
- anomaly event
- node health snapshot
- state refresh summary

## Failure behavior

Errors are isolated per binding/node. A state batch can finish `PARTIAL_SUCCESS`; affected nodes become `UNKNOWN`/`DATA_ISSUE` and cannot create normal business interventions.

# 11. Feature Engine

## Purpose

Compute reusable deterministic state features.

## Registered feature strategies

```text
current_value
period_delta
comparison_delta_pct
rolling_mean
rolling_median
rolling_std
robust_mad
EWMA
velocity
acceleration
volatility
seasonal_position
data_completeness
freshness_age
```

## Feature definition

```json
{
  "feature_id": "velocity_24h",
  "strategy_id": "finite_difference_velocity",
  "source_metric_id": "session_conversion_rate",
  "window": "24h",
  "minimum_points": 12,
  "normalization": "relative",
  "null_policy": "UNAVAILABLE",
  "version": 2
}
```

## Output

`FeatureValue(value, unit, window, as_of, completeness, strategy_version, source_observation_ids)`.

## Extension

New strategies implement `FeatureStrategy` and are registered in code; admins configure their schema-approved parameters.

# 12. Forecast Engine

## Purpose

Estimate expected values and uncertainty only where history supports it.

## Interface

```python
class ForecastStrategy(Protocol):
    def backtest(self, series: TimeSeries, profile: ForecastProfile) -> BacktestResult: ...
    def fit_predict(self, series: TimeSeries, profile: ForecastProfile) -> ForecastOutput: ...
```

## V1 strategies

- seasonal naive
- historic average by time bucket
- EWMA
- StatsForecast AutoETS
- StatsForecast MSTL + base model
- StatsForecast Theta/AutoARIMA where accepted
- Optional Merlion adapter for selected forecast/anomaly/change-point profiles
- optional XGBoost plugin

## Inputs

Historical series, frequency, horizon, exogenous inputs, candidate list and validation gates.

## Outputs

Point, lower/upper interval, horizon, residuals, model/version, artifact checksum, backtest and calibration status.

## Promotion rules

A model must improve over configured baseline and pass interval coverage/maximum error gates. Otherwise the baseline remains champion.

# 13. Anomaly Engine

## Purpose

Detect statistically unusual observations with explicit method and threshold.

## V1 strategies

1. prediction interval breach
2. robust residual/MAD score
3. seasonal baseline deviation
4. optional PyOD Isolation Forest for validated multivariate profile
5. data anomaly rules: missingness, duplicate spike, impossible bound

## Input

Actual observation, expected range/baseline, residual history and anomaly profile.

## Output

```json
{
  "anomaly_id": "an_123",
  "node_id": "checkout_conversion",
  "metric_id": "session_checkout_to_purchase_rate",
  "direction": "NEGATIVE",
  "actual": 0.021,
  "expected": 0.035,
  "lower": 0.029,
  "upper": 0.041,
  "severity": 3.8,
  "method": "prediction_interval_breach",
  "confidence": 0.91,
  "status": "ACTIVE"
}
```

# 14. Ontology Runtime

## Purpose

Load and traverse the active business graph and resolve relationships between nodes, metrics, goals, owners, interventions and commitments.

## Inputs

Published graph revision from Control Plane.

## Outputs

- validated NetworkX DiGraph
- ancestors/descendants
- dependency paths
- topological order
- subgraphs by domain
- affected goals/owners
- edge semantics/confidence

## Interface

```python
class OntologyGraph(Protocol):
    def ancestors(self, node_id: str, edge_types: set[str]) -> list[Path]: ...
    def descendants(self, node_id: str, edge_types: set[str]) -> list[Path]: ...
    def validate(self) -> GraphValidation: ...
    def affected_goals(self, node_id: str) -> list[GoalRef]: ...
```

## Failure behavior

Invalid published revision is impossible by control-plane policy. If runtime loading fails, Insight Service continues with prior valid graph revision and alerts engineering.

# 15. Health Evaluator

## Purpose

Translate node state and goals into a consistent health object without hiding missing/stale information.

## Interface

```python
class HealthPolicy(Protocol):
    def evaluate(self, node: BusinessNode, goal: Goal, state: NodeState,
                 dependencies: list[NodeHealth]) -> NodeHealth: ...
```

## Default factors

- direct target attainment
- directionality of metric
- warning/critical thresholds
- data freshness/confidence
- anomaly severity
- optional dependency contribution

## Output statuses

```text
GREEN
YELLOW
RED
UNKNOWN
UNSCORED
DATA_ISSUE
```

## Important rule

Health is not one mandatory global formula. Different metric types can use target range, minimum, maximum, ratio or milestone policies. The source health formula can be implemented as one strategy, not hard-coded for all nodes.

# 16. Insight Decision Service

## Purpose

Produce auditable founder-level insights from state, goals, ontology, commitments and intervention policy.

## APIs

```text
GET  /v1/company-health/latest
POST /v1/founder-briefs
GET  /v1/founder-briefs/latest
GET  /v1/founder-briefs/{id}
GET  /v1/founder-briefs/{id}/items/{rank}/explanation
GET  /v1/risks/latest
GET  /v1/opportunities/latest
GET  /v1/decision-traces/{id}
```

## Internal pipeline

```text
state selection
-> suspected root-driver attribution
-> intervention template matching
-> precondition evaluation
-> founder eligibility
-> materiality filters
-> root-key consolidation
-> ranking
-> top 0..3 selection
-> NLG rendering
-> persistence and notification policy
```

## Inputs

Latest valid state snapshot, graph revision, goals, owners, intervention templates, ranking policy, active commitments and response templates.

## Outputs

CompanyHealthSummary, InterventionCandidate, FounderBrief, RiskBrief, OpportunityBrief, Explanation and DecisionTrace.

# 17. Suspected Root-Driver Resolver

## Purpose

Avoid reporting downstream symptoms as separate priorities.

## V1 score inputs

- ancestor relationship and path length
- edge semantics and configured influence weight
- anomaly start time/temporal precedence
- direction consistency
- observed contribution to target change
- independent anomaly versus inherited deviation
- data/model confidence

## Output

```json
{
  "target_node_id": "net_revenue",
  "suspected_driver_node_id": "checkout_conversion",
  "method": "dependency_temporal_attribution_v1",
  "score": 0.82,
  "is_causally_verified": false,
  "supporting_paths": ["checkout_conversion -> net_revenue"],
  "alternatives": []
}
```

## Extension

`DoWhyRootCauseStrategy` implements the same interface but can run only when the graph/dataset has causal approval.

# 18. Intervention Candidate Generator

## Purpose

Translate a state/driver into an allowed action candidate.

## Template input

```json
{
  "template_id": "checkout_conversion_degraded",
  "applies_to": {
    "node_type": "PROCESS",
    "state": "RED",
    "anomaly_direction": "NEGATIVE"
  },
  "action_template": "Ask {owner_name} to investigate {node_name} before {deadline_hint}.",
  "default_owner_role": "Engineering Lead",
  "founder_required_policy": "cross_function_or_exposure",
  "preconditions": ["data_current", "financial_exposure_available"],
  "verification_rule_id": "metric_recovery_v1"
}
```

## Output

A candidate with evidence, owner, founder leverage, preconditions, expected impact, eligibility and root key.

# 19. Eligibility and Ranking Engine

## Purpose

Select no more than three interventions through explicit policy.

## Eligibility specification

```python
class CandidateSpecification(Protocol):
    def evaluate(self, candidate: InterventionCandidate, context: DecisionContext) -> RuleResult: ...
```

Examples:

- `DataIsCurrent`
- `EvidenceConfidenceAtLeast`
- `GoalIsActive`
- `FinancialExposureAtLeast`
- `NotAlreadyResolved`
- `NotDuplicateRootKey`
- `FounderLeverageAtLeast`
- `OperationalPreconditionsMet`

## Ranking interface

```python
class RankingPolicy(Protocol):
    def rank(self, candidates: list[InterventionCandidate],
             policy: RankingProfile) -> list[RankedCandidate]: ...
```

## Default score

```text
severity_weighted
x financial_exposure_weighted
x urgency_weighted
x evidence_confidence
x data_confidence
x founder_leverage
```

Factors are normalized, capped and logged. A weighted-additive strategy is available when zero values should not eliminate a candidate. TOPSIS can be added as another registered strategy.

# 20. NLG Renderer

## Purpose

Generate natural, concise speech without generative business reasoning.

## Interface

```python
class ResponseRenderer(Protocol):
    def render(self, template_id: str, payload: BaseModel,
               locale: str, mode: str) -> RenderedResponse: ...
```

## Inputs

Typed health/brief/explanation/risk/opportunity object and versioned Jinja2 template.

## Outputs

`speech_text`, optional display text, estimated speech duration, template version and fields used.

## Validation

- only allowlisted payload fields
- no missing required fields
- max word/time limit
- number/currency formatting
- freshness and uncertainty clauses when required
- snapshot tests for expected speech

# 21. Proactive Notification Channel

## Purpose

Deliver material insights without waiting for a query.

## Working

Insight Service persists a `Notification` after alert-policy evaluation. Voice Orchestrator exposes an authenticated device stream or polling endpoint. Edge speaks only when presence/mute/meeting/quiet-hour policy permits.

## Inputs

Brief item/event, alert policy, device/user presence and delivery state.

## Outputs

Pending/delivered/acknowledged/suppressed/expired notification.

## APIs

```text
GET  /v1/devices/{id}/notifications/stream
POST /v1/notifications/{id}/acknowledge
```

# 22. Meeting Capture Adapter

## Purpose

Normalize physical and online meeting sources.

## Interface

```python
class MeetingCaptureProvider(Protocol):
    async def start(self, request: MeetingStartRequest) -> CaptureSession: ...
    async def finalize(self, capture_id: str) -> CaptureArtifact: ...
```

## Providers

- `physical_edge_recorder`
- optional future `vexa_online_meeting` (not deployed for physical-room V1)
- future calendar/platform adapters

## Normalized output

Meeting metadata, participant hints, audio/artifact URIs, source platform and checksums.

# 23. Transcription and Diarization Adapter

## Purpose

Convert finalized audio into normalized speaker-attributed transcript evidence.

## Interface

```python
class MeetingTranscriber(Protocol):
    async def transcribe(self, artifact: CaptureArtifact,
                         profile: TranscriptionProfile) -> NormalizedTranscript: ...
```

## V1 physical implementation

WhisperX/Faster Whisper + pyannote, configured with expected speaker count 2 for one-to-one tests.

## Inputs

Audio parts, language, vocabulary, speaker count hints, participant hints and model profile.

## Outputs

Segments/words, timestamps, speaker labels, confidence, model/version and source references.

## Failure behavior

Low quality/overlap does not produce silent confidence. It creates review warnings and may trigger managed transcription fallback.

# 24. Participant Resolver

## Purpose

Map diarization labels to actual people without assuming identity.

## Resolution order

1. preselected founder/participant
2. calendar/meeting expected participants
3. spoken introductions matched to person aliases
4. optional voiceprint provider later
5. human review

## Output

`SpeakerResolution(speaker_label, person_id|null, method, confidence, evidence)`.

# 25. Semantic Extraction Engine

## Purpose

Create evidence-grounded meeting objects without LLM reasoning.

## Pipeline

```text
normalized transcript
-> sentence/turn segmentation
-> controlled entity resolution
-> commitment/decision phrase rules
-> dependency patterns
-> deadline parsing
-> target metric/node resolution
-> confidence aggregation
-> conflict checks
-> review objects
```

## Interface

```python
class MeetingExtractor(Protocol):
    def extract(self, transcript: NormalizedTranscript,
                context: MeetingContext,
                rules: ExtractionRuleSet) -> ExtractionResult: ...
```

## Input rule examples

- owner aliases/roles
- commitment verbs: `will`, `must`, `take`, `deliver`, `send`, `fix`, `approve`
- decision phrases: `we decided`, `approved`, `agreed`, `finalize`
- deadline phrases
- outcome phrases: `so that`, `expected`, `target`, `by improving`
- metric and node aliases from ontology

## Output

Typed decisions, commitment drafts, open questions and field-level evidence/confidence.

# 26. Review Workflow

## Purpose

Make human validation an explicit state transition rather than an informal correction.

## Inputs

Transcript viewer, source audio link, extracted fields, evidence spans, warnings and candidate ontology matches.

## Outputs

Approved/corrected/rejected objects, reviewer identity and audit events.

## Admin actions

- assign participant
- correct action/owner/deadline/outcome
- bind node/metric
- select verification rule
- approve/reject
- add extraction pattern candidate

# 27. Commitment Store and Verifier

## Purpose

Track approved commitments and determine whether evidence shows completion/outcome.

## Verification adapter interface

```python
class VerificationAdapter(Protocol):
    async def verify(self, commitment: Commitment,
                     rule: VerificationRule,
                     as_of: datetime) -> VerificationResult: ...
```

## V1 adapters

- `certified_metric_condition`
- `task_status_condition`
- `api_event_condition`
- `document_evidence_condition`
- `human_confirmation`

## Input

Approved commitment, deadline/grace period, verification rule and target bindings.

## Output

VERIFIED/BREACHED/UNVERIFIABLE/PENDING_SYSTEM with evaluated expression, evidence and rule version.

## Critical rule

System/integration failure is not a business breach. A breach requires valid evidence showing the condition failed after the applicable grace period.

# 28. Control Plane Service

## Purpose

Provide the safe dynamic platform configuration required by V1.

## APIs

```text
/v1/config/device-profiles
/v1/config/voice-profiles
/v1/config/intents
/v1/config/nodes
/v1/config/edges
/v1/config/metric-bindings
/v1/config/goals
/v1/config/features
/v1/config/forecast-profiles
/v1/config/anomaly-profiles
/v1/config/health-policies
/v1/config/intervention-templates
/v1/config/ranking-policies
/v1/config/response-templates
/v1/config/extraction-rules
/v1/config/verification-rules
/v1/config/alert-policies
/v1/config/revisions/{id}/validate
/v1/config/revisions/{id}/simulate
/v1/config/revisions/{id}/approve
/v1/config/revisions/{id}/publish
/v1/config/revisions/{id}/rollback
```

## Inputs

Typed draft objects, actor/role, base revision and change reason.

## Outputs

Validation issues, simulation diff, approval state, immutable published revision and audit event.

## Failure behavior

No partial publication. Publication is transactional and updates one `active_revision` pointer only after all validations pass.

# 29. Appsmith Admin UI

## Purpose

Deliver configuration/admin capability quickly.

## Pages

- Platform Overview
- Device Fleet
- Voice Profiles and Intents
- Ontology Graph
- Metric Bindings
- Goals and Owners
- Feature/Forecast/Anomaly Profiles
- Node Health Explorer
- Intervention Templates and Ranking
- Brief Simulator and Decision Inspector
- Response Templates
- Meetings and Review Queue
- Commitments and Verification
- Alert Policies
- Config Revisions and Rollback
- Audit and Service Health

## Important boundary

Appsmith connects to Control Plane and runtime read APIs. It does not receive direct write access to PostgreSQL.

# 30. PostgreSQL Task Queue

## Purpose

Handle asynchronous/periodic tasks with no Redis/RabbitMQ/Temporal dependency.

## Interface

```python
class TaskQueue(Protocol):
    async def enqueue(self, task_type: str, payload: BaseModel,
                      idempotency_key: str, execute_at: datetime | None = None) -> TaskRef: ...
```

## Requirements

- at-least-once execution
- retries/backoff
- queue locks
- periodic tasks
- dead-letter inspection
- task correlation and metrics
- idempotency in handler/domain write

## Failure behavior

Exhausted jobs remain visible in failed/dead state and alert engineering. They are never silently discarded.

# 31. Persistence components

## PostgreSQL

Stores configuration, revisions, devices, dialogue references, job state, brief metadata/traces, meetings, commitments, verification and audit.

## ClickHouse

Stores high-volume immutable observation/state/forecast/anomaly history and supports analytical drilldown.

## Object storage

Stores audio parts, normalized transcript artifacts, model artifacts, evaluation corpora and exported reports. Every object has checksum, classification, retention and owner metadata.

# 32. Identity and secrets

## Purpose

Authenticate humans, devices and services and keep credentials outside application configuration.

## Inputs

OIDC identity, device certificate/token, service identity and authorization context.

## Outputs

Claims/scopes used by API policy.

## Roles

```text
platform_admin
config_editor
config_approver
executive
analyst
meeting_reviewer
meeting_admin
auditor
service_identity
device_identity
```

## Secrets

Azure Key Vault or on-prem SOPS/Vault adapter. Sensitive provider secrets are referenced by secret ID, never stored in normal config JSON.

# 33. Observability and Audit

## Purpose

Make the platform operable and every recommendation reproducible.

## Technical telemetry

- service latency/error/availability
- task queue depth/age/retries
- database/object-store health
- STT/TTS provider latency/error
- device uptime/audio diagnostics

## Data/ML telemetry

- metric freshness and binding failures
- feature completeness
- model backtest/calibration/drift
- anomaly counts and review precision
- node health coverage

## Product telemetry

- intent match/no-match/confusion
- response duration
- user interruptions
- brief acceptance/acknowledgement
- meeting extraction corrections
- verification completion and backlog

## Audit

Append-only events link actor/service, action, object, before/after references, config revision, trace ID and timestamp.
