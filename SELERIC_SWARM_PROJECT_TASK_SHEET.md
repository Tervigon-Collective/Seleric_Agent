# Seleric Intelligence Swarm — Project Task & Status Sheet

> **Purpose:** Master implementation tracker for the Seleric multi-agent swarm system.  
> **Recommended operating mode:** Update this file during every implementation session / sprint review.  
> **Current architectural direction:** In-process agents for v1, typed `seleric.swarm.v1` communication envelope, LangGraph orchestration, A2A-compatible transport abstraction, MCP-backed data providers later, fixture-backed providers for the first reference mission.

---

# 0. Project Snapshot

| Field | Value |
|---|---|
| Project | Seleric Intelligence Swarm |
| Current Phase | Phase 22 / Fixture reference mission (prototype passing) — MCP + real ML/causal next |
| Current Milestone | End-to-end reference mission |
| Primary Reference Flow | Performance → Funnel → Technical |
| Specialist Flow | Observer → Anomaly → Diagnostic → Prediction → Strategy → Skeptic |
| Agent Transport v1 | In-process |
| Future Transport | A2A over HTTP |
| Data Provider v1 | Deterministic Fixtures |
| Production Data Access | MCP |
| Orchestration | LangGraph |
| Mission State | PostgreSQL + LangGraph state |
| Cache / Locking | Redis |
| Event Layer | NATS JetStream — optional after core flow |
| Causal Engine | DoWhy / EconML |
| ML Registry | MLflow |
| Observability | LangSmith + OpenTelemetry |
| Production Write Actions | Disabled |
| Overall Project Status | 🟡 IN PROGRESS — lookup_v1 path live; two-axis swarm complete on fixtures (not yet wired into HTTP API); real MCP/DoWhy/ML pending |

---

# 1. Status Legend

Use only these statuses to keep reporting consistent.

| Status | Meaning |
|---|---|
| ⬜ NOT STARTED | Work has not begun |
| 🟦 IN PROGRESS | Actively being implemented |
| 🟨 REVIEW | Implementation complete, awaiting review/testing |
| 🟥 BLOCKED | Cannot proceed because of dependency / issue |
| 🟩 DONE | Completed and accepted |
| ⏸ DEFERRED | Intentionally postponed |
| ❌ CANCELLED | No longer required |

Priority:

| Priority | Meaning |
|---|---|
| P0 | Blocking / critical foundation |
| P1 | Required for MVP |
| P2 | Important after MVP |
| P3 | Optimization / future |

---

# 2. Current Architecture Decisions

These decisions should be treated as fixed unless an ADR changes them.

| ID | Decision | Status |
|---|---|---|
| DEC-001 | Agents remain in-process for v1 | 🟩 APPROVED |
| DEC-002 | All agent communication uses typed `seleric.swarm.v1` envelope | 🟩 APPROVED |
| DEC-003 | Agent logic must not depend on HTTP vs in-process transport | 🟩 APPROVED |
| DEC-004 | `lookup_v1` remains as L0/L1 fast path inside new orchestrator | 🟩 APPROVED |
| DEC-005 | Fixture-backed realistic providers used before MCP integration | 🟩 APPROVED |
| DEC-006 | Fixture artifacts must be marked `data_origin=FIXTURE`, `synthetic=true` | 🟩 APPROVED |
| DEC-007 | First deep vertical: Performance → Funnel → Technical | 🟩 APPROVED |
| DEC-008 | Specialists are shared across domains; no duplicated specialist stack per domain | 🟩 APPROVED |
| DEC-009 | Coordinator controls mission; domain lead owns business problem | 🟩 APPROVED |
| DEC-010 | `mission_lead` and `active_specialist` are separate state fields | 🟩 APPROVED |
| DEC-011 | Numeric / causal / predictive claims require provenance | 🟩 APPROVED |
| DEC-012 | Production business writes disabled until later phase | 🟩 APPROVED |

---

# 3. Milestone Summary

| Milestone | Description | Target Status |
|---|---|---|
| M0 | Repository + contracts + architecture foundation | 🟩 DONE |
| M1 | Coordinator + lookup fast path | 🟩 DONE |
| M2 | Blackboard + Evidence Ledger | 🟨 REVIEW — ledger tables + repo API done; blackboard is in-memory prototype |
| M3 | In-process typed agent transport | 🟩 DONE |
| M4 | Performance → Funnel → Technical domain path | 🟨 REVIEW — runs in swarm prototype; `technical_agent` is `enabled: false` in registry |
| M5 | Observer + Anomaly working with fixture data | 🟨 REVIEW — Observer live; Anomaly prototype-only (template detector) |
| M6 | Diagnostic + DoWhy integration | 🟦 IN PROGRESS — hypotheses + template causal engine; real DoWhy is a stub |
| M7 | Prediction + Strategy | 🟨 REVIEW — template forecaster/optimizer; no registered models |
| M8 | Skeptic + Claim Gate | 🟨 REVIEW — Skeptic prototype complete; claim gate live for numeric/comparison only |
| M9 | Full dynamic CAC reference mission passes | 🟨 REVIEW — passes on fixtures as PROTOTYPE OUTPUT; not on the live API path |
| M10 | MCP provider integration | 🟦 IN PROGRESS — gateway + fixture/remote servers; live providers pending |
| M11 | Expand remaining domain agents | 🟦 IN PROGRESS — Commerce/Finance partial; Inventory/Procurement stubs |
| M12 | Production hardening / observability | 🟦 IN PROGRESS — LangSmith tracing + budgets; OTel/SLOs/deploy pending |

---

# 4. PHASE 0 — Repository, Contracts & Core Foundations

## Goal

Create the base project structure and contracts that every future agent/service must follow.

### Tasks

| ID | Task | Priority | Status | Owner | Depends On |
|---|---|---:|---|---|---|
| FND-001 | Create monorepo structure | P0 | 🟩 DONE |  |  |
| FND-002 | Configure Python 3.12 environment | P0 | 🟦 IN PROGRESS |  | FND-001 |
| FND-003 | Add `uv` package management | P0 | 🟩 DONE |  | FND-002 |
| FND-004 | Configure FastAPI application shell | P0 | 🟩 DONE |  | FND-002 |
| FND-005 | Configure Pydantic v2 models | P0 | 🟩 DONE |  | FND-002 |
| FND-006 | Configure LangGraph | P0 | 🟩 DONE |  | FND-002 |
| FND-007 | Add PostgreSQL local environment | P0 | 🟦 IN PROGRESS |  | FND-001 |
| FND-008 | Add Redis local environment | P1 | 🟦 IN PROGRESS |  | FND-001 |
| FND-009 | Create Docker Compose local stack | P1 | 🟩 DONE |  | FND-007,FND-008 |
| FND-010 | Configure pytest | P0 | 🟩 DONE |  | FND-002 |
| FND-011 | Configure Ruff / linting | P1 | 🟩 DONE |  | FND-002 |
| FND-012 | Configure environment / secret handling | P0 | 🟩 DONE |  | FND-004 |
| FND-013 | Create structured logging format | P1 | 🟩 DONE |  | FND-004 |
| FND-014 | Define project-wide ID conventions | P0 | 🟩 DONE |  |  |
| FND-015 | Define versioning conventions for contracts | P0 | 🟦 IN PROGRESS |  |  |

### Required ID Types

- `M-*` → Mission
- `T-*` → Task
- `MSG-*` → Agent message
- `EV-*` → Evidence
- `AN-*` → Anomaly
- `HYP-*` → Hypothesis
- `CAUS-*` → Causal analysis
- `PRED-*` → Prediction
- `STRAT-*` → Strategy
- `SK-*` → Skeptic finding
- `CL-*` → Claim
- `HT-*` → Leadership transfer

### Phase 0 Acceptance Criteria

- [ ] Repository installs from scratch.
- [ ] API health endpoint runs.
- [ ] LangGraph graph compiles.
- [ ] PostgreSQL is reachable.
- [ ] Redis is reachable.
- [ ] Tests run successfully.
- [ ] IDs follow a common convention.
- [ ] No secrets are stored in repository.
- [ ] Architecture decisions exist as ADRs.

---

# 5. PHASE 1 — Core Artifact Contracts

## Goal

Define the typed objects that all agents communicate through.

| ID | Task | Priority | Status | Owner | Depends On |
|---|---|---:|---|---|---|
| CTR-001 | Create `MissionState` schema | P0 | 🟩 DONE |  | FND-005 |
| CTR-002 | Create `SwarmEnvelope` schema | P0 | 🟩 DONE |  | FND-005 |
| CTR-003 | Create `TaskArtifact` base model | P0 | 🟩 DONE |  | FND-005 |
| CTR-004 | Create `EvidenceArtifact` | P0 | 🟩 DONE |  | CTR-003 |
| CTR-005 | Create `AnomalyArtifact` | P0 | 🟩 DONE |  | CTR-003 |
| CTR-006 | Create `HypothesisArtifact` | P0 | 🟩 DONE |  | CTR-003 |
| CTR-007 | Create `CausalArtifact` | P0 | 🟩 DONE |  | CTR-003 |
| CTR-008 | Create `PredictionArtifact` | P0 | 🟩 DONE |  | CTR-003 |
| CTR-009 | Create `StrategyArtifact` | P0 | 🟩 DONE |  | CTR-003 |
| CTR-010 | Create `SkepticArtifact` | P0 | 🟩 DONE |  | CTR-003 |
| CTR-011 | Create `Claim` model | P0 | 🟩 DONE |  | CTR-003 |
| CTR-012 | Create `LeadershipTransfer` model | P0 | 🟩 DONE |  | CTR-002 |
| CTR-013 | Create JSON Schema exports | P1 | 🟩 DONE |  | CTR-001:CTR-012 |
| CTR-014 | Add schema version field to every artifact | P0 | 🟦 IN PROGRESS |  | CTR-003 |
| CTR-015 | Add provenance fields to material artifacts | P0 | 🟩 DONE |  | CTR-004 |

