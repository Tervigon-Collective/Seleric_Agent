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
| Insight Decision Service | Yes | Hosts the Seleric Swarm Layer; candidate generation, root-driver consolidation and top-three prioritization via agent debate |
| Seleric Blackboard | Yes | Persistent case memory and the audit trail for non-deterministic reasoning |
| Swarm Coordinator | Yes | Leaderless handoff routing, task market, coalition management |
| Agent Registry | Yes | Capability/tool/cost/reputation advertising for recruitment (internal-only in V1) |
| Seven initial agents (Observer, Anomaly, Diagnostic, Prediction, Strategy, Experiment, Skeptic) | Yes | Bounded reasoning roles; Skeptic prevents premature convergence |
| Seleric Governor | Yes | Non-recruitable safety boundary: tool/spend/PII/write/spawn/iteration/approval control |
| NLG renderer | Yes | Reproducible spoken output rendering the swarm's finished typed conclusion; still not itself generative |
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

Host the Seleric Swarm Layer and produce auditable founder-level insights from state, goals, ontology, commitments and agent-swarm reasoning. **Changed 2026-08-31**: sections 17-19 below (formerly the deterministic Root-Driver Resolver, Candidate Generator, and Eligibility/Ranking Engine) are replaced by §34-40 (Blackboard, Coordinator, Registry, the seven agents, Governor enforcement point). This section's external API contract is unchanged; what changed is what computes the response.

## APIs — unchanged

```text
GET  /v1/company-health/latest
GET  /v1/founder-briefs/latest
GET  /v1/founder-briefs/{id}
GET  /v1/founder-briefs/{id}/items/{rank}/explanation
GET  /v1/risks/latest
GET  /v1/opportunities/latest
GET  /v1/decision-traces/{id}       # now resolves to a Blackboard case debate trace, not a formula trace
```

`POST /v1/founder-briefs` is removed as a synchronous trigger — Voice Orchestrator never asks Insight Decision Service to compute a brief on demand; it always reads the latest already-published one (doc 03 §5, §9). Cases are opened by the swarm's own triggers (doc 07 §3), not by an inbound API call.

## Internal pipeline — replaced

```text
state/evidence ingestion onto Blackboard
-> Observer notices candidate problem, opens/updates case
-> Coordinator posts task, agents bid, bids selected (or coalition opened)
-> agent debate: hypothesize, challenge, recruit, hand off (Governor-checked throughout)
-> Skeptic pass (mandatory before any action proposal converges)
-> Coordinator marks case CONVERGED with confidence + evidence refs
-> Governor clears (or denies) any proposed action
-> NLG rendering of the finished typed conclusion
-> persistence and notification policy
```

## Inputs

Latest valid state snapshot (Business State Service), graph revision, goals, owners, active commitments, Governor policy bundle, agent registry, and — new — prior closed cases retrieved as precedent.

## Outputs

CompanyHealthSummary, FounderBrief, RiskBrief, OpportunityBrief, Explanation — all unchanged DTOs, now additionally carrying a `confidence` field and a `case_id`/`debate_trace_id` instead of a formula-only `decision_trace_id`.

## Failure behavior

If the swarm has no converged case within a brief's freshness policy, Insight Decision Service returns the latest still-valid brief with disclosed age, exactly as the deterministic pipeline did when computation failed — Voice Orchestrator's contract at this boundary is unaffected by the reasoning-model change (doc 03 §15).

# 34. Seleric Blackboard

## Purpose

Persistent, structured operational memory shared by every agent working a case, and — because determinism is gone — the platform's accountability mechanism. Every problem gets a case record: observation, evidence, urgency, hypotheses, active agents, open tasks, proposed actions, outcome, confidence. Every conclusion must be traceable to the specific agent messages that produced it.

## Why needed

Without a shared, persistent workspace, agent-to-agent handoff would require re-explaining context on every hop, and there would be no way to reconstruct "why did the swarm conclude this" after the fact — which is the entire replacement for the deterministic decision trace.

## Working

Backed by the existing PostgreSQL instance (`decision.*` schema, doc 14 §10a) — no new datastore. A case is opened by the Observer agent (or a scheduled/event trigger), accumulates evidence and agent messages as investigation proceeds, and is marked `CONVERGED`, `INCONCLUSIVE`, or `ABANDONED` by the Coordinator. Every row is append-only; corrections are new rows referencing the corrected one, never in-place edits — the same audit discipline the platform already applies to configuration and commitments (doc 09 §9).

## Interface

```python
class Blackboard(Protocol):
    async def open_case(self, trigger: CaseTrigger) -> SwarmCase: ...
    async def get_case(self, case_id: str) -> SwarmCase: ...
    async def post_message(self, message: AgentMessage) -> None: ...
    async def post_hypothesis(self, hypothesis: Hypothesis) -> None: ...
    async def post_task(self, task: SwarmTask) -> None: ...
    async def submit_bid(self, bid: SwarmBid) -> None: ...
    async def propose_action(self, action: ProposedAction) -> None: ...
    async def close_case(self, case_id: str, outcome: CaseOutcome) -> None: ...
    async def find_similar_cases(self, observation: str, limit: int) -> list[SwarmCase]: ...
```

