# Implementation Plan, Ownership, Acceptance, and V1 Roadmap

## 0. What changed on 2026-08-31, and the timeline reality that comes with it

The founder replaced V1's business-reasoning model with an agent swarm (Blackboard, Coordinator, seven agents, task market, reputation, Governor — see docs 00-09). **The September 30, 2026 target date stands** — this plan is not being de-dated. But the honest starting point is: zero code exists today (2026-08-31), and the swarm architecture described in docs 00-09 is substantially larger than the deterministic pipeline it replaces. That is roughly a one-month runway for a design that includes durable agent orchestration, a persistent debate audit trail, bidding, reputation, and a non-recruitable safety layer, on top of everything the original plan already required (edge voice, ontology, meeting pipeline).

This plan handles that tension the same way the original plan's own risk-mitigation pattern already did — "build vertical slice first, freeze contracts, defer optional adapters" (§11, "September integration slips") — applied to the swarm specifically: **the full swarm architecture is the target and is not shrunk in docs 00-09; what changes here is sequencing.** A minimum thin slice (Governor + Blackboard + 3 agents on one real problem class) ships first and is the gate for everything else. Bidding, reputation, coalitions, and the full seven-agent population are sequenced as fast-follow phases explicitly scheduled *after* September 30 unless the thin slice proves faster than expected. This is flagged as the single highest-probability risk in §11 ("September swarm-scope slip") — read that entry before assuming the phased plan below is a comfortable one.

## 1. Product Summary


| Field              | V1 definition                                                                          |
| ------------------ | -------------------------------------------------------------------------------------- |
| Product            | Seleric Voice Node V1                                                                  |
| Delivery target    | September 30, 2026                                                                     |
| Primary user       | Founder/executive                                                                      |
| Core value         | Live business state converted into at most three evidence-backed founder interventions |
| Secondary value    | Meetings converted into approved commitments and verified outcomes                     |
| Intelligence style | Business State: deterministic rules, robust statistics, optional validated ML (unchanged). Insight Decision: agent-swarm reasoning under Governor control (changed 2026-08-31) |
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
| Founder priorities | At most three, agent-swarm-derived with confidence scores and Governor-cleared actions | Bidding/reputation/coalitions at full scale; broader agent population |
| Explanation        | Full Blackboard debate trace                                                     | Scenario and counterfactual analysis       |
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

- Control Plane Service, including Governor policy authoring
- Metric bindings and goal registry
- Business State Service and state feature marts
- Health policies
- Detector/model adapters
- **Insight Decision Service and the Seleric Swarm Layer**: Blackboard, Coordinator, Agent Registry, the seven agent roles, task market/bidding, Governor enforcement point
- Seleric MCP adapter

This is the critical path, and it is now materially larger than the deterministic pipeline it replaces — see §0 and §7's Week 1-2 thin-slice plan for how this workstream sequences the added scope without blowing the September date.

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



**Reading this plan:** weeks are compressed relative to the original deterministic-pipeline plan because the swarm adds real scope on the critical path (§0). Ontology/State workstream's Week 1-2 content below is the thin slice explicitly directed by the founder: Governor + Blackboard + a 3-agent subset (Observer, Diagnostic, Skeptic) on one real problem class, proven end-to-end before the remaining four agents, bidding, reputation, and coalitions are added.

### Week 1 - Platform contracts, live data slice, and swarm foundation

Device/Conversation:

- Assemble or order two hardware kits
- Flash raspOVOS and validate USB audio
- Create Seleric skill skeleton
- Train/test local wake word
- Implement Conversation API and two intents

Ontology/State (critical path — swarm thin-slice start):

- Create config schema and publication lifecycle, including `GovernorPolicy` and `AgentDefinition` object types
- Import initial node/edge draft
- Validate first certified MCP metrics
- Create MetricState and Blackboard (`SwarmCase`, `AgentMessage`, `Hypothesis`) schemas (doc 14 §10a)
- Stand up the Governor enforcement point with a minimal policy (read-only reasoning only — no grants yet) so nothing downstream can accidentally run ungoverned
- Produce live company-health payload (this path is fully deterministic and does not depend on the swarm — ship it first as a fallback-safe baseline)

Meeting:

- Define meeting, audio, utterance, commitment, and verification schemas
- Record one internal sample
- Run cloud and on-prem transcription spike

Gate:

```text
Laptop or Pi asks company health and receives live TH data with provenance
(deterministic path, unaffected by swarm work).
Governor enforcement point exists and fails closed by default.
```

### Week 2 - Physical voice loop and swarm thin slice (Governor + Blackboard + 3 agents)

Device/Conversation:

- Complete Pi wake, STT, backend, TTS loop
- Add LEDs, mute, stop, watchdog, reconnect
- Complete all executive intent routing (reads whatever the swarm has published so far; falls back to "no priorities yet" safely if nothing has converged)

