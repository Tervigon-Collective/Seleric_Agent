# Overengineering Validation and Open-Source Reuse Review

## 1. Review objective

The original requirements correctly demand a physical voice loop, business ontology, predictive state, top-three founder interventions, meeting extraction and closed-loop verification. They also propose several tools at once: Feast/Hopsworks, XGBoost/TFT, Isolation Forest, DoWhy/BBN, TOPSIS, Rasa, Jinja2, LiveKit/Pipecat, separate STT/TTS providers, Neo4j/PostgreSQL and Temporal.

**Founder decision (2026-08-31):** the deterministic business-reasoning path described in the original review (health calc → eligible candidates → root-driver consolidation → deterministic ranking) is replaced by a swarm of LLM agents that debate, recruit each other, and reach conclusions with confidence scores. This is a confirmed architecture change, not a proposal under evaluation. Section 3a documents the new adoptions this requires. Everything else in this ledger — the corrections in section 2, the reuse decisions in section 3, and the rejected list in section 5 — stands unless the swarm design specifically requires otherwise, and each such case is called out explicitly below.

For a reliable V1, features are retained but unnecessary infrastructure and duplicate algorithms are removed. The source SRS is the baseline for scope; this document revises implementation choices where the same outcome can be delivered with fewer moving parts.

## 2. Engineering corrections before architecture selection

### 2.1 "Zero hallucination" is no longer the V1 guarantee — read this first

The original V1 guarantee was:

> Every spoken business fact, score and intervention must be generated from a typed, versioned and reproducible computation.

That guarantee assumed a deterministic decision pipeline. It no longer holds for the metric-selection-through-action-proposal path, which now runs through LLM agents. **This is not softened or hidden** — doc 02 §6 and doc 09 restate the new, honest guarantee:

> Every conclusion the swarm reaches is evidence-grounded (every fact cited traces to a certified MCP query or a prior blackboard artifact) and carries an explicit confidence score, but the reasoning path that produced it is not guaranteed to be reproducible bit-for-bit on rerun. The full agent debate that produced the conclusion is permanently recorded on the Blackboard and is the accountability mechanism in place of determinism.

What is unchanged: the Seleric MCP remains the only source of certified facts (§2.2 below), unsupported facts are still never fabricated (agents may only cite blackboard evidence or MCP-derived state, never invent a number), and every Governor-gated action still requires an explicit permission grant. What changed: metric/candidate selection, health assessment, ranking, and root-cause diagnosis are agent judgment calls, not formula outputs, so identical inputs are not guaranteed to produce an identical brief on a second run.

### 2.2 A dependency graph is still not automatically a causal graph

Unchanged. Agents reason over the same declared-dependency ontology (`DEPENDENCY`, `INFLUENCES`, `MEASURES`, `OWNS`, `TARGETS`, `VERIFIED_CAUSAL`) rather than inventing their own causal claims from free text. An agent hypothesis that names a root driver must cite a supporting graph path; it cannot assert causation the ontology does not encode. `VERIFIED_CAUSAL` is still reserved for an approved DoWhy analysis, not agent conviction — the Skeptic agent's job explicitly includes rejecting hypotheses that overstate the evidence taxonomy (see doc 05 §37).

### 2.3 A fixed 3-sigma rule is still not universally valid

Unchanged — this is a Business State Service concern (forecast/anomaly detection), which the swarm change does not touch. The Anomaly agent consumes Business State's calibrated interval/residual output as blackboard evidence; it does not run its own ad hoc thresholding.

### 2.4 One advanced model per ontology node is still unnecessary

Unchanged, same model-selection ladder in Business State Service.

### 2.5 Forecast residuals are still the first anomaly detector

Unchanged. The swarm's Anomaly agent is a consumer of Business State's forecast-residual/interval evidence, not a replacement for it. Agents do not run their own statistical detectors — that would duplicate Business State Service and remove the one deterministic, backtested layer the founder explicitly kept.

### 2.6 TOPSIS is still not required, and deterministic ranking is retired for the swarm path

The original deterministic weighted-ranking policy (hard eligibility → normalized factors → weighted score → tie-break) is retired as the mechanism that selects the top-three founder priorities — that mechanism *was* the deterministic reasoning path the founder replaced. In its place: agents debate candidate priorities, the Skeptic challenges weak ones, and the Coordinator selects the top items by consensus/confidence once debate converges (doc 06 §9 and doc 07). TOPSIS remains rejected for the same reason it always was — it is not needed to rank three items, and it does not resolve the actual problem (adding a second numeric formula does not replace agent judgment).

### 2.7 A feature store is still not required

Unchanged. Agents read the same `state.*` projections and ClickHouse history the deterministic pipeline used; the Blackboard stores case reasoning, not materialized features.

### 2.8 Neo4j is still not required to execute the ontology

Unchanged, and this is now more load-bearing than before: doc 06/14's PostgreSQL+NetworkX ontology becomes the swarm's **shared model of reality** — the mechanism that keeps agent reasoning grounded instead of free-text (see §3a.7). Replacing it with a graph database is unjustified by the swarm change; if anything, the swarm depends on the existing ontology working correctly, which is a reason for extra care in ontology validation, not a reason to swap the store.