## Inputs

Business State evidence, agent messages, hypotheses, bids, action proposals, Governor decisions, case-outcome confirmations.

## Outputs

A queryable case record and its complete message history; a `find_similar_cases` result set (`pgvector` similarity search, doc 01 §3a.6) used for collective memory.

## Failure behavior

If the Blackboard write path is unavailable, no agent turn may proceed (an agent with nowhere to record its reasoning cannot act) — this fails closed the same way a Governor policy fetch failure does (doc 03 §15).

# 35. Swarm Coordinator

## Purpose

Route control between agents based on which agent's domain the investigation currently touches — no permanent leader.

## Why needed

Without a coordinator, recruitment and handoff would either need a fixed pipeline order (which contradicts the founder's brief) or would have no selection mechanism between competing bids.

## Working

Implemented as a LangGraph graph whose nodes are agent turns; each agent node's return value is a `Command` that either hands off directly to a named agent or returns control to the Coordinator node when no agent claims the next step. The Coordinator's own responsibilities are narrower than "leader": it posts tasks to the task market, selects bids, opens/closes coalitions, and detects convergence or stagnation (doc 06 §9.2-9.3).

## Interface

```python
class SwarmCoordinator(Protocol):
    async def post_task(self, case_id: str, description: str) -> SwarmTask: ...
    async def select_bid(self, task_id: str) -> SwarmBid | None: ...
    async def open_coalition(self, case_id: str, agent_ids: list[str]) -> Coalition: ...
    async def detect_convergence(self, case_id: str) -> ConvergenceResult: ...
```

## Task market / bidding

Concrete selection rule (doc 06 §9.3): each bid carries `confidence`, `estimated_cost`, `expected_information_gain`. The Coordinator computes `expected_value = confidence * expected_information_gain / max(estimated_cost, epsilon)`, ranks bids descending, and ties break on `agent_reputation.calibration` for that problem class. The losing bidders' bids remain on the Blackboard for audit.

## Failure behavior

If no agent bids on a posted task within its timeout, the case is marked `INCONCLUSIVE` and surfaces as a data-gap risk rather than silently disappearing (the same "never silently drop" discipline as a failed state job, doc 03 §15).

# 36. Agent Registry

## Purpose

Let agents (and the Coordinator) discover and recruit each other by capability, tool access, cost, and historical reliability.

## Why needed

Recruitment ("Diagnostic agent recruits Prediction agent because it needs a forecast") requires a place to look up who can do what — without it, recruitment would be hardcoded pipeline order, which is exactly what the swarm model replaces.

## Working

Rows in `decision.agent_registry` (doc 14 §10a), one per agent role, each declaring capability tags, available tool ports, a cost profile, and a link to its `agent_reputation` rows. **Internal-only in V1**: `exposure_scope = INTERNAL` on every row; there is no external-facing directory endpoint. The schema is intentionally shaped so a future A2A-facing directory can be added without a rewrite (doc 01 §3a.10) — this is the only concession to the future, and it costs nothing beyond one enum value today.

## Interface

```python
class AgentRegistry(Protocol):
    async def list_capable(self, capability: str) -> list[AgentRegistryEntry]: ...
    async def get_reputation(self, agent_id: str, problem_class: str) -> AgentReputation: ...
    async def register(self, entry: AgentRegistryEntry) -> None: ...
```

## Failure behavior

A registry lookup failure blocks recruitment for that turn; the current agent completes its own reasoning and hands back to the Coordinator rather than guessing at a recruit.

# 37. The Seven Initial Agents

## Purpose

Each agent is a bounded reasoning role with its own capability declaration, tool access (Governor-granted), and prompt/behavior contract. V1 starts at seven, not fifty (doc 01 §6).

## Common contract

```python
class SwarmAgent(Protocol):
    agent_id: str
    role: AgentRole
    capabilities: tuple[str, ...]

    async def perceive(self, case: SwarmCase) -> Perception: ...
    async def propose(self, perception: Perception) -> Hypothesis | SwarmBid | None: ...
    async def act(self, hypothesis: Hypothesis, scope: GovernorScope) -> AgentTurnResult: ...
```

Every `act` call passes through the Governor enforcement point (§40) before any tool, spend, write, or external call executes.

## Role summary

| Agent | Primary responsibility | Typical evidence it consumes | Typical output |
|---|---|---|---|
| Observer | Notices candidate problems/opportunities from Business State evidence; opens cases | State/health snapshots, anomaly events | `CaseTrigger`, initial `OBSERVATION` message |
| Anomaly | Interprets Business State's forecast-residual/interval evidence into a severity judgment | Anomaly/forecast output (doc 05 §12-13, unchanged) | Severity-labeled hypothesis input |
| Diagnostic | Proposes root-driver hypotheses grounded in the ontology | Graph paths, temporal precedence, anomaly concurrence | `Hypothesis` with cited evidence |
| Prediction | Estimates forward impact/expected outcome of a hypothesis or proposed action | Forecast models, historical case precedent | Impact estimate with confidence |
| Strategy | Converts a supported hypothesis into a candidate action proposal | Ontology, owner/goal bindings, precedent cases | `ProposedAction` draft |
| Experiment | Where policy allows, proposes a bounded test (e.g., a small controlled check) rather than a full commitment | Precedent cases, Governor policy on experiment scope | Experiment proposal (still Governor-gated) |
| **Skeptic** | Challenges hypotheses and proposed actions before convergence; explicitly checks for premature convergence, unmet preconditions, and causal-language overreach | Every hypothesis/action in the case | `CHALLENGE` message; confidence adjustment; may force the case back to debate |

## The Skeptic is load-bearing, not optional review

Per the founder's brief, the Skeptic exists specifically to prevent the swarm from converging on a plausible-but-wrong story. It is not a post-hoc reviewer bolted onto a finished conclusion: SWARM-005 requires a recorded Skeptic pass on every case that reaches a proposed action, and RCA-004/DEC-012 make the Skeptic explicitly responsible for catching causal-language overreach and unmet-precondition gaps that debate momentum among the other six agents might otherwise paper over. Removing or downgrading the Skeptic to "optional" is a spec violation, not a simplification.

## Failure behavior

An agent that raises an unhandled error mid-turn does not corrupt the case: its partial state is discarded, the failure is recorded as a Blackboard message, and the Coordinator either retries the bid selection or marks the case `INCONCLUSIVE` — never silently drops the investigation.

# 38. Task Market / Bidding — see §35

Bidding mechanics are documented under Swarm Coordinator (§35) since the Coordinator is the bid-selecting party; agents are the bid-submitting party (§37).

# 39. Collective Memory and Reputation

## Collective memory

`find_similar_cases` (Blackboard, §34) runs a `pgvector` cosine-similarity query over `decision.swarm_case.resolution_embedding` for closed cases, seeded before agents start independent investigation (SWARM-010). This is presented to recruited agents as additional evidence, not as a binding answer — the Skeptic may still challenge a precedent-based hypothesis.

## Agent reputation

Concrete formula (doc 06 §9.6): per `(agent_id, problem_class)`, updated when a case's outcome is later confirmed (via commitment verification where the case produced an action, or human confirmation otherwise):

```text
accuracy      = correct_conclusions / total_confirmed_conclusions
calibration   = 1 - mean(|stated_confidence - outcome_correctness|)   # Brier-like
false_positive_rate = false_positives / total_flagged_problems
avg_cost      = mean(estimated_cost across confirmed cases)
avg_speed     = mean(wall-clock time from bid-selected to case-converged)
```

Read by the Coordinator's bid-selection tie-break (§35) and surfaced in the Admin swarm inspector (doc 08 §3.18).

# 40. Seleric Governor

## Purpose

Enforce every safety boundary above the swarm: tool permissions, financial limits, PII access, external communication, production writes, API spend, agent-spawning limits, max iteration counts, and human-approval gates. This is the actual safety boundary now that business logic isn't deterministic (doc 03 §7a).

## Why needed

Once conclusions are agent-derived rather than formula-derived, "the code is deterministic so it can't do anything unreviewed" is no longer true. The Governor is the component that keeps that promise true anyway.

## Working

A policy fetched from Control Plane (`control.governor_policy`, doc 08 §3.17) and an enforcement library that every agent's `act()` call passes through (§37) before a tool executes, a spend is committed, PII is accessed, an external message is sent, a production write occurs, or a new agent/coalition is spawned. It is not a swarm agent: it has no `agent_id`, cannot be recruited, and a denial cannot be appealed by the swarm itself — only by a human through the existing config-approval workflow (doc 03 §7a).

## Interface

```python
class Governor(Protocol):
    async def check(self, request: GovernorCheckRequest) -> GovernorDecision: ...
    async def record_decision(self, decision: GovernorDecision) -> None: ...

class GovernorCheckRequest(BaseModel):
    case_id: str
    agent_id: str
    action_type: Literal["TOOL_CALL", "SPEND", "PII_ACCESS", "EXTERNAL_COMM", "PRODUCTION_WRITE", "AGENT_SPAWN"]
    requested_scope: dict
    iteration_count: int
```

## Inputs

Current published Governor policy bundle, the requesting agent's identity and current case, the specific action requested.

## Outputs

`GRANT` or `DENY` with a policy version and reason code; every decision is written to the Blackboard (as a case message) and to `platform.audit_event` (doc 14 §12.4).

## Failure behavior

Fail closed: if policy cannot be fetched or is past its validity window, every check returns `DENY` for anything beyond read-only reasoning (GOV-006). This is the one place in the platform where "unavailable" and "denied" are deliberately the same outward behavior — a Governor that fails open would defeat its purpose.

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
