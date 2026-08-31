# Admin and Configuration System Specification

## 1. Purpose

The admin system is a first-class part of V1. It allows authorized operators to add and change business objects, goals, metric bindings, policies, providers, intents, templates, meeting rules, and verification behavior without editing source code.

Appsmith Community Edition is used as the initial UI. All writes go through domain APIs.

## 2. Configuration Lifecycle

```mermaid
stateDiagram-v2
    [*] --> Draft
    Draft --> Validating
    Validating --> Draft: errors
    Validating --> Validated: passed
    Validated --> AwaitingApproval
    AwaitingApproval --> Draft: rejected
    AwaitingApproval --> Approved
    Approved --> Published
    Published --> Retired
    Published --> Published: runtime reads immutable version
    Published --> RolledBack: rollback command
    RolledBack --> Retired
```

Rules:

- Draft rows may change.
- Validated and approved states retain the validation result.
- Publishing creates an immutable version.
- Rollback publishes a new runtime pointer to a prior immutable version.
- Services record the exact published version used.

## 3. Admin Modules

### 3.1 Business Ontology

Manage:

- Node types
- Nodes
- Edge types
- Edges
- Domains/functions/processes
- Owners
- Tags and external references
- Effective dates

Validation:

- Node and edge type compatibility
- Unique stable IDs
- Disallowed cycles in dependency/causal subgraph
- Orphan nodes
- Missing owners where required
- Edge-weight range

### 3.2 Metric Bindings

Manage:

- Seleric catalogue metric ID
- Node binding
- Entity/dimension scope
- Time grain
- Aggregation/binding role
- Weight
- Criticality
- Freshness requirement
- Finality requirement

Validation:

- Metric resolves in current catalogue
- Requested dimensions are supported
- Ratio semantics are preserved
- Time axis is explicit
- Placement/event-date differences are acknowledged

### 3.3 Goals

Manage:

- Goal ID and name
- Metric binding
- Target value or target expression
- Direction: maximize, minimize, range, maintain
- Tolerance
- Evaluation period
- Baseline policy
- Effective dates
- Criticality
- Owner
- Founder escalation rule

Example:

```json
{
  "goal_id": "goal_checkout_cvr",
  "metric_binding_id": "checkout_cvr_all_mobile",
  "target": {"type": "minimum", "value": 0.035},
  "tolerance": 0.003,
  "period": "P1D",
  "owner_id": "role_product_head",
  "founder_escalation_policy_id": "critical_cross_functional"
}
```

### 3.4 Feature Definitions

Manage:

- Feature ID
- Calculator implementation ID
- Window
- Lag
- Minimum observations
- Null policy
- Outlier policy
- Grain restrictions
- Version

No arbitrary Python is accepted through admin. Only registered calculators and validated parameters are selectable.

### 3.5 Detector and Forecast Policies

Manage:

- Metric pattern or binding
- Detector strategy
- Training/lookback window
- Threshold type
- Minimum history
- Seasonality
- Calibration requirement
- Fallback strategy
- Suppression and cooldown

Example strategy choices:

```text
target_deviation
period_comparison
robust_mad
ewma_residual
statsforecast_interval_residual
change_point
```

### 3.6 Node Health Policies

Manage:

- Direct metric aggregation
- Alpha value
- Dependency edge types
- Maximum parent penalty
- Data-confidence policy
- Status bands
- Unknown-state policy

### 3.7 Intervention Templates

Manage:

- Applicable node types/tags
- Trigger predicate
- Root-cause category
- Suggested action template
- Default owner type
- Founder-required rule
- Exposure estimator
- Preconditions
- Cooldown
- Dedupe key expression
- Response template reference

Predicates use a safe declarative expression language, not executable code.

Example:

```json
{
  "template_id": "checkout_degradation",
  "applies_to": {"node_type": "funnel_stage"},
  "predicate": {
    "all": [
      {"field": "health", "op": "lt", "value": 0.6},
      {"field": "severity", "op": "gte", "value": 2.0}
    ]
  },
  "dedupe_key": "checkout_payment_degradation",
  "founder_rule": "cross_functional_or_loss_gt_threshold"
}
```

### 3.8 Eligibility and Ranking

Manage eligibility order and parameters:

```text
freshness
confidence
materiality
actionability
ownership
founder leverage
prerequisites
cooldown
deduplication
```

Manage ranking:

- Strategy ID
- Normalizers
- Component weights
- Minimum score
- Maximum selected items
- Tie-break order

V1 maximum remains three for founder-priority intent.

### 3.9 Intent Administration

Manage:

