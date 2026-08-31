# Implementation Plan, Ownership, Acceptance, and V1 Roadmap

## 1. Product Summary


| Field              | V1 definition                                                                          |
| ------------------ | -------------------------------------------------------------------------------------- |
| Product            | Seleric Voice Node V1                                                                  |
| Delivery target    | September 30, 2026                                                                     |
| Primary user       | Founder/executive                                                                      |
| Core value         | Live business state converted into at most three evidence-backed founder interventions |
| Secondary value    | Meetings converted into approved commitments and verified outcomes                     |
| Intelligence style | Deterministic rules, robust statistics, optional validated ML                          |
| Voice style        | Local wake word, configurable STT/TTS, bounded intents, deterministic NLG              |
| Data truth         | Existing Seleric MCP and certified semantic metrics                                    |
| Control truth      | Versioned PostgreSQL configuration and policy objects                                  |
| Analytical history | Existing ClickHouse plus executive-state marts                                         |
| Physical platform  | Raspberry Pi 5 plus USB microphone array and speaker                                   |
| Admin platform     | Appsmith over domain APIs                                                              |
| Azure deployment   | Container Apps, PostgreSQL Flexible Server, Blob, Entra, Key Vault, App Insights       |
| On-prem deployment | Docker Compose, PostgreSQL, MinIO, Keycloak, Traefik, OTel/Grafana stack               |




## 2. V1 Feature Matrix


| Capability         | V1 scope                                                                         | Extension path                             |
| ------------------ | -------------------------------------------------------------------------------- | ------------------------------------------ |
| Wake word          | Hey Seleric, local                                                               | Multiple profiles/languages                |
| Company health     | Configured executive nodes/goals                                                 | Full enterprise ontology                   |
| Founder priorities | At most three, deterministic                                                     | Constrained planning/action execution      |
| Explanation        | Stored decision trace                                                            | Scenario and counterfactual analysis       |
| Risk               | Observed, forecast, commitment, data quality                                     | Broader predictive models                  |
| Opportunity        | Positive variance plus prerequisites                                             | Budget/portfolio optimization              |
| Proactive alert    | Scheduled morning brief and critical policy alerts                               | Multi-channel personalized notifications   |
| Meeting capture    | One-to-one, local recording, full fledged assistant, not a standalone recording. | Multi-room and conference integrations     |
| Transcription      | Cloud or on-prem adapter                                                         | Domain-adapted ASR                         |
| Extraction         | Rules/dictionaries plus review                                                   | Trained NER/classification                 |
| Commitment         | Approved structured object                                                       | Workflow/task platform synchronization     |
| Verification       | Registered metric/API/document/human rules                                       | Temporal and richer evidence orchestration |
| Admin              | Full config and review surfaces                                                  | Custom product UI and delegation           |
| ML                 | Optional per metric, backtested                                                  | MLflow-managed broader model suite         |




## 3. API Surface



### Conversation

```text
POST /v1/conversations
POST /v1/conversations/{id}/utterances
GET  /v1/conversations/{id}
```



### Executive

```text
GET /v1/executive/health
GET /v1/executive/priorities
GET /v1/executive/briefs/{brief_id}/interventions/{id}/explanation
GET /v1/executive/risks
GET /v1/executive/opportunities
```



### Configuration

```text
GET  /v1/config/runtime-bundle
POST /v1/config/drafts
POST /v1/config/drafts/{id}/validate
POST /v1/config/drafts/{id}/approve
POST /v1/config/drafts/{id}/publish
POST /v1/config/versions/{id}/rollback
```



### Meetings

```text
POST /v1/meetings
POST /v1/meetings/{id}/audio-parts
POST /v1/meetings/{id}/stop
GET  /v1/meetings/{id}/review
POST /v1/meetings/{id}/approve
GET  /v1/commitments
POST /v1/commitments/{id}/verify
```



## 4. Core Configuration Objects

