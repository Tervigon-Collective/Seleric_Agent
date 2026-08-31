# Workflows and Data Flows

## 1. Executive Query Workflow

```mermaid
sequenceDiagram
    actor Founder
    participant Edge as Edge Voice Node
    participant Speech as Local/Managed Speech Adapter
    participant Voice as Voice Orchestrator
    participant Insight as Insight Decision Service
    participant State as Business State Service
    participant Control as Control Plane Service
    participant MCP as Seleric MCP

    Founder->>Edge: Wake word and utterance
    Edge->>Speech: Audio after local activation
    Speech-->>Edge: Transcript + confidence
    Edge->>Voice: VoiceTurnCommand + device/user/trace context
    Voice->>Voice: Normalize, classify bounded intent, resolve references

    alt Transcript or intent below threshold
        Voice-->>Edge: Safe repeat/clarify response
    else Supported executive intent
        Voice->>Insight: Typed executive query
        Insight->>Control: Resolve published runtime bundle if cache miss
        Insight->>State: Read state/health snapshot pinned to bundle

        alt Current materialized state is valid
            State-->>Insight: State + freshness + evidence
        else Bounded synchronous refresh allowed
            State->>MCP: Certified metric query
            MCP-->>State: Values + query IDs + catalogue version + freshness
            State->>State: Derive state, health, persist idempotently
            State-->>Insight: Refreshed state + evidence
        else Refresh not allowed within response SLO
            State-->>Insight: STALE/UNAVAILABLE status + last valid timestamp
        end

        Insight->>Insight: Candidate/eligibility/dedupe/ranking or health assembly
        Insight-->>Voice: Typed response + decision trace ID
        Voice->>Voice: Render strict versioned response template
        Voice->>Speech: Text/SSML
        Speech-->>Edge: Audio stream/file
        Edge-->>Founder: Spoken response
    end
```

### 1.1 Edge to Voice Orchestrator

```json
{
  "api_version": "1.0",
  "interaction_id": "uuid",
  "conversation_id": "uuid",
  "device_id": "device-office-01",
  "user_id": "founder-user-id",
  "brand_id": 20,
  "locale": "en-IN",
  "transcript": "What are the three things I need to do today?",
  "stt_confidence": 0.96,
  "captured_at": "2026-09-15T10:00:00+05:30",
  "traceparent": "00-..."
}
```

### 1.2 Voice Orchestrator to Insight Decision

```json
{
  "request_type": "FOUNDER_PRIORITIES",
  "brand_id": 20,
  "limit": 3,
  "as_of": "2026-09-15T10:00:00+05:30",
  "user_context": {
    "user_id": "founder-user-id",
    "roles": ["exec"]
  },
  "reference_context": {},
  "trace_id": "uuid"
}
```

### 1.3 Insight Decision to Voice Orchestrator

```json
{
  "brief_id": "brief_...",
  "as_of": "...",
  "freshness": {},
  "items": [],
  "decision_trace_id": "trace_...",
  "template_id": "founder_priorities_v1",
  "template_context": {},
  "versions": {
    "config": "cfg_...",
    "catalogue": "47f987dbb82d",
    "ranking": "founder_rank_v1"
  }
}
```

## 2. Company Health Workflow

```text
1. Insight Decision resolves the active runtime bundle.
2. Business State returns the latest valid health snapshot for executive-visible nodes.
3. If allowed, Business State refreshes missing/stale state using certified Seleric MCP metrics.
4. Direct goal health and bounded dependency effects are read from the state snapshot.
5. Unknown or low-confidence data remains explicitly unknown.
6. Insight Decision aggregates node health by configured domain roll-up.
7. It selects material positive and negative changes and attaches evidence references.
8. It stores an optional decision trace and returns CompanyHealth.
9. Voice Orchestrator renders the configured spoken response.
```

Health states:

```text
HEALTHY
WATCH
ATTENTION_REQUIRED
UNKNOWN_DATA
```

`UNKNOWN_DATA` is never treated as healthy.

## 3. Founder Priority Workflow