### `seleric.swarm.v1` Envelope Required Fields

- [ ] `protocol`
- [ ] `schema_version`
- [ ] `mission_id`
- [ ] `task_id`
- [ ] `message_id`
- [ ] `from_agent`
- [ ] `to_agent`
- [ ] `intent`
- [ ] `objective`
- [ ] `scope`
- [ ] `evidence_refs`
- [ ] `artifact_refs`
- [ ] `requested_capabilities`
- [ ] `expected_artifacts`
- [ ] `idempotency_key`
- [ ] `created_at`

### Supported Intent Types

- [ ] `TASK_REQUEST`
- [ ] `EVIDENCE_REQUEST`
- [ ] `ARTIFACT_RESPONSE`
- [ ] `CHALLENGE`
- [ ] `CLARIFICATION`
- [ ] `HANDOFF_PROPOSAL`
- [ ] `HANDOFF_ACCEPT`
- [ ] `HANDOFF_REJECT`
- [ ] `COMPLETION_CANDIDATE`

### Acceptance Criteria

- [ ] Every schema validates valid fixtures.
- [ ] Invalid artifacts fail validation.
- [ ] Schema versions are explicit.
- [ ] No agent-specific free-form transport objects are used.

---

# 6. PHASE 2 — Agent Registry & Capability System

## Goal

Agents are selected by capability rather than hardcoded names.

| ID | Task | Priority | Status | Owner | Depends On |
|---|---|---:|---|---|---|
| REG-001 | Define Agent Registry schema | P0 | 🟩 DONE |  | CTR-002 |
| REG-002 | Define capability taxonomy | P0 | 🟩 DONE |  | REG-001 |
| REG-003 | Register Coordinator | P0 | 🟩 DONE |  | REG-001 |
| REG-004 | Register Observer | P0 | 🟩 DONE |  | REG-001 |
| REG-005 | Register Anomaly | P0 | 🟩 DONE |  | REG-001 |
| REG-006 | Register Diagnostic | P0 | 🟩 DONE |  | REG-001 |
| REG-007 | Register Prediction | P0 | 🟩 DONE |  | REG-001 |
| REG-008 | Register Strategy | P0 | 🟩 DONE |  | REG-001 |
| REG-009 | Register Skeptic | P0 | 🟩 DONE |  | REG-001 |
| REG-010 | Register Performance Domain Agent | P0 | 🟩 DONE |  | REG-001 |
| REG-011 | Register Funnel Domain Agent | P0 | 🟩 DONE |  | REG-001 |
| REG-012 | Register Technical Domain Agent | P0 | 🟩 DONE |  | REG-001 |
| REG-013 | Add future Commerce entry | P2 | 🟩 DONE |  | REG-001 |
| REG-014 | Add future Finance entry | P2 | 🟩 DONE |  | REG-001 |
| REG-015 | Add future Inventory entry | P2 | 🟩 DONE |  | REG-001 |
| REG-016 | Add future Procurement entry | P2 | 🟩 DONE |  | REG-001 |
| REG-017 | Build capability resolver | P0 | 🟩 DONE |  | REG-002 |
| REG-018 | Build deterministic agent scoring | P1 | 🟩 DONE |  | REG-017 |
| REG-019 | Add agent availability status | P1 | 🟦 IN PROGRESS |  | REG-001 |
| REG-020 | Add Agent Card compatibility layer | P2 | 🟦 IN PROGRESS |  | REG-001 |

### Acceptance Criteria

- [ ] Coordinator can request `funnel_analysis`.
- [ ] Resolver identifies Funnel Agent.
- [ ] Resolver does not rely solely on LLM reasoning.
- [ ] Agent capabilities are inspectable.
- [ ] Future HTTP/A2A Agent Cards can map to same registry.

---

# 7. PHASE 3 — Agent Transport Layer

## Goal

Allow in-process communication now without coupling agent logic to transport.

| ID | Task | Priority | Status | Owner | Depends On |
|---|---|---:|---|---|---|
| TRN-001 | Define `AgentTransport` Protocol | P0 | 🟩 DONE |  | CTR-002 |
| TRN-002 | Implement `InProcessTransport` | P0 | 🟩 DONE |  | TRN-001 |
| TRN-003 | Create Agent Runtime registry | P0 | 🟩 DONE |  | REG-001,TRN-002 |
| TRN-004 | Add message validation before dispatch | P0 | 🟦 IN PROGRESS |  | TRN-002 |
| TRN-005 | Add idempotency handling | P1 | 🟦 IN PROGRESS |  | TRN-002 |
| TRN-006 | Add timeouts | P1 | 🟦 IN PROGRESS |  | TRN-002 |
| TRN-007 | Add retry policy | P1 | 🟦 IN PROGRESS |  | TRN-002 |
| TRN-008 | Add tracing metadata | P1 | 🟩 DONE |  | TRN-002 |
| TRN-009 | Create placeholder `A2AHttpTransport` interface | P2 | 🟦 IN PROGRESS |  | TRN-001 |
| TRN-010 | Ensure agent code has no transport-specific logic | P0 | 🟩 DONE |  | TRN-002 |

### Acceptance Criteria

- [ ] Performance Agent sends typed envelope to Funnel Agent.
- [ ] Funnel Agent responds with typed artifact.
- [ ] No HTTP knowledge exists inside agent business logic.
- [ ] Transport can later be replaced without changing agent implementations.

---

# 8. PHASE 4 — Blackboard, Evidence Ledger & Shared State

## Goal

Build the shared mission knowledge system.

| ID | Task | Priority | Status | Owner | Depends On |
|---|---|---:|---|---|---|
| BLK-001 | Create Mission persistence table | P0 | 🟩 DONE |  | FND-007,CTR-001 |
| BLK-002 | Create Task persistence table | P0 | 🟦 IN PROGRESS |  | BLK-001 |
| BLK-003 | Create Mission Event table | P0 | 🟩 DONE |  | BLK-001 |
| BLK-004 | Create Evidence Ledger table | P0 | 🟩 DONE |  | CTR-004 |
| BLK-005 | Create Artifact index | P0 | 🟦 IN PROGRESS |  | CTR-003 |
| BLK-006 | Create Hypothesis Registry | P0 | 🟦 IN PROGRESS |  | CTR-006 |
| BLK-007 | Create Leadership Transfer history | P0 | 🟩 DONE |  | CTR-012 |
| BLK-008 | Create Claim table | P0 | 🟩 DONE |  | CTR-011 |
| BLK-009 | Build Blackboard repository API | P0 | 🟨 REVIEW |  | BLK-001:BLK-008 |
| BLK-010 | Build artifact reference resolver | P0 | 🟨 REVIEW |  | BLK-009 |
| BLK-011 | Add append-only evidence policy | P0 | 🟦 IN PROGRESS |  | BLK-004 |
| BLK-012 | Add superseding/version behavior | P1 | ⬜ NOT STARTED |  | BLK-011 |
| BLK-013 | Add audit event recording | P0 | 🟩 DONE |  | BLK-003 |
| BLK-014 | Add mission replay support | P2 | 🟦 IN PROGRESS |  | BLK-003 |

### Acceptance Criteria

- [ ] Agents pass artifact IDs instead of full raw histories.
- [ ] Evidence can be retrieved by ID.
- [ ] Mission state survives process restart.
- [ ] Leadership history is auditable.
- [ ] Claims can be traced to evidence.

---

# 9. PHASE 5 — Provider Abstraction & Fixture Data

## Goal

Create realistic fixture-backed providers that can later be replaced by MCP providers.

| ID | Task | Priority | Status | Owner | Depends On |
|---|---|---:|---|---|---|
| PRV-001 | Define `PerformanceDataProvider` Protocol | P0 | 🟩 DONE |  | FND-005 |
| PRV-002 | Define `FunnelDataProvider` Protocol | P0 | 🟩 DONE |  | FND-005 |
| PRV-003 | Define `TechnicalDataProvider` Protocol | P0 | 🟩 DONE |  | FND-005 |
| PRV-004 | Implement Fixture Performance Provider | P0 | 🟩 DONE |  | PRV-001 |
| PRV-005 | Implement Fixture Funnel Provider | P0 | 🟩 DONE |  | PRV-002 |
| PRV-006 | Implement Fixture Technical Provider | P0 | 🟩 DONE |  | PRV-003 |
| PRV-007 | Mark all fixture artifacts as synthetic | P0 | 🟩 DONE |  | PRV-004:PRV-006 |
| PRV-008 | Create deterministic CAC incident fixture | P0 | 🟩 DONE |  | PRV-004:PRV-006 |
| PRV-009 | Create baseline / no-anomaly fixture | P1 | 🟦 IN PROGRESS |  | PRV-004 |
| PRV-010 | Create contradictory-data fixture | P1 | ⬜ NOT STARTED |  | PRV-004:PRV-006 |
| PRV-011 | Create missing-data fixture | P1 | 🟦 IN PROGRESS |  | PRV-004 |
| PRV-012 | Create MCP provider interfaces | P1 | 🟩 DONE |  | PRV-001:PRV-003 |

