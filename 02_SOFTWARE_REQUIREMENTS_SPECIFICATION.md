# Software Requirements Specification

## Project: Seleric Voice Node V1

**Target delivery:** September 30, 2026  
**System type:** Physical executive voice interface, business-state platform with agent-swarm business reasoning under a Governor safety boundary, meeting commitment verification system  
**Primary organization:** Tilting Heads  
**Document status:** Implementation baseline (rewritten 2026-08-31 — business reasoning is agent-swarm-driven, not deterministic; see §0)

## 0. What changed on 2026-08-31

This SRS previously specified metric selection, health calculation, ranking, and fact creation as deterministic computation with no LLM in the business-reasoning path. The founder has replaced that model: everything from metric/candidate selection through root-cause diagnosis and action proposal now runs through a swarm of LLM agents operating over the Seleric Blackboard, governed by a non-recruitable Seleric Governor. The Seleric MCP remains the sole certified-metrics source; Business State Service remains a fully deterministic evidence producer; Voice Orchestrator and Meeting Intelligence Service are untouched. Requirements below are marked **[SWARM]** where agent reasoning replaces a formerly deterministic computation, and the system guarantee in §6 is restated accordingly. Determinism/reproducibility of the *decision output* is no longer a system guarantee; evidence-grounding, confidence scoring, and full audit-trail completeness are the replacement guarantees.

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

- **Certified truth first:** Business values come from certified Seleric metric contracts. **Unchanged** — the swarm cannot query anything MCP does not expose, and every agent hypothesis must cite an evidence reference back to a certified query or a prior Blackboard artifact.
- **Observed versus inferred separation:** Raw observations, derived state, predictions and decisions are stored separately. **Unchanged.**
- **Evidence-grounded, not free-form, business reasoning [SWARM]:** the "no free-form business reasoning" rule is replaced by a narrower one — agent reasoning is bounded by typed tool ports, the declared ontology (agents must cite real node/edge IDs, not free text), and Governor-granted permissions. Agents may debate and hypothesize in natural language, but every conclusion that becomes a founder-facing fact must resolve to cited evidence, and every action proposal must pass Governor policy before execution.
- **Configurable, not arbitrary:** Admins select and parameterize pre-registered capabilities; admin configuration cannot execute arbitrary code. **Unchanged**, and now also covers `AgentDefinition` and Governor policy objects.
- **Version everything:** Configuration, features, models, policies, templates and extraction rules are versioned. **Unchanged**, and now also covers Governor policy and agent-registry capability declarations. The swarm's *reasoning path* is not versioned/reproducible the way a formula was — see the new guarantee below.
- **Human approval at side-effect boundaries:** Meeting commitments and operational actions require authorization. **Unchanged**, and the Governor is now the enforcement point for this rule inside the swarm.
- **Graceful uncertainty:** Missing targets, stale data or low confidence reduce eligibility rather than being filled by assumptions. **Unchanged** — now enforced by the Skeptic agent role in addition to eligibility rules.
- **Portable infrastructure:** Domain services use interfaces for storage, voice, models and identity. **Unchanged**, and now includes the LLM provider as a replaceable adapter.
- **Microservice discipline:** Split only when latency, scaling, security or lifecycle differs. **Unchanged** — see doc 03 §3.4/§14 for why the swarm did not earn a seventh service.

**Restated system guarantee (replaces the former "zero unsupported fact, fully deterministic" guarantee):**

> Every founder-facing conclusion is evidence-grounded (traceable to a certified MCP query or a prior Blackboard artifact) and carries an explicit confidence score. The reasoning path that produced it is not guaranteed reproducible on rerun. The complete agent debate that produced any conclusion — every message, hypothesis, challenge, and Governor decision — is permanently recorded on the Blackboard, and that record is the accountability mechanism in place of determinism.

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

## 8.8 Health and root-driver analysis [SWARM — replaces the former deterministic root-driver resolver]

