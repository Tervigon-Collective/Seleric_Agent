# Seleric Voice Node V1 - System Spec Sheet

## 1. Product identity

| Field | Specification |
|---|---|
| Product | Seleric Voice Node V1 |
| Target date | September 30, 2026 |
| Primary user | Founder / executive |
| Initial tenant | Tilting Heads (`brand_id = 20`) |
| Product form | Physical Linux voice node plus configurable backend platform |
| Primary outcome | At most three evidence-backed founder interventions from live business state |
| Secondary outcome | Meetings converted into reviewed commitments and verified outcomes |
| Reasoning policy | **Changed 2026-08-31.** Business State: deterministic domain logic, statistical ML only where backtested (unchanged). Insight Decision: agent-swarm reasoning (Blackboard, Coordinator, 7 agents, task market, reputation) under a non-recruitable Governor safety boundary — evidence-grounded and confidence-scored, not reproducible/deterministic (docs 00-09) |
| Data source of truth | Existing Seleric MCP / Cube certified metrics over current warehouse systems |
| Configuration source of truth | Versioned PostgreSQL control-plane objects |
| State/history | Existing ClickHouse plus executive-state marts |
| Audio/artifact store | S3-compatible object storage / Azure Blob |
| Admin | Appsmith Community Edition over Control Plane APIs |
| Deployment | Hybrid recommended; Azure and fully on-prem supported |

## 2. V1 user commands

| Command | Handler | Output contract |
|---|---|---|
| “How are we doing?” | `get_company_health` | Overall status, strongest/weakest nodes, freshness, evidence |
| “What are the three things I need to do today?” | `get_founder_priorities` | Zero to three distinct founder-required interventions |
| “Why?” | `explain_intervention` | Evidence chain for the referenced intervention |
| “What are you worried about?” | `get_risks` | Observed, predicted, commitment, and data-quality risks |
| “What opportunity are we missing?” | `get_opportunities` | Positive variance with explicit prerequisites |
| “Start this meeting.” | `start_meeting` | Visible recording state and meeting ID |
| “Stop this meeting.” | `stop_meeting` | Recording finalization and processing acknowledgement |

## 3. Runtime services

| Deployment unit | Primary responsibility | Owns data | Required in V1 |
|---|---|---|---:|
| Edge Voice Node | Wake, audio I/O, intent handoff, meeting recording | Local device/session/spool | Yes |
| Voice Orchestrator | Intent routing, dialogue references, NLG coordination | Dialogue sessions | Yes |
| Business State Service | Metric retrieval, derived features, forecasts, anomalies, health snapshots | State/model outputs | Yes |
| Insight Decision Service | Hosts the Seleric Swarm Layer: Blackboard, Coordinator, Agent Registry, 7 agents, task market, Governor enforcement point; root-driver hypotheses, candidate generation, prioritization, briefs | Blackboard cases, briefs, debate traces, agent registry/reputation | Yes |
| Meeting Intelligence Service | Transcription, diarization, extraction, review, commitment, verification | Meeting/commitment state | Yes |
| Control Plane Service | Configuration lifecycle, validation, simulation, publish/rollback | Config/audit | Yes |
| Admin UI | Configuration and review user experience | No domain tables | Yes |

## 4. Shared dependencies

| Dependency | Selected implementation | Alternative |
|---|---|---|
| Certified data access | Existing Seleric MCP and Cube | None for V1 |
| **Agent orchestration** | **LangGraph (swarm/handoff pattern)** | **None selected for V1; adapter-isolated behind swarm ports** |
| **Case similarity search** | **`pgvector` PostgreSQL extension** | **Dedicated vector DB only if V1 scale exceeds it** |
| **LLM reasoning provider** | **Adapter-selected (e.g. Azure OpenAI, Anthropic)** | **Self-hosted OpenAI-compatible endpoint** |
| Operational/config DB | PostgreSQL (incl. Blackboard, agent registry/reputation, LangGraph checkpoints) | Azure PostgreSQL Flexible Server |
| High-volume analytical state | Existing ClickHouse | Managed ClickHouse if later required |
| Object storage | MinIO / S3-compatible | Azure Blob Storage |
| Task queue | Procrastinate over PostgreSQL | Container Apps Jobs; Temporal later |
| Identity | Existing OIDC / Keycloak | Microsoft Entra ID |
| Observability | OpenTelemetry + Grafana stack | Azure Application Insights |
| Edge voice runtime | OpenVoiceOS | OHF Linux Voice Assistant reference path |
| Local STT | Faster Whisper via OVOS | Managed Deepgram/Azure adapter |
| Local TTS | Piper/phoonnx via OVOS | Managed provider adapter |
| Meeting transcription | WhisperX/Faster Whisper | Managed batch STT |
| Diarization | pyannote.audio | Managed diarization |
| Online meeting bot | Vexa adapter, deferred | Provider-specific integration |