```text
BusinessNodeType
BusinessNode
BusinessEdgeType
BusinessEdge
MetricBinding
GoalDefinition
OwnerBinding
EscalationPolicy
HealthPolicy
FeatureDefinition
DetectorPolicy
ForecastPolicy
InterventionTemplate
EligibilityPolicy
RankingPolicy
IntentDefinition
ResponseTemplate
DeviceProfile
ProviderProfile
ExtractionRuleSet
VerificationRuleDefinition
NotificationPolicy
RetentionPolicy
ConfigurationVersion
```



## 5. Initial Executive Node Set

The exact node set must be approved through the admin system. A practical first set uses current MCP capabilities:

```text
Company Financial Health
  - All-channel net sales
  - All-channel net profit
  - Total ad spend
  - Net ROAS / MER

Acquisition
  - Meta delivery and efficiency
  - Google delivery and efficiency
  - Amazon ads delivery

Website Funnel
  - Session PDP rate
  - Session ATC rate
  - ATC to checkout
  - Checkout to purchase
  - Session conversion

Product Performance
  - Product net revenue
  - Units sold
  - Product gross margin
  - Returns/cancels

Attribution and Channel
  - Meta/Google/Amazon/organic channel orders and net sales

Execution
  - Critical approved commitments
  - Verification outcomes

Data Health
  - Metric freshness
  - Query warnings
  - Failed state jobs
```

Inventory, procurement, supplier, HR, and operational capacity become additional nodes only when their data contracts are connected and certified.

## 6. Team Ownership



### Workstream 1 - Device and Conversation

Accountable role:

- Voice/platform engineer

Owns:

- Pi image
- Microphone/speaker integration
- openWakeWord
- OVOS configuration and Seleric skill
- Device identity
- Voice provider adapters
- Voice Orchestrator Service
- Intent evaluation
- NLG and interruption testing



### Workstream 2 - Ontology, State, and Decision

Accountable role:

- Senior data/ML engineer

Owns:

- Control Plane Service
- Metric bindings and goal registry
- Business State Service and state feature marts
- Health policies
- Detector/model adapters
- Insight Decision Service
- Suspected root-driver hypotheses
- Intervention templates
- Ranking and decision traces
- Seleric MCP adapter

This is the critical path.

### Workstream 3 - Meeting and Verification

Accountable role:

- Backend/semantic workflow engineer

Owns:

- Meeting Intelligence Service
- Audio/object pipeline
- Transcription/diarization
- Rule-based extraction
- Review interface
- Commitment lifecycle
- Verification adapters and jobs
- Task-system adapters



### Shared platform

Owns:

- PostgreSQL
- Container deployment
- Identity
- OpenTelemetry
- Appsmith
- CI/CD
- Backup/restore



## 7. Week-by-Week Plan



### Week 1 - Platform contracts and live data slice

Device/Conversation:

- Assemble or order two hardware kits
- Flash raspOVOS and validate USB audio
- Create Seleric skill skeleton
- Train/test local wake word
- Implement Conversation API and two intents

Ontology/State:

- Create config schema and publication lifecycle
- Import initial node/edge draft
- Validate first certified MCP metrics
- Create MetricState and FounderBrief contracts
- Produce live company-health payload

Meeting:

- Define meeting, audio, utterance, commitment, and verification schemas
- Record one internal sample
- Run cloud and on-prem transcription spike

Gate:

```text
Laptop or Pi asks company health and receives live TH data with provenance.
```



### Week 2 - Physical voice loop and top-three engine

Device/Conversation:

- Complete Pi wake, STT, backend, TTS loop
- Add LEDs, mute, stop, watchdog, reconnect
- Complete all executive intent routing

Ontology/State:

- Admin CRUD for nodes, edges, metric bindings, and goals
- Implement rolling/delta/velocity/volatility features
- Implement node health
- Implement intervention templates and eligibility rules
- Implement deterministic ranking and dedupe

