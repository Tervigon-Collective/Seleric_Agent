# Seleric Voice Node V1 — Claude Operating Manual

## Project identity

**Status: pre-implementation.** This repository currently contains only the
approved architecture blueprint (15 spec docs + Mermaid diagrams). There is
**no source code, no package manifest, no git repo, no build/test/lint
commands** yet. Do not invent any of those — check before claiming a command
exists.

- **Product**: Seleric Voice Node V1 — a Raspberry Pi voice assistant that
  turns certified Seleric business metrics into at most three deterministic,
  evidence-backed founder priorities, plus meeting → commitment →
  verification tracking.
- **Primary user**: founder/executive at Tilting Heads (`brand_id = 20`).
- **Target**: working physical prototype by **2026-09-30**.
- **Non-negotiable design rule**: no LLM in the business-reasoning path.
  Metric selection, health calculation, ranking, and fact creation are
  deterministic. An optional future language adapter may only summarize
  already-validated structured output.

## Where to read what (repo map)

| File | Read when |
|---|---|
| `00_README.md` | Orienting a new session — executive summary, reuse table, service list |
| `01_OVERENGINEERING_AND_REUSE_REVIEW.md` | Before proposing new infra/frameworks — this is the project's own YAGNI ledger. **Check here before adding anything not already justified.** |
| `02_SOFTWARE_REQUIREMENTS_SPECIFICATION.md` | Requirements, scope boundaries |
| `03_HIGH_LEVEL_DESIGN.md` | Service boundaries, trust zones, deployment shape |
| `04_TECH_STACK_AND_DEPLOYMENT_OPTIONS.md` | Any dependency/library choice — check it's already selected here first |
| `05_COMPONENT_CONTRACTS.md` | Before implementing any component — purpose/inputs/outputs/failure behavior are pre-specified |
| `06_LOW_LEVEL_DESIGN_OOP.md` | Class/API/schema design, patterns, idempotency |
| `07_WORKFLOW_AND_DATA_FLOW.md` | Voice/state/insight/meeting flow sequencing |
| `08_ADMIN_AND_CONFIGURATION_CONTROL_PLANE.md` | Control-plane / Appsmith / config versioning work |
| `09_SECURITY_OBSERVABILITY_AND_OPERATIONS.md` | Security, monitoring, retention, backup, runbooks |
| `10_IMPLEMENTATION_PLAN_AND_ACCEPTANCE.md` | Ownership, week-by-week plan, acceptance gates, risk register |
| `11_OFFICIAL_SOURCE_RESEARCH.md` | Verifying an OSS project's real capabilities before relying on it |
| `12_SYSTEM_SPEC_SHEET.md` | Quick spec/acceptance lookup |
| `13_OPEN_SOURCE_PLUG_AND_PLAY_MATRIX.md` | Before adopting/rejecting any open-source dependency |
| `14_DATA_MODEL_AND_PERSISTENCE.md` | Schema, current/history split, outbox, RLS, indexes |
| `diagrams/*.mmd` | Mermaid sources matching the docs above |

## Technology stack (selected, not yet built — see doc 04 for full rationale)

- **Backend**: Python 3.12, FastAPI + Pydantic v2, SQLAlchemy 2 (async) + Alembic
- **Edge**: OpenVoiceOS on Raspberry Pi 5 (`ovos-core`, custom `SelericBridgeSkill`), openWakeWord, Silero VAD
- **Data**: PostgreSQL 16 (config/audit/jobs/meetings/decisions), existing ClickHouse (analytics history), existing Seleric MCP/Cube (certified metrics — the only source of truth for business facts)
- **Object storage**: S3-compatible (Azure Blob or MinIO)
- **Task queue**: Procrastinate over PostgreSQL — **no Kafka/Redis/Temporal in V1**
- **Meeting NLP**: WhisperX/Faster Whisper, pyannote.audio, spaCy (EntityRuler/Matcher/DependencyMatcher), deterministic date parsing
- **Admin UI**: Appsmith Community Edition, API-only writes (no direct table access)
- **Observability**: OpenTelemetry → Grafana stack or Azure App Insights

Six services total: `voice-orchestrator`, `business-state-service`,
`insight-decision-service`, `meeting-intelligence-service`,
`control-plane-service`, `admin-ui` (Appsmith). Do not propose a seventh
without checking doc 03 and doc 01 first.

## Architecture rules (from doc 01 / doc 03 — do not relitigate without reading them first)

- Certified facts flow one direction: MCP metrics → versioned business
  objects → derived state → eligible candidates → root-driver consolidation
  → deterministic ranking → template response → action/commitment →
  verification. No step invents unsupported facts.
- The Seleric MCP is the only trusted metric source. Anything MCP doesn't
  expose (goals, escalation rules, ontology health, forecasts, meetings,
  commitments) is an explicit V1 platform object, not an assumption.
- OVOS message bus stays localhost-only (no auth on the bus by design —
  never expose it).
- Provider abstractions (STT/TTS, forecast, anomaly, causal) are Protocol
  interfaces with a registered adapter ID in config — never an arbitrary
  import path from config.
- Appsmith writes only through the Control Plane API — never direct DB.
- Explicitly rejected for V1 (see doc 01 §2, doc 04 §5–7): feature store,
  graph database, model-serving platform, Kafka/Redis/Temporal, TOPSIS
  ranking, fixed 3-sigma anomaly rule, one-model-per-node. Don't reintroduce
  these without a documented trigger being met (each rejection lists its
  adoption trigger).

## Engineering rules

- This is a from-scratch build against a locked spec — implement what docs
  02/05/06 already specify rather than redesigning. If a requirement is
  genuinely ambiguous or missing, flag it rather than inventing scope.
- Before adding any dependency, check doc 04 and doc 13 — it's very likely
  already evaluated (reuse/adopt/defer/reject).
- Every feature/service must land inside the service boundaries in doc 03;
  don't create cross-service shortcuts that bypass a documented API.
- No unrequested abstractions, no scaffolding "for later" — doc 01 is
  explicitly a reaction against that failure mode in the original design.
- Config lives in versioned PostgreSQL objects (doc 08), never hardcoded
  business rules in application code.

## Completion policy

There is no existing test/build/lint pipeline — the first service to be
implemented must establish its own (pytest, ruff/mypy, Alembic migration
check, etc.) as part of that ticket, not assume one exists. Until a service
has real verification wired up, do not claim "done," "working," or "tests
pass" — say what was actually checked (implemented / statically reviewed /
manually run / not yet verified).

Once a service has CI, a ticket is DONE only when: implementation matches
the relevant contract doc, its own tests pass, acceptance criteria have
evidence, and (for security-sensitive work — auth, secrets, PII, audio/PII
retention) a security review found no blocking issues.