| ID | Requirement |
|---|---|
| RCA-001 | The swarm shall separate observed anomaly, downstream impact and suspected root driver in every hypothesis it records. |
| RCA-002 | The Diagnostic agent's root-driver hypotheses shall cite graph ancestry, temporal precedence, anomaly concurrence and configured influence weight as evidence, the same evidence classes the former deterministic scorer used — the swarm reasons over this evidence rather than combining it in a fixed formula. |
| RCA-003 | The Coordinator shall consolidate multiple downstream symptoms proposed by different agents under one root-driver key before an action is proposed (doc 06 §9.4). |
| RCA-004 | Every hypothesis and every founder-facing explanation shall disclose whether the result is dependency-based (`DECLARED_DEPENDENCY`) or causally identified (`VALIDATED_CAUSAL`); agents are prohibited from asserting causal language the ontology does not support — this is a specific, checked responsibility of the Skeptic agent. |
| RCA-005 | A DoWhy strategy may be enabled only for graph revisions marked causal-approved and datasets passing validation, and its output is consumed by agents as evidence, not bypassed. |
| RCA-006 | Alternative hypotheses considered and rejected during debate shall be retained on the Blackboard and in the resulting decision/audit trace. |
| RCA-007 **[new]** | Every hypothesis shall carry an explicit numeric confidence score assigned by its proposing agent and, where challenged, an updated score reflecting the Skeptic's challenge outcome. |

## 8.9 Intervention generation and prioritization [SWARM — replaces deterministic eligibility/ranking]

| ID | Requirement |
|---|---|
| DEC-001 | Action proposals shall originate from agent reasoning grounded in the ontology and evidence on the Blackboard, not from unconstrained free text; a proposal must reference the case, hypothesis, and evidence that support it. |
| DEC-002 **[was: templates]** | An action proposal shall include action text, proposed owner, founder-required judgment, preconditions, expected-impact estimate (or explicit "impact unavailable"), and requested verification approach. |
| DEC-003 | Every action proposal is subject to Governor policy (tool/spend/write/PII checks) before it can be marked executable, in addition to the swarm's own debate-based scrutiny. |
| DEC-004 | Rejected or superseded candidate hypotheses/actions shall be stored with the debate messages that led to rejection, for audit — this replaces "rejection reasons" as a formula output with rejection reasons as a recorded agent conclusion. |
| DEC-005 | Prioritization among surviving candidates is reached by agent debate/consensus under Coordinator supervision (doc 06 §9), not a fixed weighted-score formula. |
| DEC-006 | Factors agents must consider and record when justifying priority (severity, financial exposure, urgency, evidence confidence, data confidence, founder leverage) are unchanged from the deterministic model's factor list — the swarm still has to reason about the same dimensions, it just does not combine them by fixed formula. |
| DEC-007 | Governor-enforced hard limits (spend, write scope, PII, external communication) execute before any action can be marked executable, and are non-negotiable regardless of swarm confidence. |
| DEC-008 **[changed from "deterministic ranking"]** | Candidate prioritization is not guaranteed reproducible for identical inputs; every prioritization decision carries a debate trace instead. |
| DEC-009 | Root-driver duplicates shall be consolidated (by the Coordinator, informed by the Skeptic) before selecting the top list. |
| DEC-010 | The founder brief shall contain zero to three converged, Governor-cleared interventions; the swarm shall never pad the list to reach three. |
| DEC-011 | Each selected intervention shall include expected impact range/estimate with confidence, or explicitly state that impact is unavailable. |
| DEC-012 | Recommendations dependent on unavailable inventory/procurement state shall contain an unmet precondition and shall not be stated as immediately executable — the Skeptic agent is specifically responsible for catching precondition gaps other agents' debate momentum might paper over. |
| DEC-013 **[new]** | No agent conclusion may execute a production write, spend budget, access PII, or communicate externally without a check against Governor policy passing first. |

## 8.10 Executive responses

| ID | Requirement |
|---|---|
| RESP-001 | Response text shall be produced from versioned Jinja2 templates and typed data objects — **unchanged**; the template renderer still receives a finished, validated typed DTO (`FounderBrief`, `Explanation`, etc.) from Insight Decision Service and does not itself reason or call the LLM. |
| RESP-002 | Templates shall support locale, tone, maximum duration and compact/expanded modes. |
| RESP-003 | "How are we doing?" shall summarize company status, strongest/weakest monitored areas, freshness, **and swarm confidence for any conclusion that is not a direct goal-attainment fact**. |
| RESP-004 | "What should I do today?" shall state only selected founder interventions, their confidence scores, and no unrelated dashboard metrics. |
| RESP-005 | "Why?" shall include observed change, baseline, suspected driver, impact, confidence, freshness, founder requirement, **and (on explicit follow-up) may summarize the agent debate that reached the conclusion**. |
| RESP-006 | "What are you worried about?" shall separate observed deterioration, forecast risk, commitment risk and data-quality risk. |
| RESP-007 | "What opportunity are we missing?" shall return eligible positive-variance candidates and operational preconditions. |
| RESP-008 | No template shall suppress material uncertainty, stale data, missing prerequisites, or a below-threshold swarm confidence score. |