### CAC Reference Fixture

The deterministic demo incident should contain approximately:

| Metric | Baseline | Incident |
|---|---:|---:|
| CAC | ₹604 | ₹782 |
| Spend | baseline | +4% |
| CPM | baseline | +1.2% |
| CTR | baseline | -1.1% |
| CPC | baseline | +2.4% |
| Sessions | baseline | +3% |
| Mobile Purchase CVR | baseline | -31% |
| Mobile LCP | 2.2 sec | 5.8 sec |
| JS Error Rate | 0.7% | 6.1% |
| Deployment | None | Sep 1, 11:40 AM |

### Acceptance Criteria

- [ ] Fixture produces same output on repeated runs.
- [ ] Fixture data never appears as real production data.
- [ ] Switching provider implementation does not change domain-agent interface.

---

# 10. PHASE 6 — Metric & Semantic Layer

## Goal

Stop agents from inventing metric definitions.

| ID | Task | Priority | Status | Owner | Depends On |
|---|---|---:|---|---|---|
| MET-001 | Define Metric Registry schema | P0 | 🟩 DONE |  | FND-005 |
| MET-002 | Register CAC | P0 | 🟩 DONE |  | MET-001 |
| MET-003 | Register Spend | P0 | ⬜ NOT STARTED |  | MET-001 |
| MET-004 | Register CPM | P0 | ⬜ NOT STARTED |  | MET-001 |
| MET-005 | Register CTR | P0 | ⬜ NOT STARTED |  | MET-001 |
| MET-006 | Register CPC | P0 | ⬜ NOT STARTED |  | MET-001 |
| MET-007 | Register Sessions | P0 | ⬜ NOT STARTED |  | MET-001 |
| MET-008 | Register PDP View Rate | P1 | ⬜ NOT STARTED |  | MET-001 |
| MET-009 | Register ATC Rate | P0 | 🟩 DONE |  | MET-001 |
| MET-010 | Register Checkout Rate | P0 | ⬜ NOT STARTED |  | MET-001 |
| MET-011 | Register Purchase CVR | P0 | ⬜ NOT STARTED |  | MET-001 |
| MET-012 | Create Metric Resolver | P0 | 🟦 IN PROGRESS |  | MET-001 |
| MET-013 | Add timezone/grain validation | P0 | 🟦 IN PROGRESS |  | MET-012 |
| MET-014 | Add metric version to Evidence Artifact | P0 | 🟦 IN PROGRESS |  | CTR-004,MET-001 |

### Acceptance Criteria

- [ ] CAC has one explicit selected definition per mission.
- [ ] Comparison periods use compatible grain/timezone.
- [ ] Evidence records metric version.
- [ ] Agents reference metric IDs rather than ad hoc formulas.

---

# 11. PHASE 7 — Coordinator Core

## Goal

Implement the Coordinator as mission control, not as an omniscient analytical agent.

| ID | Task | Priority | Status | Owner | Depends On |
|---|---|---:|---|---|---|
| COR-001 | Implement Request Intake | P0 | 🟩 DONE |  | CTR-001 |
| COR-002 | Implement Query Normalizer | P0 | 🟩 DONE |  | COR-001 |
| COR-003 | Implement Intent Classifier | P0 | 🟩 DONE |  | COR-002 |
| COR-004 | Implement Entity Resolver | P1 | 🟦 IN PROGRESS |  | COR-002 |
| COR-005 | Implement Time Range Resolver | P0 | 🟩 DONE |  | COR-002 |
| COR-006 | Integrate Metric Resolver | P0 | 🟦 IN PROGRESS |  | MET-012 |
| COR-007 | Implement Complexity Classifier L0–L5 | P0 | 🟩 DONE |  | COR-003 |
| COR-008 | Implement Mission Decomposer | P0 | 🟩 DONE |  | COR-007 |
| COR-009 | Implement Task DAG Builder | P0 | 🟩 DONE |  | COR-008 |
| COR-010 | Implement Capability Resolver integration | P0 | 🟩 DONE |  | REG-017 |
| COR-011 | Implement Initial Lead Selector | P0 | 🟩 DONE |  | REG-018 |
| COR-012 | Implement ready-task scheduler | P0 | 🟦 IN PROGRESS |  | COR-009 |
| COR-013 | Implement parallel task scheduling | P1 | ⬜ NOT STARTED |  | COR-012 |
| COR-014 | Implement artifact ingestion | P0 | 🟦 IN PROGRESS |  | BLK-009 |
| COR-015 | Implement Evidence Gap Detector | P1 | 🟩 DONE |  | COR-014 |
| COR-016 | Implement Conflict Resolver | P1 | 🟦 IN PROGRESS |  | COR-014 |
| COR-017 | Implement mission limits | P0 | 🟩 DONE |  | COR-012 |
| COR-018 | Implement Completion Evaluator | P0 | 🟩 DONE |  | COR-014 |
| COR-019 | Implement Response Synthesizer | P0 | 🟩 DONE |  | COR-018 |
| COR-020 | Add mission audit trace | P0 | 🟩 DONE |  | BLK-013 |

### Coordinator Must NOT

- [ ] Directly query Meta.
- [ ] Directly query Shopify/PostHog.
- [ ] Calculate anomaly scores.
- [ ] Run DoWhy itself.
- [ ] Generate unsupported forecast numbers.
- [ ] Override Skeptic rejection.
- [ ] Execute business write actions.

---

# 12. PHASE 8 — Preserve `lookup_v1` as Fast Path

## Goal

Keep simple queries cheap and deterministic.

| ID | Task | Priority | Status | Owner | Depends On |
|---|---|---:|---|---|---|
| LKP-001 | Audit existing `lookup_v1` behavior | P0 | 🟩 DONE |  |  |
| LKP-002 | Identify reusable lookup components | P0 | 🟩 DONE |  | LKP-001 |
| LKP-003 | Wrap lookup flow as Coordinator route | P0 | 🟩 DONE |  | COR-007 |
| LKP-004 | Route L0 queries to lookup | P0 | 🟩 DONE |  | LKP-003 |
| LKP-005 | Route L1 queries to domain + Observer | P0 | 🟩 DONE |  | LKP-003 |
| LKP-006 | Route L2+ queries to dynamic mission | P0 | 🟦 IN PROGRESS |  | COR-009 |
| LKP-007 | Add regression tests for existing lookup use cases | P0 | 🟩 DONE |  | LKP-003 |
| LKP-008 | Remove duplicate legacy orchestration | P1 | 🟦 IN PROGRESS |  | LKP-007 |

### Acceptance Criteria

- [ ] “Sales yesterday?” does not activate whole swarm.
- [ ] Existing lookup use cases continue to work.
- [ ] Lookup logic shares metric/evidence contracts with new architecture.

---

# 13. PHASE 9 — Domain Agent Base Framework

## Goal

Define reusable architecture for all business-domain agents.

| ID | Task | Priority | Status | Owner | Depends On |
|---|---|---:|---|---|---|
| DOM-001 | Create `DomainAgent` base interface | P0 | 🟨 REVIEW |  | CTR-002 |
| DOM-002 | Add Domain Context Builder | P0 | 🟦 IN PROGRESS |  | DOM-001 |
| DOM-003 | Add domain capability router | P0 | 🟨 REVIEW |  | DOM-001 |
| DOM-004 | Add evidence request helper | P0 | 🟨 REVIEW |  | TRN-002 |
| DOM-005 | Add specialist request helper | P0 | 🟨 REVIEW |  | TRN-002 |
| DOM-006 | Add cross-domain request helper | P0 | 🟨 REVIEW |  | TRN-002 |
| DOM-007 | Add handoff proposal helper | P0 | 🟨 REVIEW |  | CTR-012 |
| DOM-008 | Add Domain Artifact builder | P0 | 🟨 REVIEW |  | CTR-003 |
| DOM-009 | Add domain completion candidate behavior | P1 | 🟦 IN PROGRESS |  | DOM-001 |
| DOM-010 | Add common domain tests | P0 | 🟨 REVIEW |  | DOM-001 |

---

# 14. PHASE 10 — Performance Domain Agent

| ID | Task | Priority | Status | Owner | Depends On |
|---|---|---:|---|---|---|
| PERF-001 | Define Performance ontology | P0 | 🟨 REVIEW |  | DOM-001 |
| PERF-002 | Define performance capabilities | P0 | 🟩 DONE |  | REG-010 |
| PERF-003 | Integrate PerformanceDataProvider | P0 | 🟨 REVIEW |  | PRV-001 |
| PERF-004 | Implement CAC decomposition | P0 | 🟦 IN PROGRESS |  | MET-002 |
| PERF-005 | Implement media-metric decomposition | P0 | 🟦 IN PROGRESS |  | MET-003:MET-006 |
| PERF-006 | Add channel → campaign → adset → ad hierarchy | P1 | 🟦 IN PROGRESS |  | PERF-001 |
| PERF-007 | Build evidence bundle output | P0 | 🟨 REVIEW |  | CTR-004 |
| PERF-008 | Add Funnel handoff criteria | P0 | 🟨 REVIEW |  | DOM-007 |
| PERF-009 | Add Commerce handoff criteria | P2 | 🟦 IN PROGRESS |  | DOM-007 |
| PERF-010 | Add Finance handoff criteria | P2 | 🟦 IN PROGRESS |  | DOM-007 |
| PERF-011 | Test CAC incident fixture | P0 | 🟨 REVIEW |  | PRV-008 |