```mermaid
flowchart TD
    S[Materialized Metric and Node State]
    C[Open Commitment and Data Risks]
    H[Material Negative/Positive Nodes]
    R[Suspected Root-Driver Hypotheses]
    T[Intervention Template Matching]
    E[Hard Eligibility Gates]
    D[Root Driver and Action Consolidation]
    P[Deterministic Priority Ranking]
    L[Minimum Score + 0-to-3 Limit]
    B[Founder Brief + Decision Trace]

    S --> H
    C --> T
    H --> R --> T --> E --> D --> P --> L --> B
```

### 3.1 Eligibility pseudocode

```text
for candidate in candidates:
    if not candidate.data_is_current:
        reject(STALE_DATA)
    elif candidate.data_quality_failed:
        reject(DATA_QUALITY_FAILED)
    elif candidate.data_confidence < policy.min_data_confidence:
        reject(LOW_DATA_CONFIDENCE)
    elif candidate.evidence_confidence < policy.min_evidence_confidence:
        reject(LOW_EVIDENCE_CONFIDENCE)
    elif candidate.materiality < policy.min_materiality:
        reject(IMMATERIAL)
    elif not candidate.has_action:
        reject(NO_ACTION)
    elif candidate.owner_is_handling and not candidate.requires_escalation:
        reject(ALREADY_OWNED)
    elif not candidate.founder_required:
        reject(DELEGABLE)
    elif not prerequisites_satisfied(candidate):
        reject(MISSING_PREREQUISITE)
    elif candidate.in_cooldown:
        reject(COOLDOWN)
    else:
        accept()
```

### 3.2 Consolidation order

1. Explicit configured root-driver key.
2. Same suspected root node plus intervention category.
3. Same action object and owner.
4. Configured mutually exclusive group.

Rejected duplicates remain in `DecisionTrace` as linked symptoms.

## 4. “Why?” Workflow

```mermaid
sequenceDiagram
    actor Founder
    participant Voice as Voice Orchestrator
    participant Insight as Insight Decision Service
    participant Trace as Decision Trace Store

    Founder->>Voice: Why?
    Voice->>Voice: Resolve last brief/intervention reference
    alt No valid reference
        Voice-->>Founder: Ask which item should be explained
    else Reference resolved
        Voice->>Insight: explain(decision_trace_id, intervention_id)
        Insight->>Trace: Read original trace
        Trace-->>Insight: Evidence, alternatives, score, exclusions
        Insight-->>Voice: Explanation DTO
        Voice->>Voice: Render explanation template
        Voice-->>Founder: Evidence-backed explanation
    end
```

The explanation reads the original evidence. It does not silently recalculate with newer data unless the founder explicitly requests an update.

## 5. Risk Workflow

Risk classes:

```text
OBSERVED_DEGRADATION
FORECAST_RISK
CHANGE_POINT
COMMITMENT_RISK
DATA_QUALITY_RISK
OPERATING_PRECONDITION_RISK
```

```text
Business State snapshots
  + forecast/anomaly evidence
  + material commitment state
  + data-quality/freshness state
  -> risk candidates
  -> eligibility and consolidation
  -> deterministic risk ranking
  -> risk brief
```

A forecast risk requires horizon, estimate/probability, uncertainty or calibration state, model version, backtest status and freshness.

## 6. Opportunity Workflow

Opportunity classes:

```text
POSITIVE_VARIANCE
UNDERFUNDED_WINNER
FUNNEL_IMPROVEMENT
PRODUCT_TRACTION
GEOGRAPHIC_TRACTION
CREATIVE_TRACTION
COST_EFFICIENCY
RESOLVED_CONSTRAINT
```

Required checks:

```text
signal stability
financial upside
margin availability
inventory availability
fulfilment capacity
creative/campaign capacity
owner readiness
goal conflict
confidence and freshness
```

When an operational prerequisite source is not connected, the output status is `VALIDATION_REQUIRED`, not an executable recommendation.

## 7. Business State Computation Data Flow