## 8.10a Seleric Swarm Layer [new]

| ID | Requirement |
|---|---|
| SWARM-001 | The Blackboard shall record, for every case, an observation, evidence, urgency, hypotheses, active agents, open tasks, proposed actions, outcome and confidence (doc 05 §34, doc 14 §10a). |
| SWARM-002 | Every Blackboard write (message, hypothesis, bid, handoff, action proposal, Governor decision) shall be immutable and attributed to its originating agent and case. |
| SWARM-003 | The Coordinator shall have no permanent leader agent; control shall transfer based on which agent's declared capability matches the investigation's current need (doc 06 §9.2). |
| SWARM-004 | V1 shall launch with exactly seven agent roles: Observer, Anomaly, Diagnostic, Prediction, Strategy, Experiment, Skeptic. Adding or retiring a role is a Control-Plane-published `AgentDefinition` configuration change, not a code change, once the base agent-execution machinery is implemented. |
| SWARM-005 | The Skeptic agent shall be invoked in every case that reaches a proposed action, and its challenge/confidence-adjustment shall be recorded before an action can be marked Governor-eligible; a case shall not converge on its first plausible hypothesis without a recorded Skeptic pass. |
| SWARM-006 | The Agent Registry shall record each agent's capabilities, available tools, cost profile, and historical reliability, and it shall be readable by the Coordinator and by other agents for recruitment (doc 05 §36). The registry is internal-only in V1 — no external agent may query or register (see doc 01 §3a.10 for the A2A deferral). |
| SWARM-007 | Problems shall be postable to a task market; agents shall bid with confidence, estimated cost and expected information gain; the Coordinator shall select investigations using the documented selection rule (doc 06 §9.3). |
| SWARM-008 | Agents shall be able to hand off directly to another agent without returning to the Coordinator for every step, using LangGraph's handoff mechanism; every handoff shall still be recorded on the Blackboard. |
| SWARM-009 | Agent reasoning shall be grounded in the existing business ontology: a hypothesis or message that names a business concept shall reference a real node/edge ID, not free text only. |
| SWARM-010 | Closed cases shall be retrievable as precedent for new cases via similarity search (doc 06 §9.6); a new case shall surface similar prior cases to the recruited agents before they start independent investigation. |
| SWARM-011 | Agent reputation (accuracy, calibration, false-positive rate, cost, speed) shall be tracked per agent per problem class and updated when case outcomes are confirmed; the Coordinator/bidding selection shall use reputation as an input. |
| SWARM-012 | For a broad problem, the Coordinator may open multiple independent agent coalitions against the same case; their conclusions shall be reconciled (not silently averaged) before an action is proposed. |
| SWARM-013 | No swarm case shall be published as a founder-facing brief item without a recorded confidence score and a complete, retrievable debate trace. |

## 8.10b Seleric Governor [new]