### Performance → Funnel Handoff Trigger

Example:

- [ ] CPM stable.
- [ ] CTR stable.
- [ ] CPC stable.
- [ ] Paid traffic volume stable.
- [ ] Purchase CVR materially down.
- [ ] Evidence references attached.
- [ ] Unresolved question explicitly states downstream funnel problem.

---

# 15. PHASE 11 — Funnel Domain Agent

| ID | Task | Priority | Status | Owner | Depends On |
|---|---|---:|---|---|---|
| FUN-001 | Define Funnel ontology | P0 | 🟨 REVIEW |  | DOM-001 |
| FUN-002 | Define funnel capabilities | P0 | 🟩 DONE |  | REG-011 |
| FUN-003 | Integrate FunnelDataProvider | P0 | 🟨 REVIEW |  | PRV-002 |
| FUN-004 | Implement session → PDP → ATC → checkout → purchase funnel | P0 | 🟦 IN PROGRESS |  | MET-007:MET-011 |
| FUN-005 | Implement stage-loss comparison | P0 | 🟨 REVIEW |  | FUN-004 |
| FUN-006 | Implement device segmentation | P0 | 🟨 REVIEW |  | FUN-004 |
| FUN-007 | Implement browser segmentation | P1 | 🟦 IN PROGRESS |  | FUN-004 |
| FUN-008 | Implement landing/PDP segmentation | P1 | 🟦 IN PROGRESS |  | FUN-004 |
| FUN-009 | Add Technical handoff criteria | P0 | 🟨 REVIEW |  | DOM-007 |
| FUN-010 | Add Commerce handoff criteria | P2 | 🟦 IN PROGRESS |  | DOM-007 |
| FUN-011 | Test mobile-CVR incident | P0 | 🟨 REVIEW |  | PRV-008 |

---

# 16. PHASE 12 — Technical Domain Agent

| ID | Task | Priority | Status | Owner | Depends On |
|---|---|---:|---|---|---|
| TEC-001 | Define Technical ontology | P0 | 🟨 REVIEW |  | DOM-001 |
| TEC-002 | Define technical capabilities | P0 | 🟦 IN PROGRESS |  | REG-012 |
| TEC-003 | Integrate TechnicalDataProvider | P0 | 🟨 REVIEW |  | PRV-003 |
| TEC-004 | Implement deployment timeline analysis | P0 | 🟨 REVIEW |  | TEC-003 |
| TEC-005 | Implement LCP comparison | P0 | 🟨 REVIEW |  | TEC-003 |
| TEC-006 | Implement JS error comparison | P0 | 🟨 REVIEW |  | TEC-003 |
| TEC-007 | Implement incident correlation view | P1 | 🟦 IN PROGRESS |  | TEC-004:TEC-006 |
| TEC-008 | Add Funnel return-handoff criteria | P1 | 🟨 REVIEW |  | DOM-007 |
| TEC-009 | Test deployment-linked degradation fixture | P0 | 🟨 REVIEW |  | PRV-008 |

---

# 17. PHASE 13 — Specialist Agent Base Framework

## Goal

Specialists are analytical capabilities, not domain owners.

| ID | Task | Priority | Status | Owner | Depends On |
|---|---|---:|---|---|---|
| SPC-001 | Create `SpecialistAgent` base interface | P0 | 🟨 REVIEW |  | CTR-002 |
| SPC-002 | Add specialist Context Builder | P0 | 🟦 IN PROGRESS |  | SPC-001 |
| SPC-003 | Add artifact loading by reference | P0 | 🟨 REVIEW |  | BLK-010 |
| SPC-004 | Add service/model router interface | P0 | 🟨 REVIEW |  | SPC-001 |
| SPC-005 | Add typed artifact output | P0 | 🟨 REVIEW |  | CTR-003 |
| SPC-006 | Prevent uncontrolled direct MCP access | P0 | 🟨 REVIEW |  | SPC-001 |
| SPC-007 | Add common specialist tests | P0 | 🟨 REVIEW |  | SPC-001 |

---

# 18. PHASE 14 — Observer Agent

| ID | Task | Priority | Status | Owner | Depends On |
|---|---|---:|---|---|---|
| OBS-001 | Implement Observation Planner | P0 | 🟩 DONE |  | SPC-001 |
| OBS-002 | Integrate Metric Resolver | P0 | 🟦 IN PROGRESS |  | MET-012 |
| OBS-003 | Integrate Entity/Time resolver | P0 | 🟦 IN PROGRESS |  | COR-004,COR-005 |
| OBS-004 | Implement provider capability routing | P0 | 🟩 DONE |  | PRV-001:PRV-003 |
| OBS-005 | Add raw result normalization | P0 | 🟩 DONE |  | OBS-004 |
| OBS-006 | Add data-quality checks | P0 | 🟦 IN PROGRESS |  | OBS-005 |
| OBS-007 | Build EvidenceBundle | P0 | 🟩 DONE |  | CTR-004 |
| OBS-008 | Persist evidence to ledger | P0 | 🟩 DONE |  | BLK-004 |
| OBS-009 | Reject missing metric definitions | P0 | 🟩 DONE |  | MET-001 |
| OBS-010 | Test fixture provenance labeling | P0 | 🟩 DONE |  | PRV-007 |

---

# 19. PHASE 15 — Anomaly Agent

| ID | Task | Priority | Status | Owner | Depends On |
|---|---|---:|---|---|---|
| ANO-001 | Define anomaly detector interface | P0 | 🟨 REVIEW |  | SPC-004 |
| ANO-002 | Implement robust Z/MAD baseline | P0 | 🟦 IN PROGRESS |  | ANO-001 |
| ANO-003 | Implement STL residual detector | P1 | ⬜ NOT STARTED |  | ANO-001 |
| ANO-004 | Add change-point detector | P1 | ⬜ NOT STARTED |  | ANO-001 |
| ANO-005 | Create detector router | P0 | 🟦 IN PROGRESS |  | ANO-002 |
| ANO-006 | Add history sufficiency checks | P0 | 🟦 IN PROGRESS |  | ANO-005 |
| ANO-007 | Implement segment drilldown | P1 | 🟦 IN PROGRESS |  | ANO-005 |
| ANO-008 | Build AnomalyArtifact | P0 | 🟨 REVIEW |  | CTR-005 |
| ANO-009 | Persist anomaly artifact | P0 | 🟨 REVIEW |  | BLK-005 |
| ANO-010 | Validate mobile CVR anomaly fixture | P0 | 🟨 REVIEW |  | PRV-008 |

### Acceptance Criteria

- [ ] LLM does not invent anomaly score.
- [ ] Detector metadata stored.
- [ ] Expected range stored.
- [ ] Insufficient history is explicitly handled.

---

# 20. PHASE 16 — Diagnostic Agent & DoWhy

| ID | Task | Priority | Status | Owner | Depends On |
|---|---|---:|---|---|---|
| DIA-001 | Build Hypothesis Generator | P0 | 🟨 REVIEW |  | SPC-001 |
| DIA-002 | Build Hypothesis Ranker | P0 | 🟦 IN PROGRESS |  | DIA-001 |
| DIA-003 | Build Evidence Requirement Generator | P0 | 🟦 IN PROGRESS |  | DIA-001 |
| DIA-004 | Implement Evidence Request flow | P0 | 🟨 REVIEW |  | TRN-002 |
| DIA-005 | Add statistical association tests | P0 | 🟦 IN PROGRESS |  | DIA-003 |
| DIA-006 | Define Causal Graph Registry | P0 | 🟦 IN PROGRESS |  |  |
| DIA-007 | Add funnel/technical causal graph | P0 | 🟦 IN PROGRESS |  | DIA-006 |
| DIA-008 | Build causal question object | P0 | 🟨 REVIEW |  | DIA-006 |
| DIA-009 | Integrate DoWhy | P0 | 🟦 IN PROGRESS |  | DIA-008 |
| DIA-010 | Add estimator policy | P1 | ⬜ NOT STARTED |  | DIA-009 |
| DIA-011 | Add refutation tests | P0 | 🟦 IN PROGRESS |  | DIA-009 |
| DIA-012 | Track supporting/contradictory evidence | P0 | 🟨 REVIEW |  | CTR-006 |
| DIA-013 | Distinguish association vs causal support | P0 | 🟦 IN PROGRESS |  | DIA-009 |
| DIA-014 | Build CausalArtifact | P0 | 🟨 REVIEW |  | CTR-007 |
| DIA-015 | Test frontend-regression hypothesis | P0 | 🟨 REVIEW |  | PRV-008 |

### Diagnostic Hypotheses for Reference Mission

- [ ] H1 — Mobile latency increased.
- [ ] H2 — Traffic quality deteriorated.
- [ ] H3 — Discount/pricing changed.
- [ ] H4 — Stock availability changed.
- [ ] H5 — Payment failure increased.
- [ ] H6 — Tracking/attribution broke.