Gate:

```text
Physical node answers health, priorities, and why using live TH data.
```



### Week 3 - Risk, opportunity, and admin hardening

- Add robust anomaly/change-point policies
- Add optional StatsForecast models for selected metrics that pass backtesting
- Add risk and opportunity candidate pipelines
- Add prerequisite handling
- Complete config validation, approval, publish, and rollback
- Add decision inspector and OpenTelemetry traces
- Build intent test corpus and failure tests

Gate:

```text
All five executive intelligence questions pass golden tests and traces.
```



### Week 4 - Meeting intelligence

- Implement recording spool and segmented upload
- Implement transcription/diarization adapters
- Build vocabulary package from ontology
- Implement spaCy/dateparser extraction
- Build review screen
- Approve and persist commitments
- Add one certified metric verification adapter

Gate:

```text
A real one-to-one meeting becomes approved evidence-linked commitments.
```



### Week 5 - Closed loop and reliability

- Run deadline workers and verification
- Feed breach/unverifiable states into executive risk and attention
- Test stale data, provider outage, duplicate uploads, and worker crashes
- Complete backup/restore drill
- Complete ten end-to-end rehearsals
- Freeze features by September 26

Gate:

```text
At least one commitment reaches VERIFIED, BREACHED, or UNVERIFIABLE from real evidence.
```



### September 29-30 - Demo lock

- No new features
- Final config publish
- Final data reconciliation
- Final acoustic tuning
- Backup device/audio path
- Acceptance script against exact target utterances



## 8. Acceptance Metrics



### Voice

```text
wake false positive target: less than 1 per normal office day after tuning
wake success at 1-3 meters: target at least 95 percent in test set
supported-intent accuracy: target at least 95 percent
low-confidence false execution: zero in acceptance set
interruption latency: less than 300 ms in tested setup
```



### Data and decision

```text
certified metric usage: 100 percent
answers with query/config/version provenance: 100 percent
selected interventions with decision trace: 100 percent
more than 3 founder priorities: zero
duplicate root-cause priorities in same brief: zero in golden set
unsupported inventory/capacity assumption: zero
```



### Meeting

```text
audio loss in 30-minute test: zero
commitment fields without source evidence: zero
unapproved commitment becoming active: zero
owner/deadline invention in golden set: zero
verification result without evidence: zero
```



## 9. Operational Cost Controls

- Use one PostgreSQL cluster with separate schemas.
- Use Container Apps scale-to-zero for batch workers when safe.
- Keep Conversation and Executive APIs warm during office hours.
- Run WhisperX only on demand; use cloud batch STT if no GPU is available.
- Do not deploy Feast, Temporal, or a message broker in V1.
- Store raw audio under lifecycle rules.
- Cache immutable runtime bundles and metric definitions.
- Precompute briefs on schedule to reduce interactive latency.



## 10. Go-Live Checklist



### Platform

- [ ] Production configuration version published
- [ ] Metric catalogue version validated
- [ ] Database backups and restore tested
- [ ] Device credentials issued and revocation tested
- [ ] Provider quotas and fallback configured
- [ ] OpenTelemetry dashboards and alerts active



### Voice

- [ ] Wake-word acoustic test passed
- [ ] Muting and recording indicator passed
- [ ] All target intents passed
- [ ] Low-confidence fallback passed
- [ ] Barge-in or push-to-interrupt fallback passed



### Decision

- [ ] Goals and owners approved
- [ ] Health bands approved
- [ ] Eligibility and ranking policy approved
- [ ] Top-three golden tests approved
- [ ] Freshness/finality wording approved



### Meeting

- [ ] Consent language approved
- [ ] Audio retention policy approved
- [ ] Extraction review workflow passed
- [ ] Verification rule passed against real data
- [ ] Breach escalation appears in executive brief



## 11. Delivery Risk Register