- Intent ID
- Example utterances
- Keywords and negative examples
- Synonyms
- Entity/slot definitions
- Handler ID
- Confidence threshold
- Fallback message
- Allowed roles
- Locale

Training workflow:

1. Edit examples
2. Validate minimum examples and class separation
3. Train candidate classifier
4. Run fixed evaluation set
5. Compare with active classifier
6. Approve
7. Publish model/package version
8. Retain rollback version

### 3.10 Response Templates

Manage:

- Intent
- Response subtype
- Locale
- Jinja2 template
- Optional SSML template
- Maximum item and word count
- Required variables
- Freshness/provisional clauses

Preview must use synthetic typed payloads and production-like formatting.

### 3.11 Device Administration

Manage:

- Device ID
- Assigned user/room/brand
- Hardware profile
- Wake-word profile
- Voice provider profile
- Allowed intents
- Certificate/client status
- Software version
- Last heartbeat
- Local storage threshold
- Revoked state

### 3.12 Provider Profiles

Manage non-secret provider configuration:

- Adapter ID
- Endpoint
- Model
- Language
- Timeout
- Retry
- cost/usage limit
- secret reference
- fallback provider

### 3.13 Meeting Dictionaries and Rules

Manage:

- Person names and aliases
- Role names
- Product names and aliases
- Metric names and catalogue IDs
- Process/project/system names
- Commitment verbs
- Decision phrases
- Follow-up phrases
- Deadline patterns
- Stop phrases and negation patterns

Rules are versioned and tested against approved utterance examples.

### 3.14 Verification Rules

Manage:

- Rule ID
- Adapter ID
- Input schema
- Target object type
- Metric/API/document references
- Evaluation expression
- Observation window
- Grace period
- Retry policy
- Manual fallback
- Success/failure wording

No raw SQL field exists in the admin UI.

### 3.15 Meeting Review

Review screen shows:

- Audio playback at utterance timestamp
- Speaker label and proposed participant
- Transcript
- Extracted field
- Confidence
- Source utterance IDs
- Ontology/entity match
- Approve, correct, reject, or mark missing

Corrections are stored separately from machine output for evaluation and future training.

### 3.16 Audit and Decision Inspector

Admin can inspect:

- Voice interaction
- Intent and confidence
- Handler invoked
- Config version
- Metric queries and freshness
- Feature and detector versions
- Candidate interventions
- Eligibility decisions
- Ranking components
- Selected brief
- Rendered template
- Commitment verification evidence

## 4. Admin Roles

| Role | Primary permissions |
|---|---|
| Platform Admin | Devices, providers, identity mappings, deployment flags |
| Ontology Steward | Nodes, edges, terminology, metric bindings |
| Business Goal Owner | Goals, thresholds, owner and escalation settings |
| Data/ML Steward | Features, detectors, models, state validation |
| Executive Policy Admin | Intervention, eligibility, ranking, notification policy |
| Meeting Reviewer | Participant mapping, transcript correction, extraction review |
| Commitment Approver | Approve commitments and verification rules |
| Auditor | Read-only config, decision, access, and verification history |

Separation of duties can require two roles for publish operations.

## 5. Configuration API

### Draft command

```json
{
  "base_version": "cfg_42",
  "change_set": [
    {
      "operation": "upsert",
      "object_type": "goal",
      "object_id": "goal_checkout_cvr",
      "payload": {}
    }
  ],
  "reason": "September target revision"
}
```

### Validation result

```json
{
  "draft_id": "draft_91",
  "status": "FAILED",
  "errors": [],
  "warnings": [],
  "affected_services": ["state-decision"],
  "recompute_scope": {
    "node_ids": [],
    "from": "2026-09-01"
  }
}
```

### Runtime bundle

```json
{
  "version": "cfg_43",
  "hash": "sha256:...",
  "effective_at": "...",
  "brand_id": 20,
  "ontology": {},
  "goals": [],
  "policies": {},
  "templates": {},
  "provider_profiles": [],
  "created_at": "..."
}
```

## 6. Dynamic Reload

Services cache the runtime bundle by version and ETag.

On ConfigPublished:

1. Service receives outbox event or polls version endpoint
2. Fetches new bundle
3. Validates local adapter availability
4. Atomically swaps active bundle
5. Emits config activation telemetry
6. Recomputes only affected state when specified
7. Retains previous bundle for immediate rollback

## 7. Admin Safety

- Appsmith uses API tokens scoped to the logged-in user.
- It has no direct write credentials for domain databases.
- Secret values are never returned after creation.
- Raw SQL and code fields are not exposed.
- Publish actions are audited with before/after hashes.
- Every configuration object supports effective dates.
- Validation must run against the current Seleric metric catalogue version.