### Acceptance Criteria

- [ ] Hypotheses start as `PROPOSED`.
- [ ] Hypotheses can be `REJECTED`, `RETAINED`, or `INSUFFICIENT`.
- [ ] DoWhy never runs without explicit treatment/outcome/graph.
- [ ] Failed refutation prevents strong causal claim.

---

# 21. PHASE 17 — Prediction Agent

| ID | Task | Priority | Status | Owner | Depends On |
|---|---|---:|---|---|---|
| PRE-001 | Define Model Registry schema | P0 | 🟦 IN PROGRESS |  |  |
| PRE-002 | Define prediction model interface | P0 | 🟨 REVIEW |  | SPC-004 |
| PRE-003 | Create fixture forecast model | P0 | 🟨 REVIEW |  | PRE-002 |
| PRE-004 | Implement Model Router | P0 | 🟦 IN PROGRESS |  | PRE-001 |
| PRE-005 | Add model applicability checks | P0 | 🟦 IN PROGRESS |  | PRE-004 |
| PRE-006 | Add model version metadata | P0 | 🟨 REVIEW |  | PRE-001 |
| PRE-007 | Add uncertainty interval | P0 | 🟨 REVIEW |  | PRE-003 |
| PRE-008 | Add drift status field | P0 | 🟨 REVIEW |  | PRE-001 |
| PRE-009 | Implement fallback hierarchy | P0 | 🟦 IN PROGRESS |  | PRE-004 |
| PRE-010 | Build PredictionArtifact | P0 | 🟨 REVIEW |  | CTR-008 |
| PRE-011 | Validate 7-day CAC/order-impact fixture | P0 | 🟨 REVIEW |  | PRV-008 |

### Required Fallback Order

1. Registered production model.
2. Approved statistical baseline.
3. `INSUFFICIENT_EVIDENCE`.

Never:

- [ ] LLM-generated numerical forecast as hidden fallback.

---

# 22. PHASE 18 — Strategy Agent

| ID | Task | Priority | Status | Owner | Depends On |
|---|---|---:|---|---|---|
| STR-001 | Define Intervention model | P0 | 🟨 REVIEW |  | CTR-009 |
| STR-002 | Build Business Objective Resolver | P0 | 🟦 IN PROGRESS |  | SPC-001 |
| STR-003 | Build Constraint Resolver | P0 | 🟦 IN PROGRESS |  | STR-002 |
| STR-004 | Build Intervention Generator | P0 | 🟨 REVIEW |  | STR-002 |
| STR-005 | Require validated mechanism reference | P0 | 🟨 REVIEW |  | DIA-014 |
| STR-006 | Add risk score | P1 | 🟦 IN PROGRESS |  | STR-001 |
| STR-007 | Add reversibility score | P1 | 🟦 IN PROGRESS |  | STR-001 |
| STR-008 | Add expected impact field | P0 | 🟨 REVIEW |  | PRE-010 |
| STR-009 | Add scenario comparison | P1 | 🟦 IN PROGRESS |  | STR-004 |
| STR-010 | Build StrategyArtifact | P0 | 🟨 REVIEW |  | CTR-009 |
| STR-011 | Test rollback/hotfix recommendation | P0 | 🟨 REVIEW |  | PRV-008 |

### Reference Mission Expected Ranking

1. Roll back problematic deployment.
2. Fix JS/mobile regression.
3. Validate LCP recovery.
4. Do not materially alter paid-media strategy until funnel is restored.

---

# 23. PHASE 19 — Skeptic Agent

| ID | Task | Priority | Status | Owner | Depends On |
|---|---|---:|---|---|---|
| SKP-001 | Build Claim Extractor | P0 | 🟦 IN PROGRESS |  | CTR-011 |
| SKP-002 | Build Alternative Explanation Generator | P0 | 🟨 REVIEW |  | SKP-001 |
| SKP-003 | Build Contradiction Search | P0 | 🟨 REVIEW |  | BLK-010 |
| SKP-004 | Add Data Quality Attack | P0 | 🟨 REVIEW |  | SKP-001 |
| SKP-005 | Add Methodology Attack | P0 | 🟨 REVIEW |  | SKP-001 |
| SKP-006 | Add Causal Assumption Attack | P0 | 🟨 REVIEW |  | DIA-014 |
| SKP-007 | Add Model Reliability Attack | P0 | 🟨 REVIEW |  | PRE-010 |
| SKP-008 | Add Strategy Logic Attack | P0 | 🟨 REVIEW |  | STR-010 |
| SKP-009 | Implement PASS verdict | P0 | 🟨 REVIEW |  | SKP-001 |
| SKP-010 | Implement REVISE verdict | P0 | 🟨 REVIEW |  | SKP-001 |
| SKP-011 | Implement REJECT verdict | P0 | 🟨 REVIEW |  | SKP-001 |
| SKP-012 | Create remediation task generation | P0 | 🟨 REVIEW |  | SKP-010 |
| SKP-013 | Build SkepticArtifact | P0 | 🟨 REVIEW |  | CTR-010 |
| SKP-014 | Test alternative-cause challenge | P0 | 🟨 REVIEW |  | PRV-008 |

### Skeptic Reference Checks

- [ ] Did price change?
- [ ] Did stock change?
- [ ] Did campaign mix change?
- [ ] Did attribution/tracking change?
- [ ] Did payment failures change?
- [ ] Is timing direction correct?
- [ ] Is the causal graph missing a confounder?
- [ ] Is the forecast model applicable?
- [ ] Does recommendation directly address cause?

---

# 24. PHASE 20 — Dynamic Leadership Manager

| ID | Task | Priority | Status | Owner | Depends On |
|---|---|---:|---|---|---|
| LEAD-001 | Define leadership state | P0 | 🟩 DONE |  | CTR-001 |
| LEAD-002 | Implement initial lead selection | P0 | 🟩 DONE |  | COR-011 |
| LEAD-003 | Implement handoff proposal validation | P0 | 🟩 DONE |  | CTR-012 |
| LEAD-004 | Require evidence for transfer | P0 | 🟩 DONE |  | LEAD-003 |
| LEAD-005 | Implement capability comparison | P0 | 🟦 IN PROGRESS |  | REG-018 |
| LEAD-006 | Implement leadership epoch | P0 | 🟩 DONE |  | LEAD-001 |
| LEAD-007 | Implement ping-pong detection | P0 | 🟩 DONE |  | BLK-007 |
| LEAD-008 | Add handoff hysteresis | P1 | 🟦 IN PROGRESS |  | LEAD-005 |
| LEAD-009 | Add coordinator arbitration | P0 | 🟩 DONE |  | LEAD-007 |
| LEAD-010 | Persist transfer history | P0 | 🟩 DONE |  | BLK-007 |
| LEAD-011 | Test Performance → Funnel | P0 | 🟩 DONE |  | PERF-008,FUN-001 |
| LEAD-012 | Test Funnel → Technical | P0 | 🟨 REVIEW |  | FUN-009,TEC-001 |

### Leadership Transfer Must Include

- [ ] `from_agent`
- [ ] `to_agent`
- [ ] `reason`
- [ ] `evidence_refs`
- [ ] `unresolved_question`
- [ ] `requested_capability`
- [ ] `leadership_epoch`

---

# 25. PHASE 21 — Claim Gate & Response Validation

| ID | Task | Priority | Status | Owner | Depends On |
|---|---|---:|---|---|---|
| CLM-001 | Define claim classes | P0 | 🟩 DONE |  | CTR-011 |
| CLM-002 | Numeric claim policy | P0 | 🟩 DONE |  | CLM-001 |
| CLM-003 | Comparison claim policy | P0 | 🟩 DONE |  | CLM-001 |
| CLM-004 | Anomaly claim policy | P0 | 🟦 IN PROGRESS |  | ANO-008 |
| CLM-005 | Causal claim policy | P0 | 🟦 IN PROGRESS |  | DIA-014 |
| CLM-006 | Forecast claim policy | P0 | 🟦 IN PROGRESS |  | PRE-010 |
| CLM-007 | Strategy claim policy | P0 | 🟦 IN PROGRESS |  | STR-010 |
| CLM-008 | Require Skeptic pass for strong causal conclusions | P0 | 🟦 IN PROGRESS |  | SKP-013 |
| CLM-009 | Implement unsupported-claim rejection | P0 | 🟩 DONE |  | CLM-002:CLM-008 |
| CLM-010 | Implement trust labels | P1 | 🟦 IN PROGRESS |  | CLM-009 |
| CLM-011 | Integrate gate into Coordinator completion | P0 | 🟩 DONE |  | COR-018 |

### Trust Labels

- `VERIFIED`
- `STRONG`
- `PROBABLE`
- `WEAK`
- `INSUFFICIENT`

Do not use arbitrary LLM percentages.

---

# 26. PHASE 22 — Full Reference Mission

## Mission

> Why has CAC increased for the last three days, what happens if it continues, and what should we do?

### Expected Execution