| Risk                                               | Probability | Impact | Early signal                                                | Mitigation                                                                                 | Owner                 |
| -------------------------------------------------- | ----------- | ------ | ----------------------------------------------------------- | ------------------------------------------------------------------------------------------ | --------------------- |
| Far-field echo or wake reliability fails           | Medium      | High   | Poor test accuracy at 2-3 m; self-trigger during playback   | Use USB DSP array, tune acoustics, add push-to-talk fallback, short audio-specialist spike | Voice/platform        |
| Existing metric cannot support an intended insight | High        | High   | Missing certified metric, dimension, freshness, or target   | Fail closed; expose as data-gap node; add contract before recommendation                   | Data/ML               |
| Goal/owner configuration is incomplete             | High        | High   | Large number of ineligible candidates or generic priorities | Seed a small approved executive ontology; admin completeness report                        | Business owner + data |
| Top-three list is noisy or duplicated              | Medium      | High   | Same issue appears under multiple symptoms                  | Hard eligibility, root key, graph dedupe, golden brief tests                               | Insight service       |
| Forecasting produces unstable alerts               | Medium      | Medium | Poor interval coverage, high false-alert review rate        | Use simple baseline, backtest gate, cooldown/hysteresis, disable per metric                | Data/ML               |
| Dependency edge is mistaken for causal proof       | Medium      | High   | Explanations use definitive causal language                 | Evidence taxonomy and template wording; `VERIFIED_CAUSAL` requires approval                | Ontology steward      |
| Rule-based meeting extraction has low recall       | High        | Medium | Reviewers add many missing commitments                      | Start with one-to-one corpus, vocabulary/rule updates, optional model after labels         | Meeting team          |
| Owner/deadline is invented or misresolved          | Medium      | High   | Reviewer correction/ambiguity rates increase                | Evidence requirement, null allowed, participant resolution, mandatory review               | Meeting team          |
| GPU unavailable for local transcription            | Medium      | Medium | Processing exceeds meeting SLA                              | Managed batch STT adapter; run on CPU for smaller model; on-demand GPU host later          | Platform              |
| Vexa/OVOS open-source dependency changes           | Medium      | Medium | Breaking release, archived plugin, CVE                      | Pin image/version, adapter boundary, SBOM, fallback implementation                         | Platform              |
| Appsmith becomes coupled to tables                 | Medium      | Medium | Business rules appear in UI queries                         | API-only writes; contract tests; no production DB credentials                              | Control plane         |
| PostgreSQL task queue cannot meet later scale      | Low in V1   | Medium | Queue lag/locks grow, many long-lived workflows             | TaskQueue port permits migration to Temporal/broker without domain rewrite                 | Platform              |
| Data or model versions are not reproducible        | Medium      | High   | Explanation cannot reconstruct original brief               | Pin config/catalogue/model/feature IDs in every trace; immutable artifacts                 | All services          |
| September integration slips                        | Medium      | High   | No live-data thin slice by end of Week 1                    | Build vertical slice first; freeze service contracts; defer optional adapters              | Program owner         |




## 12. MVP Effort Boundary



### Must be implemented

```text
one physical edge node
six bounded executive intents
published configuration bundle
approved initial ontology and goals
state refresh for selected executive metrics
deterministic health and candidate pipeline
top-three ranking and explanation trace
meeting audio/transcript/review flow
one real verification adapter
admin configuration and rollback
security, audit, monitoring, backup
```



### May use a managed adapter to protect the date

```text
streaming STT
TTS
batch meeting transcription
diarization where local quality is insufficient
OIDC and managed PostgreSQL/object storage
```



### Must not block the prototype

```text
custom hardware PCB
large causal model
TFT/autoencoder training
online feature store
fully autonomous write actions
multi-agent planner
enterprise graph database
multi-region HA
```

The platform contracts are kept from the start, so each deferred implementation can be added as a registered adapter or configuration object rather than a rewrite.