Ontology/State (critical path):

- Admin CRUD for nodes, edges, metric bindings, and goals
- Implement rolling/delta/velocity/volatility features (Business State Service, unchanged/deterministic)
- Implement node health (unchanged/deterministic)
- **Swarm thin slice**: LangGraph Coordinator wired for exactly three agent roles — Observer, Diagnostic, Skeptic — on one real, narrow problem class (e.g., checkout-conversion degradation). No bidding yet (direct assignment), no reputation yet, no coalitions yet.
- Governor policy expanded to grant a narrow, reviewed set of tool permissions for these three agents only
- One full case, end-to-end, on real TH data: Observer opens a case, Diagnostic hypothesizes, Skeptic challenges, case converges or goes `INCONCLUSIVE`, action (if any) is Governor-checked

Gate:

```text
Physical node answers health using live TH data (deterministic).
At least one real swarm case completes end-to-end (Observer -> Diagnostic ->
Skeptic -> Governor check), fully inspectable in the debate trace.
```

### Week 3 - Full seven-agent population, task market, and admin hardening

- Add remaining four agent roles: Anomaly, Prediction, Strategy, Experiment
- Implement task market / bidding (doc 06 §9.3a) replacing Week 2's direct assignment
- Add robust anomaly/change-point policies feeding the Anomaly agent (Business State Service, deterministic)
- Add optional StatsForecast models for selected metrics that pass backtesting, feeding the Prediction agent
- Add risk and opportunity case triggers using the same swarm loop, scoped by problem class
- Complete config validation, approval, publish, and rollback — including Governor policy lifecycle
- Add the Admin swarm/debate inspector (doc 08 §3.18) and OpenTelemetry traces per case
- Build intent test corpus, Governor grant/deny fixture tests, and bid-selection formula tests (doc 06 "Swarm-specific tests")

Gate:

```text
All seven agent roles are live. Founder priorities, why, worried-about, and
opportunity questions are answered from converged swarm cases with disclosed
confidence and a complete debate trace. At least one Governor denial is
demonstrated end-to-end.
```

### Week 4 - Agent reputation, collective memory, and meeting intelligence

- Implement `pgvector` case-embedding pipeline and precedent retrieval (doc 06 §9.6a)
- Implement agent reputation tracking and wire it into bid-selection tie-break (doc 05 §39)
- Implement temporary coalitions for broad-problem cases (doc 06 §9.3a coalition path)
- Meeting Intelligence Service (unchanged, deterministic — proceeds in parallel, not blocked by swarm work):
  - Implement recording spool and segmented upload
  - Implement transcription/diarization adapters
  - Build vocabulary package from ontology
  - Implement spaCy/dateparser extraction
  - Build review screen
  - Approve and persist commitments
  - Add one certified metric verification adapter

Gate:

```text
A second, similar swarm case demonstrably retrieves and cites the first
case's precedent before independent investigation. A real one-to-one
meeting becomes approved evidence-linked commitments.
```

### Week 5 - Closed loop and reliability

- Run deadline workers and verification (meeting side, unchanged)
- Feed breach/unverifiable states into swarm cases as risk evidence
- Test stale data, LLM provider outage/pause-resume, Governor policy-fetch failure, duplicate uploads, and worker crashes (doc 09 Runbooks A-I)
- Complete backup/restore drill, including Governor policy rollback
- Complete ten end-to-end rehearsals, including at least one Governor denial and one case reaching `INCONCLUSIVE` cleanly
- Freeze features by September 26

Gate:

```text
At least one commitment reaches VERIFIED, BREACHED, or UNVERIFIABLE from real
evidence. At least one Governor denial and one fail-closed scenario are
demonstrated without data loss or a silently-dropped case.
```

### September 29-30 - Demo lock

- No new features
- Final config publish, including final Governor policy version
- Final data reconciliation
- Final acoustic tuning
- Backup device/audio path
- Acceptance script against exact target utterances, including at least one "why" follow-up that surfaces a debate trace

### Fast-follow phases (explicitly sequenced after September 30, not cut from the design)

The full architecture in docs 00-09 is the target; the items below are the parts most likely to still be maturing past the September date if the thin-slice sequencing above needs to compress further to hold the date:

1. Bidding-formula tuning beyond the documented default (doc 06 §9.3a) based on real case data.
2. Reputation-driven agent selection maturing beyond a simple tie-break.
3. Broader coalition use for genuinely ambiguous multi-domain problems.
4. Expanding the agent population beyond seven roles via `AgentDefinition` config (SWARM-004) once the seven-role loop is stable.
5. A2A-facing registry work — explicitly not started until its adoption trigger is met (doc 01 §3a.10).



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
selected interventions with a complete Blackboard debate trace: 100 percent
selected interventions with a disclosed confidence score: 100 percent
more than 3 founder priorities: zero
duplicate root-cause priorities in same brief: zero in golden set
unsupported inventory/capacity assumption: zero
```

### Swarm and Governor [new]

```text
cases reaching a proposed action without a recorded Skeptic pass: zero
Governor-gated actions executed without a recorded grant decision: zero
Governor policy fetch failures resulting in a permissive (fail-open) action: zero
cases silently dropped (neither converged, inconclusive, nor abandoned with reason): zero
precedent retrieval demonstrated on at least one repeat-pattern case: yes
agent reputation updated after at least one confirmed case outcome: yes
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
- [ ] Initial Governor policy approved and published
- [ ] Seven `AgentDefinition` roles approved and published
- [ ] Top-three golden tests (swarm case fixtures with mocked reasoning provider) approved
- [ ] Freshness/finality wording approved
- [ ] At least one live Governor denial rehearsed and confirmed visible in audit trail



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
| Top-three list is noisy or duplicated              | Medium      | High   | Same issue appears under multiple symptoms                  | Coordinator root-key consolidation, mandatory Skeptic pass, golden case-fixture tests       | Insight service       |
| Forecasting produces unstable alerts               | Medium      | Medium | Poor interval coverage, high false-alert review rate        | Use simple baseline, backtest gate, cooldown/hysteresis, disable per metric                | Data/ML               |
| Dependency edge is mistaken for causal proof       | Medium      | High   | Explanations use definitive causal language                 | Ontology-grounded citation requirement, Skeptic causal-language check, `VALIDATED_CAUSAL` requires approval | Ontology steward / Insight service |
| **September swarm-scope slip** [new]               | **High**    | **High** | No end-to-end swarm case (Observer->Diagnostic->Skeptic->Governor) by end of Week 2 | Thin-slice-first sequencing (§7 Week 1-2); full 7-agent population, bidding, reputation, coalitions explicitly sequenced as fast-follow after September 30 if the thin slice runs late — see §0 and §7 fast-follow list; do not silently cut the Skeptic or Governor to save time | Program owner |
| **Agent debate never converges or loops** [new]    | Medium      | High   | `swarm_case_time_to_converge_seconds` exceeds SLO; repeated handoffs without new evidence | Governor `max_iteration_count` forces `INCONCLUSIVE` rather than infinite debate (doc 09 §5a.1) | Insight service |
| **Governor policy is too permissive at launch**    | Medium      | High   | An agent action executes that a human reviewer would not have approved | Hard platform ceilings independent of policy config (doc 09 §5a.1); start with a deliberately narrow Week 1-2 policy and expand only as agents prove reliable | Security administrator / Insight service |
| **LLM provider cost or latency exceeds plan**       | Medium      | Medium | `llm_provider_token_spend_by_case` or `llm_provider_latency_ms` trending up | Per-case/day/role API spend limits (Governor); provider adapter allows switching without a rewrite (doc 04 §4a) | Insight service |
| **Swarm produces a confidently wrong conclusion (calibration failure)** [new] | Medium | High | Reviewer disagrees with a high-confidence brief item | Skeptic mandatory pass, agent reputation/calibration tracking feeding future bid selection, Runbook I (doc 09 §16) for post-hoc review without live-editing the case | Insight service |
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
published configuration bundle, including initial Governor policy
approved initial ontology and goals
state refresh for selected executive metrics (deterministic, unchanged)
Seleric Blackboard + Governor enforcement point
at least three agent roles (Observer, Diagnostic, Skeptic) end-to-end on real data
top-three founder brief assembly with confidence + debate trace
meeting audio/transcript/review flow
one real verification adapter
admin configuration and rollback, including Governor policy admin
security, audit, monitoring, backup
```

### Must be implemented by September 30, may still be maturing

```text
full seven-agent population
task market / bidding beyond direct assignment
agent reputation tracking
temporary coalitions
```

These are not optional scope — doc 00-09 specify them as the target architecture, and doc 01 explicitly rejected cutting bidding/reputation/coalitions to "phase 2" without a concrete reason. What is flexible is exactly how mature each is by the September date; §7's fast-follow list and the "September swarm-scope slip" risk (§11) are the honest tracking mechanism for this, not a silent scope cut.

### May use a managed adapter to protect the date

```text
streaming STT
TTS
batch meeting transcription
diarization where local quality is insufficient
OIDC and managed PostgreSQL/object storage
LLM reasoning provider (managed API acceptable; adapter-swappable per doc 04 §4a)
```

### Must not block the prototype

```text
custom hardware PCB
large causal model
TFT/autoencoder training
online feature store
production writes beyond the Governor's narrow, reviewed allowlist
A2A protocol implementation (doc 01 §3a.10 — trigger-gated, not started)
agent population beyond seven roles
enterprise graph database
multi-region HA
```

The platform contracts are kept from the start, so each deferred implementation can be added as a registered adapter, an `AgentDefinition`, or a Governor policy change rather than a rewrite.