```text
Coordinator
    ↓
Performance Lead
    ↓
Observer
    ↓
Anomaly
    ↓
Media appears stable
    ↓
Funnel anomaly detected
    ↓
Performance → Funnel handoff
    ↓
Funnel Agent
    ↓
Mobile CVR deterioration
    ↓
Funnel → Technical handoff
    ↓
Technical Agent
    ↓
Deployment + LCP + JS error evidence
    ↓
Diagnostic
    ↓
DoWhy / causal validation
    ↓
Prediction
    ↓
Strategy
    ↓
Skeptic
    ↓
PASS / remediation loop
    ↓
Claim Gate
    ↓
Coordinator synthesis
```

### Tasks

| ID | Task | Priority | Status | Owner |
|---|---|---:|---|---|
| DEMO-001 | Create mission fixture | P0 | 🟩 DONE |  |
| DEMO-002 | Verify CAC increase | P0 | 🟨 REVIEW |  |
| DEMO-003 | Analyze media metrics | P0 | 🟨 REVIEW |  |
| DEMO-004 | Detect purchase CVR anomaly | P0 | 🟨 REVIEW |  |
| DEMO-005 | Trigger Performance → Funnel handoff | P0 | 🟨 REVIEW |  |
| DEMO-006 | Detect mobile-specific funnel degradation | P0 | 🟨 REVIEW |  |
| DEMO-007 | Trigger Funnel → Technical handoff | P0 | 🟨 REVIEW |  |
| DEMO-008 | Retrieve deployment/LCP/error evidence | P0 | 🟨 REVIEW |  |
| DEMO-009 | Generate competing hypotheses | P0 | 🟨 REVIEW |  |
| DEMO-010 | Reject unsupported hypotheses | P0 | 🟨 REVIEW |  |
| DEMO-011 | Run causal validation | P0 | 🟦 IN PROGRESS |  |
| DEMO-012 | Produce forecast | P0 | 🟨 REVIEW |  |
| DEMO-013 | Generate strategy | P0 | 🟨 REVIEW |  |
| DEMO-014 | Run Skeptic | P0 | 🟨 REVIEW |  |
| DEMO-015 | Run remediation if required | P0 | 🟦 IN PROGRESS |  |
| DEMO-016 | Pass Claim Gate | P0 | 🟦 IN PROGRESS |  |
| DEMO-017 | Produce final verified response | P0 | 🟨 REVIEW |  |
| DEMO-018 | Verify complete mission audit trace | P0 | 🟦 IN PROGRESS |  |

### Demo Definition of Done

- [ ] Mission starts with Performance as lead.
- [ ] Observer produces fixture-backed EvidenceArtifacts.
- [ ] Anomaly Agent quantitatively identifies CVR issue.
- [ ] Performance proposes evidence-backed handoff.
- [ ] Funnel becomes lead.
- [ ] Funnel identifies mobile-specific degradation.
- [ ] Technical becomes lead.
- [ ] Technical evidence aligns with incident timing.
- [ ] Diagnostic produces multiple hypotheses.
- [ ] DoWhy workflow runs with explicit causal assumptions.
- [ ] Skeptic tests alternative explanations.
- [ ] At least one hypothesis is rejected.
- [ ] Prediction contains model metadata.
- [ ] Strategy references validated mechanism.
- [ ] Final response contains no unsupported claims.
- [ ] Every material claim links to artifacts.
- [ ] Full mission can be replayed.

---

# 27. PHASE 23 — MCP Integration

## Start only after fixture reference mission is stable.

| ID | Task | Priority | Status | Owner | Depends On |
|---|---|---:|---|---|---|
| MCP-001 | Inventory existing MCP servers | P0 | 🟩 DONE |  | DEMO-018 |
| MCP-002 | Catalogue MCP capabilities/tools | P0 | 🟦 IN PROGRESS |  | MCP-001 |
| MCP-003 | Map MCP capabilities to domain agents | P0 | 🟩 DONE |  | MCP-002 |
| MCP-004 | Implement MCP Gateway | P0 | 🟩 DONE |  | MCP-002 |
| MCP-005 | Implement agent/tool authorization | P0 | 🟩 DONE |  | MCP-004 |
| MCP-006 | Implement MCP Performance Provider | P0 | 🟦 IN PROGRESS |  | MCP-004,PRV-001 |
| MCP-007 | Implement MCP Funnel Provider | P0 | 🟦 IN PROGRESS |  | MCP-004,PRV-002 |
| MCP-008 | Implement MCP Technical Provider | P0 | ⬜ NOT STARTED |  | MCP-004,PRV-003 |
| MCP-009 | Add provenance from MCP calls | P0 | 🟦 IN PROGRESS |  | MCP-004 |
| MCP-010 | Add MCP error handling | P0 | 🟦 IN PROGRESS |  | MCP-004 |
| MCP-011 | Add MCP result freshness | P0 | ⬜ NOT STARTED |  | MCP-004 |
| MCP-012 | Compare fixture and MCP provider outputs | P1 | 🟦 IN PROGRESS |  | MCP-006:MCP-008 |
| MCP-013 | Disable direct provider bypass in production | P0 | ⬜ NOT STARTED |  | MCP-006:MCP-008 |

---

# 28. PHASE 24 — Additional Domain Agents

Implement only after reference architecture is proven.

## Commerce

| ID | Task | Priority | Status |
|---|---|---:|---|
| COM-001 | Commerce ontology | P1 | 🟦 IN PROGRESS |
| COM-002 | Shopify provider integration | P1 | 🟦 IN PROGRESS |
| COM-003 | Amazon provider integration | P2 | ⬜ NOT STARTED |
| COM-004 | Blinkit provider integration | P2 | ⬜ NOT STARTED |
| COM-005 | Product/SKU entity model | P1 | ⬜ NOT STARTED |
| COM-006 | Orders/sales/returns analysis | P1 | 🟦 IN PROGRESS |
| COM-007 | Domain handoff rules | P1 | 🟦 IN PROGRESS |

## Finance

| ID | Task | Priority | Status |
|---|---|---:|---|
| FIN-001 | Finance ontology | P1 | 🟦 IN PROGRESS |
| FIN-002 | Unit economics engine | P1 | ⬜ NOT STARTED |
| FIN-003 | Gross/net sales metrics | P1 | 🟦 IN PROGRESS |
| FIN-004 | COGS/margin/profit metrics | P1 | 🟦 IN PROGRESS |
| FIN-005 | Reconciliation rules | P1 | ⬜ NOT STARTED |
| FIN-006 | Commerce/Inventory handoff rules | P1 | 🟦 IN PROGRESS |

## Inventory

| ID | Task | Priority | Status |
|---|---|---:|---|
| INV-001 | Inventory ontology | P1 | ⬜ NOT STARTED |
| INV-002 | Stock/available/reserved model | P1 | ⬜ NOT STARTED |
| INV-003 | Days-cover metric | P1 | ⬜ NOT STARTED |
| INV-004 | Stockout risk | P1 | ⬜ NOT STARTED |
| INV-005 | Ageing / dead-stock metrics | P2 | ⬜ NOT STARTED |
| INV-006 | Procurement handoff | P1 | ⬜ NOT STARTED |

## Procurement

| ID | Task | Priority | Status |
|---|---|---:|---|
| PRO-001 | Procurement ontology | P2 | ⬜ NOT STARTED |
| PRO-002 | Vendor entity model | P2 | ⬜ NOT STARTED |
| PRO-003 | PO / MOQ model | P2 | ⬜ NOT STARTED |
| PRO-004 | Lead-time metrics | P2 | ⬜ NOT STARTED |
| PRO-005 | Vendor reliability | P2 | ⬜ NOT STARTED |
| PRO-006 | Replenishment constraints | P2 | ⬜ NOT STARTED |

---

# 29. PHASE 25 — Feature Engine

| ID | Task | Priority | Status |
|---|---|---:|---|
| FTR-001 | Define Feature Registry | P1 | 🟦 IN PROGRESS |
| FTR-002 | Add raw features | P1 | ⬜ NOT STARTED |
| FTR-003 | Add delta/change features | P1 | ⬜ NOT STARTED |
| FTR-004 | Add rolling-window features | P1 | ⬜ NOT STARTED |
| FTR-005 | Add trend/slope features | P1 | ⬜ NOT STARTED |
| FTR-006 | Add volatility features | P2 | ⬜ NOT STARTED |
| FTR-007 | Add funnel-transition features | P1 | ⬜ NOT STARTED |
| FTR-008 | Add interaction features | P2 | ⬜ NOT STARTED |
| FTR-009 | Add contextual/calendar features | P2 | ⬜ NOT STARTED |
| FTR-010 | Add candidate-feature generation via LLM | P2 | ⬜ NOT STARTED |
| FTR-011 | Add leakage detection | P0 | ⬜ NOT STARTED |
| FTR-012 | Add feature validation | P1 | ⬜ NOT STARTED |
| FTR-013 | Add candidate → approved → production lifecycle | P1 | ⬜ NOT STARTED |

---

# 30. PHASE 26 — Model Registry / MLOps

| ID | Task | Priority | Status |
|---|---|---:|---|
| MLO-001 | Install/configure MLflow | P1 | ⬜ NOT STARTED |
| MLO-002 | Register anomaly models | P1 | ⬜ NOT STARTED |
| MLO-003 | Register prediction models | P1 | ⬜ NOT STARTED |
| MLO-004 | Store training metadata | P1 | ⬜ NOT STARTED |
| MLO-005 | Store validation metrics | P1 | ⬜ NOT STARTED |
| MLO-006 | Store feature-set version | P1 | ⬜ NOT STARTED |
| MLO-007 | Add drift monitoring | P1 | ⬜ NOT STARTED |
| MLO-008 | Add model disable switch | P0 | ⬜ NOT STARTED |
| MLO-009 | Add model fallback policy | P0 | 🟦 IN PROGRESS |
| MLO-010 | Add model applicability policy | P0 | 🟦 IN PROGRESS |