## 5. Data capabilities confirmed through Seleric MCP

```text
Certified canonical P&L
All-channel sales/orders
Meta, Google and Amazon performance
Hourly Meta and Google delivery
First-party attribution
Campaign/ad set/ad dimensions
Product and SKU performance
Returns and cancellations
Session funnel and time-to-stage metrics
Device, geography and landing-page dimensions
Metric definitions, validation, freshness and provenance
```

Not currently confirmed and therefore built as V1 objects:

```text
Company goals and targets
Business owners and founder escalation
Ontology health
Derived state and regimes
Forecast/anomaly outputs
Intervention candidates
Founder ranking
Meetings and commitments
Verification state
Executable actions
Inventory/procurement readiness
```

## 6. Core configurable objects

```text
BusinessNodeType
BusinessNode
BusinessEdgeType
BusinessEdge
MetricBinding
GoalDefinition
OwnerBinding
EscalationPolicy
FeatureDefinition
DetectorPolicy
ForecastPolicy
HealthPolicy
GovernorPolicy
AgentDefinition
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

All runtime decisions pin the active configuration version.

## 7. Intelligence pipeline

```text
Certified metric series
-> feature definitions
-> optional baseline forecast
-> anomaly/change evidence
-> goal evaluation
-> metric state
-> node health                          [Business State Service - unchanged, deterministic]
-> Blackboard case opened (Observer)
-> precedent retrieval (pgvector)
-> task posted, agents bid, bid/coalition selected
-> agent debate: hypothesize, challenge, recruit, hand off (Governor-checked)
-> mandatory Skeptic pass
-> Coordinator convergence + confidence score
-> Governor clearance of any proposed action
-> top three limit (still enforced, now on Governor-cleared converged actions)
-> template response and Blackboard debate trace  [Insight Decision Service - changed 2026-08-31]
```

## 8. Default V1 analytical strategies

| Area | Default | Optional extension |
|---|---|---|
| Rolling state | SQL/Python rolling windows | Feature-store adapter later |
| Velocity/acceleration | Finite differences over configured windows | Smoothed derivatives |
| Volatility | Robust MAD/EWMA dispersion | GARCH or selected model |
| Forecast | Seasonal naive/EWMA then StatsForecast | XGBoost for selected metrics; deep models later |
| Anomaly | Prediction intervals, robust residual, change rule | PyOD multivariate detectors |
| Graph | PostgreSQL source + NetworkX runtime | Neo4j adapter later |
| Root driver | Agent (Diagnostic) hypothesis grounded in declared dependency + temporal/evidence scoring, Skeptic-challenged | DoWhy on validated causal subgraphs, consumed as agent evidence |
| Prioritization | Agent debate + Coordinator convergence under Governor control | Task-market bidding tie-broken by reputation (doc 06 §9.3a) |
| NLG | Jinja2 typed templates, rendering the swarm's finished output | Optional constrained language adapter for paraphrase only |
| Intent | HassIL/config grammar + classifier fallback; Speech-to-Phrase optional spike | Rasa later if domain expands |
| Meeting extraction | spaCy rules/dictionaries + review | Trainable NER after labelled corpus |

## 9. Founder-priority selection [changed 2026-08-31]

There is no longer a scoring formula. Selection is agent debate + Coordinator convergence, Governor-cleared, with a mandatory Skeptic pass before any item can be published (doc 06 §9.2a-9.5a).

Factors agents are still required to reason about and cite (same dimensions the old formula used, now judgment inputs rather than formula terms):

```text
severity
financial_exposure
urgency
evidence_confidence
data_confidence
founder_leverage
```

Considerations that used to be hard formula vetoes are now split between agent judgment (Skeptic-enforced) and Governor-enforced non-negotiables:

```text
stale or failed data              -> agent judgment (Skeptic-checked)
insufficient evidence             -> aggregate invariant: HypothesisWithoutEvidence blocks it structurally
below materiality threshold       -> agent judgment (Skeptic-checked)
no actionable intervention        -> agent judgment
owner already resolving within SLA -> agent judgment
founder not required              -> agent judgment
prerequisite unconfirmed          -> agent judgment (Skeptic-checked)
cooldown/suppression active       -> agent judgment
duplicate root cause              -> Coordinator consolidation (doc 07 §3.2)
production write / spend / PII / external comm -> Governor, non-negotiable regardless of confidence
```

## 10. Hardware spec

| Component | V1 specification |
|---|---|
| Compute | Raspberry Pi 5, 8 GB, active cooling |
| OS | 64-bit Debian/Raspberry Pi OS/raspOVOS-compatible Linux |
| Microphone | USB 4-microphone array with AEC/noise suppression; ReSpeaker XVF3800 preferred |
| Speaker | Powered USB or 3.5 mm speaker |
| Wake word | openWakeWord ONNX/TFLite, local |
| Controls | Physical mute and meeting stop button |
| Status | LED states for muted/listening/thinking/speaking/recording/error |
| Network | Ethernet preferred, Wi-Fi fallback |
| Local storage | High-quality 64-128 GB storage; encrypted meeting spool |

## 11. Service interface summary

```text
POST /v1/conversations
POST /v1/conversations/{id}/utterances
GET  /v1/executive/health
GET  /v1/executive/priorities
GET  /v1/executive/briefs/{brief_id}/interventions/{id}/explanation
GET  /v1/executive/risks
GET  /v1/executive/opportunities
POST /v1/meetings
POST /v1/meetings/{id}/audio-parts
POST /v1/meetings/{id}/stop
GET  /v1/meetings/{id}/review
POST /v1/meetings/{id}/approve
POST /v1/commitments/{id}/verify
POST /v1/config/drafts
POST /v1/config/drafts/{id}/validate
POST /v1/config/drafts/{id}/simulate
POST /v1/config/drafts/{id}/approve
POST /v1/config/drafts/{id}/publish
POST /v1/config/versions/{id}/rollback
GET  /v1/config/runtime-bundle
```

## 12. Security spec

```text
OIDC + MFA for human administrators
Device certificate or key-based enrollment
Short-lived tokens
No database credentials on the Pi
No standing MCP/DB/write credential for any agent or the LLM provider
Governor enforcement point: tool/spend/PII/write/spawn/iteration/approval gates
Governor fails closed on policy-fetch failure
Private service endpoints
Brand-scoped authorization
Read-only voice business path
Visible meeting-recording state
Encrypted transport and storage
Append-only audit, config, and Blackboard debate traces
Secrets in Key Vault/SOPS/Vault
```

## 13. Reliability spec

| Requirement | Target |
|---|---:|
| Wake acknowledgement | p95 < 250 ms |
| Barge-in stop | p95 < 300 ms |
| Precomputed answer first audio | p95 < 2.5 s |
| Bounded non-cached answer first audio | p95 < 6 s |
| Supported-intent accuracy | >= 95% golden set |
| Numeric statements with provenance | 100% |
| Interventions with a complete Blackboard debate trace | 100% |
| Founder priorities returned | 0-3 only |
| Founder priorities without a disclosed confidence score | 0 |
| Cases reaching a proposed action without a recorded Skeptic pass | 0 |
| Governor-gated actions executed without a recorded grant decision | 0 |
| Active commitments without approval | 0 |
| Extracted commitments without source evidence | 0 |
| Verification outcomes without evidence | 0 |

## 14. V1 acceptance outcome

The V1 is accepted when the founder can walk into the office, invoke the device, receive live company health, receive at most three auditable, confidence-scored priorities produced by the agent swarm under Governor control, ask for the evidence behind a priority and get back the debate that produced it, hear risks and opportunities without unsupported operational assumptions, start a real one-to-one meeting, review extracted commitments, later see at least one commitment verified or marked breached/unverifiable from real evidence, and see at least one Governor denial demonstrated end-to-end with a visible audit trail.