| ID | Requirement |
|---|---|
| GOV-001 | The Governor shall enforce, for every agent action: tool permissions, financial spend limits, PII access rules, external-communication restrictions, production-write restrictions, API spend limits, agent-spawning limits, maximum iteration counts, and human-approval gates. |
| GOV-002 | The Governor shall not be recruitable by any agent and shall not have an `agent_id` in the Agent Registry. |
| GOV-003 | A Governor denial shall be terminal for that action in that turn; no automated retry-with-different-approach path shall bypass a denial. Only an explicit human-approved policy exception, applied through the existing Control Plane approval workflow, may permit the action. |
| GOV-004 | Governor policy shall be versioned Control Plane configuration using the same draft/validate/simulate/approve/publish/rollback lifecycle as every other configuration object. |
| GOV-005 | Every Governor decision (grant or deny) shall be recorded on the Blackboard and in the platform audit trail with the policy version evaluated. |
| GOV-006 | If Governor policy cannot be fetched or is stale beyond its validity window, the enforcement point shall fail closed: no tool call, spawn, spend, or write shall be permitted; read-only reasoning against already-fetched evidence may continue. |

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
| First audible response for precomputed query P95 | <= 2.5 s (reads the swarm's latest published case; no live agent debate runs synchronously in the voice path — doc 03 §5, §9) |
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
11a. **[new]** SwarmCase, Hypothesis, AgentMessage, SwarmTask/Bid, ProposedAction, Coalition (the Blackboard)
11b. **[new]** AgentDefinition, AgentRegistryEntry, AgentReputation
11c. **[new]** GovernorPolicy, ToolPermission, SpendLimit, ApprovalGate, AgentSpawnLimit
12. ResponseTemplate and Notification
13. Meeting, AudioPart, Participant and TranscriptSegment
14. ExtractedDecision, CommitmentDraft and Commitment
15. VerificationRule, VerificationAttempt and VerificationResult
16. ConfigurationRevision and AuditEvent

# 11. Traceability matrix for target interactions

| Interaction | Primary service | Required data/objects | Failure response |
|---|---|---|---|
| How are we doing? | Voice Orchestrator -> Insight Decision | Latest company health summary, freshness, strongest/weakest nodes | State unavailable/stale with exact scope |
| Three things today | Voice Orchestrator -> Insight Decision (reads latest published swarm case, does not trigger live debate) | Latest converged founder brief, goals, candidates, Blackboard debate trace, confidence scores | Zero-to-three valid items; never fabricate; disclose confidence |
| Why? | Voice Orchestrator -> Insight Decision | Current dialogue reference, Blackboard debate trace, evidence | Ask which item when reference is ambiguous |
| Worried about? | Insight Decision (swarm) | Negative state, forecast risk, breached commitments, data risks | Separate unavailable categories |
| Missing opportunity? | Insight Decision (swarm) | Positive variance candidates, preconditions, goal relevance | State operational prerequisites explicitly |
| Start meeting | Voice Orchestrator -> Meeting Service + Edge recorder | Device/session, participant context, recording policy | Refuse/announce failure if recording cannot be guaranteed |

# 12. V1 acceptance criteria

1. Physical node reliably detects "Hey Seleric" at normal table distance under agreed office noise.
2. The six interactions run against real TH data/configuration, not mocked business responses.
3. Each spoken business response is linked to an immutable response payload and a complete Blackboard debate trace (replaces "decision trace" as a formula-audit artifact with a debate-audit artifact — see doc 05 §34).
4. Founder priorities contain no more than three distinct root-driver interventions, each with a disclosed confidence score.
5. Stale/provisional data is clearly identified.
6. `WHY` references the prior selected intervention correctly in multi-turn tests, and can retrieve the debate that produced it.
7. Admin can create and publish a new metric binding, goal, `AgentDefinition`, Governor policy and response template without service code change.
8. One active ontology revision loads and validates successfully, and agent hypotheses referencing it resolve to real node/edge IDs.
9. At least one forecast profile passes backtesting and produces a monitored output feeding the swarm as evidence; other nodes can use deterministic baselines.
10. One 30-minute one-to-one meeting is captured, transcribed, diarized and reviewed (unchanged, deterministic).
11. At least one approved commitment is subsequently VERIFIED, BREACHED or UNVERIFIABLE through a registered rule (unchanged, deterministic).
12. Device/service/config/decision/meeting/**Governor** audit records are queryable.
13. An on-prem Docker deployment and an Azure deployment mapping are documented and reproducible.
14. **[new]** At least one swarm case demonstrates the full loop: Observer notices a candidate problem, at least two agents debate (one of which is the Skeptic), a hypothesis converges with a recorded confidence score, and a proposed action is either Governor-approved or Governor-denied with a recorded reason.
15. **[new]** At least one Governor denial is demonstrated end-to-end: an agent attempts an out-of-policy action (e.g., a write beyond its granted scope) and the Governor blocks it, with the denial visible in the audit trail.
16. **[new]** Case-retrieval precedent works: a second, similar case surfaces the first case's resolution to the recruited agents before independent investigation restarts from zero.