---

# 31. PHASE 27 — Observability

| ID | Task | Priority | Status |
|---|---|---:|---|
| OBSV-001 | Configure LangSmith | P1 | 🟩 DONE |
| OBSV-002 | Configure OpenTelemetry | P1 | 🟦 IN PROGRESS |
| OBSV-003 | Trace mission ID | P0 | 🟩 DONE |
| OBSV-004 | Trace task ID | P0 | 🟦 IN PROGRESS |
| OBSV-005 | Trace agent ID | P0 | 🟩 DONE |
| OBSV-006 | Trace evidence IDs | P0 | 🟦 IN PROGRESS |
| OBSV-007 | Trace leadership transfers | P0 | 🟦 IN PROGRESS |
| OBSV-008 | Trace model executions | P1 | 🟦 IN PROGRESS |
| OBSV-009 | Track mission latency | P1 | 🟦 IN PROGRESS |
| OBSV-010 | Track agent calls | P1 | 🟦 IN PROGRESS |
| OBSV-011 | Track token / LLM cost | P1 | 🟩 DONE |
| OBSV-012 | Track unsupported-claim rejections | P1 | 🟦 IN PROGRESS |
| OBSV-013 | Track Skeptic overturn rate | P2 | ⬜ NOT STARTED |
| OBSV-014 | Build Grafana dashboard | P2 | ⬜ NOT STARTED |

---

# 32. PHASE 28 — Testing & Evaluation

## Unit Tests

- [ ] Schema validation.
- [ ] Metric calculations.
- [ ] Capability resolver.
- [ ] Agent selection.
- [ ] Leadership-transfer rules.
- [ ] Claim gate.
- [ ] Provider adapters.
- [ ] Model router.
- [ ] Causal request validation.

## Contract Tests

- [ ] `seleric.swarm.v1` envelope.
- [ ] EvidenceArtifact.
- [ ] A2A transport interface.
- [ ] Provider Protocols.
- [ ] MCP adapter.
- [ ] Model response contract.

## Integration Tests

- [ ] Coordinator → Performance.
- [ ] Performance → Observer.
- [ ] Performance → Funnel.
- [ ] Funnel → Technical.
- [ ] Diagnostic → Domain Evidence Request.
- [ ] Skeptic → Reopen Diagnostic.
- [ ] Claim Gate → Coordinator.

## Adversarial Tests

| ID | Scenario | Status |
|---|---|---|
| TST-ADV-001 | Missing data | 🟩 DONE |
| TST-ADV-002 | Stale data | ⬜ NOT STARTED |
| TST-ADV-003 | Conflicting data | ⬜ NOT STARTED |
| TST-ADV-004 | Invalid metric version | ⬜ NOT STARTED |
| TST-ADV-005 | Agent timeout | ⬜ NOT STARTED |
| TST-ADV-006 | Agent failure | ⬜ NOT STARTED |
| TST-ADV-007 | Handoff ping-pong | 🟩 DONE |
| TST-ADV-008 | MCP unavailable | ⬜ NOT STARTED |
| TST-ADV-009 | Model drift | ⬜ NOT STARTED |
| TST-ADV-010 | DoWhy refutation failure | ⬜ NOT STARTED |
| TST-ADV-011 | Unsupported causal claim | 🟦 IN PROGRESS |
| TST-ADV-012 | Prompt injection in MCP output | 🟩 DONE |

---

# 33. PHASE 29 — Security & Governance

| ID | Task | Priority | Status |
|---|---|---:|---|
| SEC-001 | Agent identity model | P0 | 🟦 IN PROGRESS |
| SEC-002 | Agent capability permissions | P0 | 🟩 DONE |
| SEC-003 | MCP allowlists | P0 | 🟩 DONE |
| SEC-004 | Read-only enforcement | P0 | 🟩 DONE |
| SEC-005 | Secret manager integration | P1 | 🟦 IN PROGRESS |
| SEC-006 | Tool result treated as untrusted input | P0 | 🟩 DONE |
| SEC-007 | Prompt injection test suite | P0 | 🟩 DONE |
| SEC-008 | Audit trail immutability | P0 | 🟦 IN PROGRESS |
| SEC-009 | PII handling policy | P1 | ⬜ NOT STARTED |
| SEC-010 | Human approval framework for future writes | P3 | ⏸ DEFERRED |

---

# 34. PHASE 30 — Production Hardening

| ID | Task | Priority | Status |
|---|---|---:|---|
| PRD-001 | Retry strategy finalized | P1 | 🟦 IN PROGRESS |
| PRD-002 | Timeout strategy finalized | P1 | 🟦 IN PROGRESS |
| PRD-003 | Idempotency verified | P1 | 🟦 IN PROGRESS |
| PRD-004 | Rate limits | P1 | ⬜ NOT STARTED |
| PRD-005 | Circuit breakers | P2 | ⬜ NOT STARTED |
| PRD-006 | Cache strategy | P2 | ⬜ NOT STARTED |
| PRD-007 | Mission resource budgets | P1 | 🟩 DONE |
| PRD-008 | Disaster/restart test | P1 | 🟦 IN PROGRESS |
| PRD-009 | Mission replay test | P1 | 🟦 IN PROGRESS |
| PRD-010 | SLOs defined | P1 | ⬜ NOT STARTED |
| PRD-011 | Deployment pipeline | P1 | 🟦 IN PROGRESS |
| PRD-012 | Staging environment | P1 | ⬜ NOT STARTED |
| PRD-013 | Production environment | P1 | ⬜ NOT STARTED |

---

# 35. Future Phase — A2A HTTP Service Split

Do not start until there is a real operational reason.

| ID | Task | Priority | Status |
|---|---|---:|---|
| A2A-001 | Identify agents requiring service isolation | P3 | ⏸ DEFERRED |
| A2A-002 | Implement A2A HTTP transport | P3 | ⏸ |
| A2A-003 | Publish Agent Cards | P3 | ⏸ |
| A2A-004 | Add agent authentication | P3 | ⏸ |
| A2A-005 | Add TLS | P3 | ⏸ |
| A2A-006 | Add remote-agent health checks | P3 | ⏸ |
| A2A-007 | Test local ↔ remote transport swap | P3 | ⏸ |
| A2A-008 | Split first agent into independent service | P3 | ⏸ |

### Split Agent Only When

- [ ] Separate security boundary is required.
- [ ] Independent scaling is required.
- [ ] Different deployment cadence is required.
- [ ] Separate team owns the agent.
- [ ] Heavy model/GPU workload requires isolation.
- [ ] External interoperability is required.

---

# 36. Current Sprint Template

## Sprint

**Sprint Name:**  
**Start Date:**  
**End Date:**  
**Sprint Goal:**  

### Planned Tasks

| Task ID | Description | Owner | Priority | Status |
|---|---|---|---|---|
|  |  |  |  |  |
|  |  |  |  |  |
|  |  |  |  |  |

### Sprint Success Criteria

- [ ]
- [ ]
- [ ]

### Blockers

| Blocker | Owner | Impact | Resolution | Status |
|---|---|---|---|---|
|  |  |  |  |  |

### Sprint Notes

```text

```

---

# 37. Weekly Project Status

## Week of: __________

### Overall Status

- Project: 🟢 / 🟡 / 🔴
- Architecture: 🟢 / 🟡 / 🔴
- Data: 🟢 / 🟡 / 🔴
- Agents: 🟢 / 🟡 / 🔴
- ML: 🟢 / 🟡 / 🔴
- Causal: 🟢 / 🟡 / 🔴
- MCP: 🟢 / 🟡 / 🔴
- Testing: 🟢 / 🟡 / 🔴

### Completed This Week

- [ ]
- [ ]
- [ ]

### In Progress

- [ ]
- [ ]
- [ ]

### Blocked

- [ ]
- [ ]

### Next Week

- [ ]
- [ ]
- [ ]

### Key Decisions Made

1.
2.
3.

---

# 38. Blocker Register

| ID | Date | Blocker | Affected Tasks | Severity | Owner | Resolution | Status |
|---|---|---|---|---|---|---|---|
| BLK-01 |  |  |  |  |  |  |  |

---

# 39. Risk Register

