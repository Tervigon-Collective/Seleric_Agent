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
| Reasoning policy | Deterministic domain logic; statistical ML only where backtested; no LLM required in the business decision path |
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
| Insight Decision Service | Root-driver hypotheses, candidate generation, eligibility, ranking, briefs | Candidates, briefs, traces | Yes |
| Meeting Intelligence Service | Transcription, diarization, extraction, review, commitment, verification | Meeting/commitment state | Yes |
| Control Plane Service | Configuration lifecycle, validation, simulation, publish/rollback | Config/audit | Yes |
| Admin UI | Configuration and review user experience | No domain tables | Yes |

## 4. Shared dependencies

| Dependency | Selected implementation | Alternative |
|---|---|---|
| Certified data access | Existing Seleric MCP and Cube | None for V1 |
| Operational/config DB | PostgreSQL | Azure PostgreSQL Flexible Server |
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
RootDriverPolicy
InterventionTemplate
PrerequisiteDefinition
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

All runtime decisions pin the active configuration version.

## 7. Intelligence pipeline

```text
Certified metric series
-> feature definitions
-> optional baseline forecast
-> anomaly/change evidence
-> goal evaluation
-> metric state
-> node health
-> suspected root drivers
-> intervention templates
-> hard eligibility rules
-> root-cause deduplication
-> deterministic ranking
-> top three limit
-> template response and decision trace
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
| Root driver | Declared dependency + temporal/evidence scoring | DoWhy on validated causal subgraphs |
| Ranking | Eligibility + configurable weighted score | TOPSIS strategy plugin |
| NLG | Jinja2 typed templates | Optional constrained language adapter |
| Intent | HassIL/config grammar + classifier fallback; Speech-to-Phrase optional spike | Rasa later if domain expands |
| Meeting extraction | spaCy rules/dictionaries + review | Trainable NER after labelled corpus |

## 9. Founder-priority score

The final score is applied only after hard eligibility checks.


default normalized factors:

```text
severity
financial_exposure
urgency
evidence_confidence
data_confidence
founder_leverage
```

Required hard vetoes:

```text
stale or failed data
insufficient evidence
below materiality threshold
no actionable intervention
owner already resolving within SLA
founder not required
prerequisite unconfirmed
cooldown/suppression active
duplicate root cause
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
Private service endpoints
Brand-scoped authorization
Read-only voice business path
Visible meeting-recording state
Encrypted transport and storage
Append-only audit and decision traces
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
| Interventions with decision trace | 100% |
| Founder priorities returned | 0-3 only |
| Active commitments without approval | 0 |
| Extracted commitments without source evidence | 0 |
| Verification outcomes without evidence | 0 |

## 14. V1 acceptance outcome

The V1 is accepted when the founder can walk into the office, invoke the device, receive live company health, receive at most three auditable priorities, ask for the evidence behind a priority, hear risks and opportunities without unsupported operational assumptions, start a real one-to-one meeting, review extracted commitments, and later see at least one commitment verified or marked breached/unverifiable from real evidence.