### 2.9 Temporal is still unnecessary for V1 volume

Unchanged for background jobs (state refresh, transcription, verification). The swarm's own execution is LangGraph-managed in-process/worker state (§3a.1), not a Temporal-style durable workflow — LangGraph's own checkpointing (persisted to PostgreSQL, see doc 14 §11a) covers the swarm's durability need at V1 scale.

### 2.10 Full voice-to-voice latency under 650 ms is still not a reliable requirement

Unchanged, and now harder: agent debate adds latency the deterministic pipeline did not have. Doc 07 and doc 09 introduce a **precompute-first** rule — the founder-facing voice query reads the latest completed swarm brief; it does not wait for a live agent debate synchronously. A brief is generated ahead of the query by the swarm's own schedule/event triggers (see doc 07 §3).

## 3. Open-source reuse assessment (unchanged systems)

Voice/edge, forecasting/anomaly, meeting transcription, admin, and task-queue reuse decisions from the original review are unaffected by the swarm change and are not restated in full here — see doc 04 and doc 13 for the current table. The ontology/graph, causal, and ranking-adjacent rows are annotated above in §2.

## 3a. New adoptions required by the swarm architecture

These move from not-considered / previously-rejected to **ADOPT**, each with the trigger that justifies it now.

### 3a.1 LangGraph — orchestration, persistent state, agent handoffs

**Decision: ADOPT.** Previously this class of tool was REJECT_V1 in doc 13 §7 ("LLM framework stacks... would duplicate Voice Orchestrator and decision policy") because the six executive intents were bounded and deterministic. That premise is exactly what the founder changed. LangGraph is adopted specifically for its swarm/handoff primitive (`langgraph-swarm`-style active-agent handoff via `Command` returns) and its built-in checkpointer, which persists graph state to a backing store on every step — configured against PostgreSQL so no new datastore is introduced. LangGraph runs inside `insight-decision-service` (see doc 03 §3.4/§14 for the service-boundary decision); it does not become a new service.

Scope boundary: LangGraph orchestrates the swarm's control flow (which agent runs next, what state each agent sees). It does not choose metrics, does not call MCP directly, and does not have standing write permission to anything — all of that is mediated by the Governor (§3a.11) and typed tool ports the same way the old deterministic services used typed ports.

### 3a.2 Blackboard on existing PostgreSQL — no new datastore

**Decision: ADOPT.** The blackboard pattern (shared structured workspace multiple problem-solving agents read/write) is well-established and fits the founder's brief exactly: it is also the accountability mechanism now that determinism is gone. Persisted as new tables in the existing `decision.*` schema (doc 14 §10a) — this is schema addition, not a new system, consistent with "no new datastore" from the original review.

### 3a.3 Internal agent registry — Postgres + application code

**Decision: ADOPT.** Capability advertising (what an agent can do, what tools/cost/reliability it has) needs a place to live so the Coordinator and other agents can discover and recruit. Doc 14 §10a defines `decision.agent_registry` and `decision.agent_reputation`. This is explicitly **not** a new service — it is rows in the existing insight-decision-service schema, read by the Coordinator at recruitment/bidding time. It is designed so the same rows can later back an A2A-facing directory (§3a.a) without a rewrite: `agent_registry.capability` uses the same typed capability vocabulary an A2A Agent Card would need, and `agent_registry.exposure_scope` (`INTERNAL` only in V1) is the field that would flip to `EXTERNAL` when A2A is built.

### 3a.4 Task market / bidding

**Decision: ADOPT**, not deferred to phase 2 — the founder's brief calls this a deliberate differentiator and asked to keep it unless there's a concrete reason it can't work. No such reason surfaced: bidding is cheap to implement as rows (`decision.swarm_task`, `decision.swarm_bid`) and a selection rule in the Coordinator (doc 06 §9.3 gives the concrete formula). It is sequenced in doc 10 as a fast-follow after the Week-1 thin slice, not cut from the design.

### 3a.5 Direct agent-to-agent messaging

**Decision: ADOPT.** Implemented as LangGraph `Command(goto=<agent>, update=<state>)` handoffs plus a durable copy of every handoff written to `decision.swarm_message` for audit. Agents may hand off directly (Observer → Diagnostic → Skeptic) without returning to the Coordinator each time; the Coordinator only re-enters when no agent claims the next step or a Governor gate is hit.

### 3a.6 Collective memory / case retrieval

**Decision: ADOPT** via the `pgvector` PostgreSQL extension, not a new vector database. A closed case's observation+resolution summary is embedded and stored in `decision.swarm_case.resolution_embedding`; new cases run a similarity query before agents start from scratch (doc 06 §9.6). This is still "the existing PostgreSQL instance" — `pgvector` is an extension, the same category of decision as using PostgreSQL's JSONB or RLS, not a new storage system requiring separate justification.

### 3a.7 Ontology-grounded communication