| ID | Risk | Probability | Impact | Mitigation | Owner | Status |
|---|---|---|---|---|---|---|
| RSK-001 | Too many agents implemented before core architecture works | High | High | Build one vertical deeply |  | OPEN |
| RSK-002 | Agent outputs become unstructured prose | Medium | High | Typed artifact contracts |  | OPEN |
| RSK-003 | Coordinator becomes omnipotent | Medium | High | Strict role boundary |  | OPEN |
| RSK-004 | Metric-definition mismatch | High | High | Canonical metric registry |  | OPEN |
| RSK-005 | LLM invents anomaly | Medium | High | Quantitative anomaly detector |  | OPEN |
| RSK-006 | Narrative root cause mistaken for causality | High | High | Hypothesis + DoWhy + refutation |  | OPEN |
| RSK-007 | Prediction Agent fabricates numbers | Medium | High | Registered model-only outputs |  | OPEN |
| RSK-008 | Agent handoff loops | Medium | Medium | Hysteresis + loop detection |  | OPEN |
| RSK-009 | MCP tool sprawl | Medium | High | Capability gateway + domain ownership |  | OPEN |
| RSK-010 | Fixture numbers mistaken for real data | Medium | High | `synthetic=true` everywhere |  | OPEN |
| RSK-011 | Full swarm invoked for simple lookup | High | Medium | L0/L1 fast path |  | OPEN |
| RSK-012 | Context/token explosion | Medium | High | Artifact refs + context builder |  | OPEN |

---

# 40. Decision Log

| ID | Date | Decision | Reason | Impact | Owner |
|---|---|---|---|---|---|
| DEC-001 |  |  |  |  |  |

---

# 41. Change Request Log

| ID | Date | Requested Change | Requested By | Priority | Decision | Status |
|---|---|---|---|---|---|---|
| CR-001 |  |  |  |  |  |  |

---

# 42. Bug Register

| Bug ID | Description | Severity | Found In | Owner | Status | Fix Version |
|---|---|---|---|---|---|---|
| BUG-001 |  |  |  |  |  |  |

---

# 43. Test Incident Library

Maintain known historical/fixture scenarios.

| Incident ID | Scenario | Expected Lead | Expected Handoff | Expected Root Cause | Status |
|---|---|---|---|---|---|
| INC-001 | CAC ↑ due to mobile technical regression | Performance | Performance → Funnel → Technical | Frontend regression | 🟨 REVIEW |
| INC-002 | CAC ↑ due to CPM inflation | Performance | None | Auction cost | ⬜ NOT STARTED |
| INC-003 | CVR ↓ due to stockout | Commerce/Funnel | → Inventory | Availability | ⬜ NOT STARTED |
| INC-004 | Profit ↓ despite ROAS ↑ | Finance | → Commerce/Funnel | Returns / margin issue | ⬜ NOT STARTED |
| INC-005 | Sales ↓ due to inventory shortage | Commerce | → Inventory → Procurement | Replenishment issue | ⬜ NOT STARTED |

---

# 44. MVP Definition of Done

The first MVP is NOT complete until all items below are true.

## Coordinator

- [ ] Understands query type.
- [ ] Selects fast path vs swarm.
- [ ] Decomposes complex missions.
- [ ] Builds task DAG.
- [ ] Selects domain lead.
- [ ] Runs ready tasks.
- [ ] Handles evidence gaps.
- [ ] Handles leadership transfer.
- [ ] Detects loops.
- [ ] Enforces mission budget.
- [ ] Applies Claim Gate.
- [ ] Produces final response.

## Communication

- [ ] All agent communication uses `seleric.swarm.v1`.
- [ ] In-process transport works.
- [ ] Agent logic is transport-independent.
- [ ] Artifact refs are used instead of giant conversation histories.

## Domain Agents

- [ ] Performance Agent works.
- [ ] Funnel Agent works.
- [ ] Technical Agent works.
- [ ] Performance → Funnel handoff works.
- [ ] Funnel → Technical handoff works.

## Specialists

- [ ] Observer works.
- [ ] Anomaly works.
- [ ] Diagnostic works.
- [ ] Prediction works.
- [ ] Strategy works.
- [ ] Skeptic works.

## Intelligence

- [ ] Numeric facts have provenance.
- [ ] Anomalies come from quantitative detector.
- [ ] Hypotheses are explicit.
- [ ] Causal claim uses causal-analysis artifact.
- [ ] Prediction references registered model/baseline.
- [ ] Strategy references validated mechanism.
- [ ] Skeptic can reopen investigation.

## Data

- [ ] Fixture providers are deterministic.
- [ ] Fixture evidence is clearly synthetic.
- [ ] Provider Protocols are ready for MCP replacement.

## Testing

- [ ] CAC reference mission passes end-to-end.
- [ ] Missing-data scenario passes.
- [ ] Contradictory-data scenario passes.
- [ ] Handoff-loop scenario passes.
- [ ] Unsupported claim is blocked.
- [ ] Mission trace is replayable.

---

# 45. Recommended Immediate Implementation Order

Do these tasks first.

## Sprint 1 — Foundation

- [ ] FND-001 — Repository structure
- [ ] FND-002 — Python environment
- [ ] FND-004 — FastAPI shell
- [ ] FND-006 — LangGraph
- [ ] CTR-001 — MissionState
- [ ] CTR-002 — SwarmEnvelope
- [ ] CTR-004 — EvidenceArtifact
- [ ] CTR-012 — LeadershipTransfer
- [ ] REG-001 — Agent Registry
- [ ] TRN-001 — AgentTransport
- [ ] TRN-002 — InProcessTransport

## Sprint 2 — State + Providers

- [ ] BLK-001 — Mission persistence
- [ ] BLK-004 — Evidence Ledger
- [ ] BLK-009 — Blackboard repository
- [ ] PRV-001 — Performance provider Protocol
- [ ] PRV-002 — Funnel provider Protocol
- [ ] PRV-003 — Technical provider Protocol
- [ ] PRV-004 — Fixture Performance Provider
- [ ] PRV-005 — Fixture Funnel Provider
- [ ] PRV-006 — Fixture Technical Provider
- [ ] PRV-008 — CAC incident fixture

## Sprint 3 — Coordinator + Domains

- [ ] COR-002 — Query Normalizer
- [ ] COR-007 — Complexity Classifier
- [ ] COR-008 — Mission Decomposer
- [ ] COR-009 — Task DAG
- [ ] COR-011 — Lead Selector
- [ ] PERF-004 — CAC decomposition
- [ ] FUN-004 — Funnel model
- [ ] TEC-004 — Deployment analysis
- [ ] LEAD-003 — Handoff validation

## Sprint 4 — Specialists

- [ ] OBS-001 — Observer
- [ ] ANO-002 — Basic anomaly detector
- [ ] ANO-005 — Detector router
- [ ] DIA-001 — Hypotheses
- [ ] DIA-009 — DoWhy integration
- [ ] PRE-003 — Fixture forecast model
- [ ] STR-004 — Intervention generation
- [ ] SKP-001 — Claim extraction

## Sprint 5 — Full Mission

- [ ] DEMO-001 through DEMO-018

Only after this:

- MCP real providers
- Commerce
- Finance
- Inventory
- Procurement
- advanced feature engine
- distributed A2A services

---

# 46. Project Progress Formula

A simple progress model:

```text
Foundation / Contracts       15%
Coordinator / State          20%
Reference Domain Agents      15%
Specialists                  20%
Dynamic Leadership           10%
End-to-End Demo              10%
Testing / Governance          5%
Observability                 5%
```

## Current Progress

```text
Foundation / Contracts       ~14 / 15%   (schema_version + full pg stack outstanding)
Coordinator / State          ~15 / 20%   (control plane done; DAG scheduler/conflict/entity partial)
Reference Domain Agents      ~9  / 15%   (Perf/Funnel logic thin; Technical registry-disabled)
Specialists                  ~11 / 20%   (structure complete; DoWhy + real models are stubs)
Dynamic Leadership           ~8  / 10%   (evidence-gated transfer + arbitration working)
End-to-End Demo              ~6  / 10%   (fixture prototype passes; not on live API, no real providers)
Testing / Governance         ~3  /  5%
Observability                ~3  /  5%

TOTAL                        ~69 / 100%
```

---

# 47. Daily Developer Update Template

```text
Date:

Completed:
- 
- 

In Progress:
- 
- 

Blocked:
- 
- 

Decisions:
- 

Tests Passed:
- 

Tests Failed:
- 

Next:
- 
- 
```

---

# 48. Final Project Principle Checklist

Before marking any architectural task complete, confirm:

- [ ] Is this deterministic where deterministic logic is possible?
- [ ] Is the LLM being used only where semantic reasoning is useful?
- [ ] Does this produce a typed artifact?
- [ ] Can the output be traced to evidence?
- [ ] Can this component fail safely?
- [ ] Does it work without assuming all agents are remote services?
- [ ] Can it later move behind A2A HTTP without rewriting business logic?
- [ ] Does it respect domain vs specialist separation?
- [ ] Does it preserve `mission_lead` vs `active_specialist`?
- [ ] Does it avoid exposing unnecessary context?
- [ ] Can the Skeptic challenge it?
- [ ] Can the Coordinator stop it if it loops?
- [ ] Can the mission be replayed afterward?

---

# 49. Next Recommended Project Goal

> **Do not begin by filling out all remaining domain agents.**

The next concrete milestone should be:

```text
One completely working CAC investigation

Coordinator
    ↓
Performance Lead
    ↓
Observer
    ↓
Anomaly
    ↓
Performance → Funnel
    ↓
Funnel
    ↓
Funnel → Technical
    ↓
Technical
    ↓
Diagnostic + DoWhy
    ↓
Prediction
    ↓
Strategy
    ↓
Skeptic
    ↓
Claim Gate
    ↓
Verified Response
```

Once that mission works reliably, use the exact same architecture to add Commerce, Finance, Inventory and Procurement.

---

# 50. Project Owner Notes

Use this area for free-form notes.

```text




```