```mermaid
flowchart LR
    Scheduler[PostgreSQL Job Scheduler]
    Control[Published Runtime Bundle]
    Planner[Metric Query Planner]
    MCP[Seleric MCP]
    Raw[Certified Metric Series + Provenance]
    Features[Feature Calculators]
    Forecast[Forecast Strategies]
    Detect[Anomaly/Change Strategies]
    Health[Node Health Evaluator]
    CH[(ClickHouse State History)]
    PG[(PostgreSQL Latest State)]
    Event[BusinessStateRefreshed Outbox Event]

    Scheduler --> Control
    Scheduler --> Planner --> MCP --> Raw
    Raw --> Features
    Features --> Forecast
    Features --> Detect
    Features --> Health
    Forecast --> Health
    Detect --> Health
    Features --> CH
    Forecast --> CH
    Detect --> CH
    Health --> CH
    Health --> PG --> Event
```

Job key:

```text
brand_id + config_version + state_bucket + state_profile_id
```

Finality:

```text
INTRADAY
PROVISIONAL
FINAL
STALE
FAILED_QUALITY
```

A value can be available and still provisional because costs, returns or settlements lag.

## 8. Insight Decision Data Flow

```mermaid
flowchart LR
    Trigger[Voice Request / State Event / Schedule]
    State[Business State API]
    Control[Published Ontology + Policies]
    Commit[Material Commitment Risks]
    Graph[Declared Graph Resolver]
    Candidate[Intervention Factory]
    Eligible[Eligibility Pipeline]
    Dedupe[Consolidation]
    Rank[Ranking Policy]
    Brief[(Brief + Decision Trace)]
    Notify[Proactive Notification]

    Trigger --> State
    Trigger --> Control
    Trigger --> Commit
    State --> Graph
    Control --> Graph
    Graph --> Candidate
    Commit --> Candidate
    Candidate --> Eligible --> Dedupe --> Rank --> Brief
    Brief --> Notify
```

## 9. Configuration Publish Data Flow

```text
Admin command
  -> draft revision
  -> schema validation
  -> certified metric/dimension validation
  -> graph validation
  -> policy/adapter validation
  -> template compilation
  -> historical simulation/diff
  -> approval
  -> immutable published version
  -> ConfigPublished outbox event
  -> runtime bundle refresh
  -> targeted state and brief recompute
```

## 10. Meeting Start and Capture Workflow

```mermaid
sequenceDiagram
    actor Founder
    participant Edge as Edge Voice Node
    participant Voice as Voice Orchestrator
    participant Meeting as Meeting Intelligence Service
    participant Object as Object Storage

    Founder->>Edge: Start this meeting
    Edge->>Voice: START_MEETING intent
    Voice->>Meeting: Create meeting with device/user context
    Meeting-->>Voice: meeting_id + recording policy
    Voice-->>Edge: Start approved
    Edge->>Edge: Enable visible recording indicator
    Edge->>Edge: Record segmented audio to encrypted spool

    loop Each segment or after stop
        Edge->>Meeting: Upload part + checksum + idempotency key
        Meeting->>Object: Store encrypted object
        Object-->>Meeting: Object version/ETag
        Meeting-->>Edge: Part acknowledged
    end

    Founder->>Edge: Stop meeting or physical stop
    Edge->>Meeting: Stop meeting
    Meeting->>Meeting: Queue processing after manifest is complete
```

## 11. Meeting Processing Workflow

```mermaid
flowchart TD
    A[Audio Manifest Complete]
    T[Transcription Adapter]
    D[Diarization Adapter]
    U[Utterance Reconciliation]
    P[Participant Resolution]
    X[Rule/Dictionary Extraction]
    V[Completeness, Negation, Retraction Validation]
    R[Human Review]
    C[Approved Decisions/Commitments]
    Q[Verification Jobs]

    A --> T
    A --> D
    T --> U
    D --> U
    U --> P --> X --> V --> R --> C --> Q
```

Participant resolution order:

1. Preselected participant.
2. Calendar/meeting metadata.
3. Spoken self-identification.
4. Human reviewer mapping.
5. Future consented voiceprint only after a separate privacy design.
6. Leave unresolved.

## 12. Semantic Extraction Data Flow

```text
Utterances
  -> normalize punctuation and speaker turns
  -> phrase-match known people, roles, products, metrics and projects
  -> match commitment/decision/follow-up verb and dependency patterns
  -> parse deadlines relative to meeting start/timezone
  -> compose action and expected-outcome spans
  -> link ontology IDs
  -> validate negation, corrections and retractions
  -> create evidence-linked drafts
```