**Decision: ADOPT / extend existing work.** Doc 06/14 already has a PostgreSQL-sourced, NetworkX-executed business ontology (nodes, typed edges, goals). That does not change. What's new: agents are required to cite ontology node/edge IDs in hypotheses and messages rather than free text ("checkout_conversion degraded" not "the checkout thing looks bad"). This is the founder's stated differentiator vs. generic agent frameworks, and it is cheap because the ontology already exists — the swarm layer adds a validation rule (hypothesis must resolve to a real node/edge) rather than new infrastructure.

### 3a.8 Agent reputation

**Decision: ADOPT.** `decision.agent_reputation` (doc 14 §10a) tracks accuracy, calibration, false-positive rate, cost, and speed per agent per problem class, updated when a case closes and its outcome is later confirmed (via the existing commitment-verification loop where the case produced an action, or human confirmation otherwise). The Coordinator/bidding selection reads reputation as a bid tie-breaker (doc 06 §9.3).

### 3a.9 Temporary coalitions

**Decision: ADOPT.** For a broad problem the Coordinator can open multiple `decision.coalition` groups against the same case, each running its own sub-investigation, with the Skeptic and Coordinator reconciling divergent conclusions before an action is proposed. This is LangGraph subgraphs invoked in parallel, checkpointed independently, joined back into the parent case.

### 3a.10 Ontology-visible A2A boundary (explicitly deferred, not built)

**Decision: DEFER — trigger condition, not built now.** Google's A2A protocol is the mechanism for Seleric agents to talk to agents belonging to other businesses. Nothing about A2A is implemented in V1. The registry (§3a.3) is deliberately shaped so this doesn't require a rewrite later: capability records are typed and self-describing, and `exposure_scope` already models the internal/external distinction. **Adoption trigger:** a real cross-business agent-to-agent use case exists (e.g., a Seleric agent needs to negotiate with a supplier's or partner's own agent) — not "it would be nice to be ready." Until then, building A2A support is exactly the kind of speculative infrastructure this ledger exists to prevent.

### 3a.11 Seleric Governor — new, and the most important addition

**Decision: ADOPT, required.** With business logic no longer deterministic, the Governor is the actual safety boundary, and it is designed as absorbing/superseding the relevant parts of the existing doc 08 approval-workflow model and doc 09 security boundary rather than duplicating them (doc 03 §7a, doc 05 §40, doc 09 §5a give the full design). It is not a swarm agent, cannot be recruited, cannot be overridden by any agent conclusion, and its policy is versioned Control Plane configuration (`control.governor_policy`) — the same publish/validate/approve/rollback lifecycle every other policy object already uses. This is reuse of the existing config lifecycle, not a new control plane.

## 4. Open-source adoption controls (unchanged)

The same eight controls from the original review (license review, pinned digests, vulnerability scan, integration tests, hardware performance test, data-egress review, fallback adapter, named owner) apply to LangGraph and `pgvector` exactly as they applied to every other adopted dependency. No exception is made for the swarm stack.

## 5. Components still explicitly not included in V1

- Free-form LLM access to arbitrary tools/SQL — every agent tool call is a typed port gated by the Governor, same discipline as the old deterministic services' provider ports.
- A2A protocol implementation (§3a.10 — deferred, trigger-gated).
- Kafka, RabbitMQ or Redis.
- Kubernetes/AKS.
- Neo4j or any dedicated graph database (§2.8).
- Feast/Hopsworks.
- Temporal (§2.9) — LangGraph's own checkpointing covers swarm durability at V1 scale; this is not a reason to add Temporal.
- Distributed model-serving platform.
- Unbounded agent population — V1 starts at 7 roles, not 50; growing the population is a doc-08-governed configuration change (new `AgentDefinition` object), not a code rewrite, but it is not done speculatively.
- Autonomous production writes without a Governor-approved action — agents may *propose* actions; execution still requires the same class of human/policy gate the old "no automatic campaign/budget write actions" rule required.
- Automatic commitment publication without review (unchanged, meeting-intelligence-service is untouched by this change).
- Voice biometric authorization, custom PCB/enclosure (unchanged).

## 6. Why this is still the minimum viable swarm

| Temptation | Why V1 resists it |
|---|---|
| 50-agent population from day one | Founder's own brief says start at ~7; more roles is a configuration change once the 7-agent loop is proven, not a prerequisite |
| Build A2A now "to be ready" | No real cross-business use case exists yet; the registry is shaped to add it later without a rewrite, which is the correct amount of future-proofing |
| Separate vector database for case retrieval | `pgvector` extension on the existing PostgreSQL satisfies the requirement |
| New "swarm service" (a seventh service) | insight-decision-service already owns decision policy; the swarm is a new internal architecture for that same responsibility, not a new bounded context (doc 03 §3.4) |
| Governor as "just another agent with more permissions" | An agent with more permissions is still recruitable/overridable in principle; the founder's brief is explicit that Governor must sit outside the swarm entirely |
| Full Temporal-style durable workflow for agent steps | LangGraph's PostgreSQL-backed checkpointer already gives resumability at V1's case volume |