Example correction:

```text
“Aman will send the plan tomorrow. Actually, make that Friday.”
```

The later utterance updates the same draft deadline and both evidence spans remain attached.

Ambiguous example:

```text
“Someone from growth should look at it soon.”
```

Expected draft:

```text
owner = null
deadline = null
action = review referenced issue
confidence = low
review_required = true
```

## 13. Review and Approval Workflow

```mermaid
stateDiagram-v2
    [*] --> ExtractionDraft
    ExtractionDraft --> ReviewRequired
    ReviewRequired --> Corrected
    ReviewRequired --> Rejected
    ReviewRequired --> Approved
    Corrected --> Approved
    Approved --> CommitmentActive
    Rejected --> [*]
```

Approval stores machine proposal, human-final values, reviewer, time, reason, source utterances and extractor/rule version.

## 14. Commitment Verification Workflow

```mermaid
sequenceDiagram
    participant Worker as PostgreSQL Task Worker
    participant Meeting as Meeting Intelligence Service
    participant State as Business State Service
    participant Adapter as API/Document/Human Adapter
    participant Evidence as Evidence Store

    Worker->>Meeting: Claim due verification job
    Meeting->>Meeting: Load commitment + published rule version

    alt Certified metric condition
        Meeting->>State: Evaluate registered condition
        State-->>Meeting: Observation + metric provenance
    else API/document/human verification
        Meeting->>Adapter: Execute typed adapter
        Adapter-->>Meeting: Observation + evidence
    end

    Meeting->>Evidence: Persist evidence record
    Meeting->>Meeting: Evaluate rule and transition state

    alt Verified
        Meeting->>Meeting: Mark VERIFIED
    else Failed after grace period
        Meeting->>Meeting: Mark BREACHED + emit event
    else Not evaluable
        Meeting->>Meeting: Mark UNVERIFIABLE or request review
    end
```

## 15. Proactive Notification Workflow

```text
BusinessStateRefreshed or CommitmentBreached
  -> Insight Decision recomputes impacted candidate set
  -> notification eligibility/cooldown policy
  -> create Notification object with evidence
  -> Voice Orchestrator delivery queue
  -> enrolled device receives non-sensitive alert metadata
  -> device indicates alert; founder invokes playback
  -> delivery/acknowledgement recorded
```

V1 does not play sensitive business details without explicit user interaction.

## 16. Data Retention and Deletion Flow

Configurable retention objects:

```text
voice interaction transcript retention
audio retention
meeting transcript retention
utterance/evidence retention
model artifact retention
audit retention
```

```text
approved deletion request
  -> authorization and legal-hold check
  -> mark object pending deletion
  -> delete/tombstone derived extraction
  -> delete transcript if policy permits
  -> delete audio object versions
  -> retain minimum immutable audit record
  -> record completion
```

## 17. Error and Fallback Flows

### MCP unavailable

- Never fabricate data.
- Use a latest valid cached brief only when the policy permits.
- State the exact timestamp and finality.
- Otherwise report live data unavailable.

### State stale

- Return stale/provisional label.
- Suppress high-confidence action wording.
- Queue refresh without blocking indefinitely.

### STT or intent confidence low

- Ask the user to repeat or choose from supported commands.
- Do not call an executive handler.

### Meeting processing failure

- Preserve source audio and manifest.
- Retry with the configured fallback provider/profile.
- Move to review-required after retry exhaustion.

### Verification adapter failure

- Retry within policy.
- Preserve current commitment state.
- Mark verification delayed, not breached, until grace policy is exhausted.

## 18. Observability Flow

One executive interaction trace links:

```text
wake_event
voice_session
stt_span
intent_classification
insight_api
config_bundle
state_read_or_refresh
mcp_query_if_needed
decision_trace
template_render
tts_span
device_playback
```

One meeting trace links:

```text
meeting_start
audio_part_upload
transcription_job
diarization_job
utterance_reconciliation
extraction_job
review
commitment_approval
verification_job
status_transition
```
