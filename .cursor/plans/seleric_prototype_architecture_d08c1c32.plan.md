---
name: Seleric Prototype Architecture
overview: "Implement a production-minded V1 prototype as a single in-process LangGraph mission on the existing Seleric swarm scaffold: Azure Llama 4 Maverick behind an LLM port, versioned prompts, fixture-backed MCP, evidence/claim gates, and LangSmith tracing/eval from the first vertical slice."
todos:
  - id: sprint-0-foundation
    content: Settings, LLMPort + Azure/Fake adapters, PromptRegistry, LangSmith bootstrap, log redaction, traced LLM ping
    status: completed
  - id: sprint-1-lookup-slice
    content: "LangGraph lookup_v1: coordinator, commerce, observer, fixture MCP, evidence, claim gate, synthesizer, mission API"
    status: completed
  - id: sprint-2-eval-harness
    content: Gold datasets, deterministic + optional LLM-as-judge evaluators, eval CLI, baseline metrics, CI without live Llama
    status: completed
  - id: sprint-3-resilience
    content: Budgets, LangSmith-down isolation, INSUFFICIENT_EVIDENCE UX, optional second metric or comparison class
    status: completed
  - id: later-phases
    content: Resume docs/22 from Anomaly onward only after lookup gold stays at 100% exact-match
    status: completed
isProject: false
---

# Seleric V1 Prototype: Architecture and Implementation Plan

This plan extends the existing [Seleric Intelligence Swarm](docs/00_PROJECT_CHARTER.md) scaffold. It does **not** invent a second agent stack (no Planner/Researcher/Synthesizer agents). Those names in the brief are treated as *prompt-folder examples*; production agent IDs stay `coordinator_agent`, `commerce_agent`, `observer_agent`, `claim_gate`, and a thin `response_synthesizer` node.

The current repo is documentation-complete and implementation-empty: FastAPI returns a fake `planned` mission ([src/seleric_swarm/main.py](src/seleric_swarm/main.py)), agents return `not_implemented`, and [pyproject.toml](pyproject.toml) has LangGraph/MCP/A2A but **no** LLM client, LangSmith, or prompt package. Observability is documented as Phase 7 in [docs/22_IMPLEMENTATION_ROADMAP.md](docs/22_IMPLEMENTATION_ROADMAP.md). This prototype **overrides that sequencing**: tracing and evaluation are Sprint 0, not a hardening afterthought.

```mermaid
flowchart TD
  user[User or eval harness]
  api[FastAPI POST /v1/missions]
  graph[LangGraph mission]
  coord[Coordinator]
  domain[Commerce domain]
  observer[Observer]
  mcp[MCP gateway]
  fixtures[Fixture commerce store]
  ledger[Evidence ledger]
  gate[Claim gate]
  synth[Response synthesizer]
  ls[LangSmith]

  user --> api --> graph
  graph --> coord --> domain --> observer --> mcp --> fixtures
  fixtures --> ledger --> gate --> synth
  coord -.-> ls
  observer -.-> ls
  mcp -.-> ls
  synth -.-> ls
```

---

## 1. Prototype definition

### Objective

Deliver the **smallest useful, reviewable, measurable** intelligence loop:

> A business user asks a grounded lookup question. The system classifies it, retrieves canonical metrics through a governed data path, refuses unsupported numbers, and returns a cited answer. Every hop is traced in LangSmith and regression-tested against a gold dataset.

Success is not “many agents talking.” Success is a replaceable pipeline whose quality we can score.

### Smallest useful end-to-end workflow (build this first)

**Canonical V1 question:** `"What were net sales yesterday?"` (timezone from request scope, default `Asia/Kolkata`).

**Path (minimum agent set, matching [docs/26_AGENT_ACTIVATION_MATRIX.md](docs/26_AGENT_ACTIVATION_MATRIX.md)):**

```text
User
  -> POST /v1/missions
  -> Coordinator (classify + DAG + lead selection)
  -> Commerce domain agent (scope, metric allowlist, MCP capability)
  -> Observer (resolve metric.net_sales, fetch, normalize, evidence artifact)
  -> Claim Gate (deterministic provenance policy)
  -> Response synthesizer (LLM wraps structured claims only)
  -> GET /v1/missions/{id} + LangSmith parent run
```

A second gold question in the same slice: `"What were net sales on 2026-08-01?"` (absolute date, no relative-time ambiguity).

### User interaction flow

1. Client sends `{query, scope:{timezone, as_of?}, mode:"read_only"}`.
2. API creates `mission_id`, `request_id`, `session_id` (session optional; generate if absent).
3. LangGraph runs synchronously for V1 (timeout budget, e.g. 30s). Async job queue is a non-goal.
4. Response includes mission status, claims, evidence refs, trust labels, and `langsmith_run_url` in non-prod.
5. Failures return a structured `MissionError` (`TIMEOUT`, `LLM_UNAVAILABLE`, `INSUFFICIENT_EVIDENCE`, `CLAIM_REJECTED`, `ROUTING_UNSUPPORTED`) — never a crashed 500 with a stack of agent internals.

### Expected inputs (V1)

- Natural-language lookup over **registered commerce metrics** (`metric.net_sales` first; `metric.gross_sales` optional stretch).
- Explicit `scope.timezone` and optional `scope.as_of` date.
- `mode` must be `read_only` (reject anything else).

### Expected outputs (V1)

Typed `MissionResult`:

- `mission_id`, `status` (`completed` | `partial` | `failed`)
- `query_class`: `lookup` (only class implemented)
- `mission_lead`: `commerce_agent`
- `active_specialist`: `observer_agent`
- `claims[]`: `claim_type=numeric`, `support_refs`, `trust_label`
- `evidence[]`: ids, metric id/version, value, unit, time range, source, freshness
- `limitations[]` if data missing/stale
- `trace`: `request_id`, `langsmith_run_id`

No causal language. No recommendations. No invented zeros.

### System boundaries

**In V1:** API, in-process LangGraph, LLM port, prompt registry, fixture MCP, evidence ledger (Postgres from existing [migrations/001_init.sql](migrations/001_init.sql)), claim gate, LangSmith, eval CLI.

**At the boundary, not inside agents:** Azure credentials, LangSmith API key, fixture files, metric YAML.

**Out of process later, not now:** A2A HTTP servers, live Shopify/Meta MCP, Redis-as-source-of-truth, per-agent microservices ([docs/21_DEPLOYMENT.md](docs/21_DEPLOYMENT.md) already says start physically simple).

### Assumptions

- Azure endpoint `*.services.ai.azure.com` is **OpenAI-compatible Azure AI Inference**, not classic Azure OpenAI deployments. The adapter must not assume `gpt-4o` JSON-mode or Azure “deployment name” semantics.
- Llama 4 Maverick **may be weaker at native structured output** than GPT-4-class models. Structured outputs are a first-class retry path, not an assumed platform feature.
- No live merchant MCP is required for the prototype review. A fixture store is the governed data plane stand-in.
- One LangSmith project per environment (`seleric-swarm-local`, `seleric-swarm-staging`).
- Python 3.11+, existing package name `seleric_swarm`.

### Non-goals for V1 (deliberately postponed)

| Postponed | Why |
|---|---|
| Anomaly / Diagnostic / DoWhy / Prediction / Strategy | Requires metric correctness first ([docs/22](docs/22_IMPLEMENTATION_ROADMAP.md) Phases 2–5) |
| Dynamic leadership / handoff state machine | No second domain in the thin slice |
| A2A HTTP + Agent Cards as a network | In-process typed envelopes are enough; HTTP is Phase 6 |
| Live Meta/Shopify/PostHog MCP | External flakiness hides agent/eval signal |
| Autonomous writes | ADR-004 / `ALLOW_WRITE_ACTIONS=false` |
| Conversation memory / multi-turn chat | Missions are one-shot; blackboard is the memory |
| Multi-tenant auth, SSO, Key Vault runtime wiring | Design the interface; implement Key Vault in deploy sprint |
| Fallback models | Port supports it; V1 has one primary model |
| LLM-as-judge in the live request path | Judges belong in eval, not serving |
| Redis-backed graph checkpointing | Optional after Postgres mission persist works |

---

## 2. Architectural decisions (reviewable)

### D1 — Implement into this repo, do not start a greenfield app

**Decision:** Fill [src/seleric_swarm/](src/seleric_swarm/), keep ADRs and agent IDs.  
**Trade-off:** Some stubs (finance, inventory, DoWhy) stay unused.  
**Alternative:** New `apps/prototype` package. Rejected: splits contracts and invites a second architecture.

### D2 — In-process LangGraph now; A2A as a typed envelope, not a network

**Decision:** All V1 agents are LangGraph nodes in one process. Inter-node payloads are the existing A2A envelope shape ([schemas/a2a_envelope.schema.json](schemas/a2a_envelope.schema.json), [src/seleric_swarm/protocols/a2a/envelope.py](src/seleric_swarm/protocols/a2a/envelope.py)).  
**Trade-off:** We do not prove independent deployability.  
**Alternative:** One process per agent + A2A HTTP. Rejected for V1 as unnecessary distributed infrastructure.

### D3 — Deterministic core, LLM at three seams only

| Seam | LLM? | Why |
|---|---|---|
| Intent classification + entity/date parse | Yes, structured | Ambiguous NL |
| Metric ID mapping against registry | Yes, constrained to registry IDs | Ambiguous wording (“revenue yesterday”) |
| Final prose synthesis | Yes | Human-readable wrapping of **already gated** claims |
| Query class routing matrix | **No** | [docs/26](docs/26_AGENT_ACTIVATION_MATRIX.md) is code |
| Metric formulas | **No** | [docs/16](docs/16_METRIC_SEMANTIC_LAYER.md) |
| MCP auth, allowlists, evidence IDs | **No** | Policy |
| Claim gate | **No** | [src/seleric_swarm/services/claim_gate.py](src/seleric_swarm/services/claim_gate.py) |

If Llama returns a metric ID not in the registry, Observer fails closed (`INSUFFICIENT_EVIDENCE`), it does not compute a formula from the prompt.

### D4 — Fixture MCP is the V1 data plane

**Decision:** `MCPGateway.call` is real code with capability allowlists. The commerce server is a **fixture adapter** (`data/fixtures/commerce/daily_sales.json`) behind the same interface as a future Shopify MCP.  
**Trade-off:** We do not validate live API quirks.  
**Alternative:** Wire Shopify on day one. Rejected: blocks eval reproducibility and CI.

### D5 — Observability is a runtime dependency, not Phase 7

Parent LangSmith run = mission. Child runs = coordinator, domain, observer, MCP tool, LLM, claim_gate, synthesizer. Missing required metadata is a **test failure**, not a dashboard nicety.

### D6 — Single process, Postgres for durable audit, Redis unused in V1

Keep docker-compose Postgres. Do not require Redis for the prototype. LangGraph checkpointing: memory saver locally; Postgres checkpointer is a Sprint 3 option.

---

## 3. LLM integration layer

Agent code **must not** import Azure/OpenAI SDKs. Depend on `LLMPort`.

```text
Agent / graph node
  -> LLMPort.complete | complete_structured
       -> AzureOpenAICompatibleAdapter  (V1)
       -> (later) AnthropicAdapter | AzureOpenAIGptAdapter
```

### Port contract (`src/seleric_swarm/llm/port.py`)

`LLMRequest`: `messages`, `model`, `temperature`, `max_tokens`, `timeout_s`, `response_format` (text | json_schema), `metadata` (mission_id, task_id, agent_id, agent_version, prompt_id, prompt_version, workflow_name, workflow_version, request_id), `tags`.

`LLMResponse`: `text`, `parsed` (optional), `model`, `finish_reason`, `usage` (prompt/completion/total tokens if the provider returns them), `latency_ms`, `retry_count`, `provider_request_id`, `normalized_error` (null on success).

`complete_structured[T: BaseModel]`: schema-first. Implementation order:

1. Provider JSON schema / tool-call if advertised.
2. Else: prompt-enforced JSON + Pydantic validation.
3. On `ValidationError`: **one** repair call with the validator error (counted in `retry_count`).
4. Still invalid: `LLMStructuredOutputError` — node maps to `INSUFFICIENT_EVIDENCE` or `LLM_UNAVAILABLE`, does not crash the graph.

### Azure adapter (`src/seleric_swarm/llm/adapters/azure_openai_compatible.py`)

Use the **OpenAI Python SDK** `AzureOpenAI` (or `ChatOpenAI` with `base_url` + Azure API version) pointed at:

- `AZURE_OPENAI_ENDPOINT`
- `AZURE_OPENAI_API_KEY`
- `AZURE_OPENAI_MODEL=Llama-4-Maverick-17B-128E-Instruct-FP8`
- `AZURE_OPENAI_API_VERSION=2024-05-01-preview`

**Do not** use LangChain as the *domain* API. Optional: wrap the client with LangSmith `wrap_openai` **or** invoke via `langchain-openai` only inside the adapter so traces attach automatically. Keep that choice **inside the adapter**.

Recommended V1: `openai.AzureOpenAI` + `langsmith.wrappers.wrap_openai` so LangGraph custom nodes still nest under the parent run, without forcing every agent onto `ChatModel`.

Retries (tenacity, already a dependency): retry **only** on timeout, 429, 5xx. Never retry 4xx validation. Default: 2 retries, exponential backoff, overall deadline from `timeout_s`.

Error normalization: map SDK exceptions to `LLMError` codes `AUTH`, `RATE_LIMIT`, `TIMEOUT`, `BAD_REQUEST`, `UNAVAILABLE`, `PARSE`.

Fallback models: `LLMPort` accepts `fallback_model: str | None`; factory returns a **noop in V1** (`FallbackDisabled`). Do not silently swap models in production without an experiment.

### Configuration (`src/seleric_swarm/config/settings.py`)

`pydantic-settings` already listed. Env for local:

```text
AZURE_OPENAI_ENDPOINT=https://llama4-maverick-prod-resource.services.ai.azure.com
AZURE_OPENAI_API_KEY=...          # from env, never YAML
AZURE_OPENAI_MODEL=Llama-4-Maverick-17B-128E-Instruct-FP8
AZURE_OPENAI_API_VERSION=2024-05-01-preview
LLM_TIMEOUT_S=30
LLM_TEMPERATURE=0
LLM_MAX_TOKENS=1024
LLM_MAX_RETRIES=2

LANGSMITH_TRACING=true
LANGSMITH_API_KEY=...
LANGSMITH_PROJECT=seleric-swarm-local
LANGSMITH_WORKSPACE_ID=           # if needed
```

Replace the placeholder `LLM_*` keys in [.env.example](.env.example). **Never log** endpoint query strings that contain keys; redact `api_key` in structlog processors.

### Secrets in deployed environments

- Local: `.env` (gitignored).
- Deployed: **Azure Key Vault** + managed identity; app settings only hold Key Vault references (`AZURE_OPENAI_API_KEY` secret name `seleric-azure-openai-api-key`, `LANGSMITH_API_KEY` similarly).
- CI: GitHub/Azure DevOps secret variables; eval jobs get a **restricted** LangSmith key and a **non-prod** Azure key if live LLM tests run; default CI uses recorded/cassettes or a fake adapter.

`FakeLLMAdapter` (deterministic fixtures) is mandatory for unit tests so CI does not depend on Llama.

---

## 4. Prompt management

### Layout (git is source of truth)

```text
prompts/
  coordinator/
    classify.v1.yaml
  observer/
    metric_map.v1.yaml
  synthesizer/
    response.v1.yaml
  _schemas/          # optional JSON schema copies referenced by prompts
```

Do **not** create `planner/` / `researcher/` folders. They do not exist in the catalog.

### YAML contract (every file)

```yaml
id: coordinator.classify
version: "1"
agent_id: coordinator_agent
agent_version: "0.1.0"
workflow: lookup_v1
model: Llama-4-Maverick-17B-128E-Instruct-FP8
temperature: 0
max_tokens: 800
output_schema: CoordinatorClassificationV1
langsmith:
  prompt_name: coordinator.classify
  dataset: eval/datasets/coordinator_classify.jsonl
system: |
  ...
user_template: |
  Query: {{ query }}
  Timezone: {{ timezone }}
  As-of: {{ as_of }}
  Allowed query classes: lookup
```

`PromptRegistry.load(id, version)` renders templates with a strict allowlist of variables (no extra keys). Active versions live in [config/](config/) e.g. `config/prompt_versions.yaml`:

```yaml
coordinator.classify: "1"
observer.metric_map: "1"
synthesizer.response: "1"
```

Production code reads **only** the pinned file. New work is always `*.v2.yaml` alongside v1.

### Experiment → evaluation → production candidate

```text
1. Author prompts/<agent>/<name>.vN.yaml (PR, not hot-edit prod)
2. Register/update LangSmith prompt (push name+commit SHA) optional
3. Run eval CLI: langsmith experiment vs current production version
4. Gates (must all pass):
   - schema validity = 100%
   - routing exact-match on gold lookup set >= threshold (start: 0.95)
   - numeric exact-match on fixture metrics = 100%
   - claim-gate rejection of uncited numbers = 100%
   - faithfulness LLM-as-judge on synthesis >= threshold (start: 0.80) AND no judge on numbers
   - p95 latency / token budget vs baseline (no silent regressions > 20% without sign-off)
5. If pass: bump config/prompt_versions.yaml in a separate PR with experiment link
6. If fail: do not merge; file failure modes; iterate
```

Never “improve the prompt” in prod without a LangSmith experiment URL in the PR.

---

## 5. LangGraph workflow (V1)

Extend [src/seleric_swarm/orchestration/graph.py](src/seleric_swarm/orchestration/graph.py) and [state.py](src/seleric_swarm/orchestration/state.py):

```text
coordinator -> route
  lookup+commerce -> domain_commerce -> observer -> claim_gate -> synthesize -> END
  unsupported    -> finalize_unsupported -> END
  any node error -> finalize_error -> END
```

**Coordinator output schema (structured):** `query_class`, `domain_lead`, `entities`, `time_range`, `metric_hints[]`, `unsupported_reason`.  
**Routing after that is code:** if class != lookup or lead != commerce → unsupported (V1 does not activate Performance/Funnel).

**Observer output:** `EvidenceBundle` (list of `EvidenceArtifact` matching [schemas/evidence_artifact.schema.json](schemas/evidence_artifact.schema.json)).  
**Claim gate:** existing `validate_claim`; persist `gate_status`.  
**Synthesizer:** LLM sees **only** gated claims + evidence refs, with an instruction that numbers must be copied, not recomputed. Post-check: every number in prose must appear in evidence values (deterministic digit scan). Fail → strip to a table-only fallback (no LLM numbers).

Budgets on state: `max_llm_calls=6`, `max_tool_calls=8`, `max_runtime_s=30`.

---

## 6. LangSmith observability architecture

### Tracing — what creates spans

| Span | Source |
|---|---|
| `mission.lookup_v1` | API wrapper `ls.trace` / LangGraph compile with `run_name` |
| `node.coordinator` | graph node |
| `llm.coordinator.classify` | LLMPort metadata |
| `node.commerce` | graph node |
| `node.observer` | graph node |
| `tool.mcp.commerce.daily_sales` | MCPGateway |
| `node.claim_gate` | graph node (no LLM) |
| `llm.synthesizer.response` | LLMPort |
| `node.finalize` | graph node |

LangGraph + wrapped OpenAI client should nest LLM children automatically if the parent run is in context (`tracing_context` / `langsmith.run_helpers`).

### Metadata on every run (required)

`request_id`, `session_id`, `mission_id`, `task_id`, `workflow_name=lookup_v1`, `workflow_version`, `agent_name`, `agent_version`, `prompt_id`, `prompt_version`, `model`, `query_class`, `latency_ms` (span-native), `retry_count`, `tool_name` (tools only), `evidence_ids`, `claim_ids`, `error_code`.

**Never:** API keys, raw `.env`, full MCP auth headers, PII beyond the business query (treat query as potentially sensitive; do not put secrets in queries in gold datasets).

### Datasets (start small, representative)

| Dataset | Size | Gold |
|---|---|---|
| `eval/datasets/lookup_commerce.jsonl` | 15–25 | metric id, date, numeric value from fixtures, expected lead |
| `eval/datasets/coordinator_classify.jsonl` | 20+ | query_class, domain, unsupported cases (“why did CAC rise”, “email a vendor”) |
| `eval/datasets/claim_gate.jsonl` | 10 | pass/fail, no LLM |
| `eval/datasets/injection_tool_text.jsonl` | 5 | tool payload tries to override policy |

Include: relative dates, explicit dates, synonym (“revenue” → net_sales or unsupported if ambiguous), missing fixture day → INSUFFICIENT_EVIDENCE, prompt-injection in tool text.

### Evaluators — what to use and what not to judge with an LLM

| Metric | Method | LLM-as-judge? |
|---|---|---|
| JSON/schema validity | Pydantic | **No** |
| Routing (class, lead) | Exact match | **No** |
| Metric ID | Exact match vs registry | **No** |
| Numeric value / units / date range | Exact match / tolerance 0 | **No** |
| Evidence ref present on numeric claims | Assertion | **No** |
| Uncited number in synthesis | Regex/digit audit vs evidence | **No** |
| Tool selection | Exact match vs gold tool | **No** |
| Latency, tokens, cost | LangSmith usage + timers | **No** |
| Hallucinated extra metrics | Set comparison | **No** |
| Synthesis faithfulness / completeness / tone | LLM-as-judge **or** human | **Yes, eval only** |
| Ambiguous NL “was this a good answer?” | Human review sample | Human |

**Do not** use LLM-as-judge for money, counts, dates, routing, or schema. Llama judging Llama on numbers is circular.

Automated judges for synthesis: short rubric (faithfulness, completeness, no extra numbers). Pin judge **model + prompt version** separately; ideally a *different* model when available — until then, accept the bias and keep the numeric gates deterministic.

Human feedback: LangSmith annotation queue on failed experiments and a 10% weekly sample of staging traces.

### Regression and experiments

- `make eval` runs deterministic tests always; live LLM eval is `make eval-llm` (opt-in).
- Each prompt PR must attach `experiment_id`.
- Baseline = currently pinned prompt versions on the gold lookup set.

---

## 7. Project structure (proposed)

Keep the existing top level; add the missing runtime modules. Unused domain/intelligence stubs remain as placeholders.

```text
seleric_swarm/
  pyproject.toml
  .env.example
  prompts/
    coordinator/classify.v1.yaml
    observer/metric_map.v1.yaml
    synthesizer/response.v1.yaml
  config/
    prompt_versions.yaml
    metric_registry.yaml          # promote from example
    agent_registry.yaml
    mcp_servers.yaml
  data/fixtures/commerce/daily_sales.json
  eval/
    datasets/*.jsonl
    evaluators/                   # deterministic + optional judge
    suites/lookup_v1.yaml
  src/seleric_swarm/
    main.py                       # FastAPI
    config/settings.py
    llm/
      port.py
      factory.py
      errors.py
      adapters/azure_openai_compatible.py
      adapters/fake.py
    prompts/registry.py
    observability/
      tracing.py                  # parent run, metadata helpers, redaction
      langsmith_eval.py
    orchestration/graph.py, state.py
    agents/...                    # implement coordinator, commerce, observer only
    protocols/mcp/gateway.py      # allowlist + fixture server
    protocols/mcp/servers/fixture_commerce.py
    services/evidence.py, claim_gate.py, metrics.py
    persistence/                  # Postgres mission/evidence/claims
  tests/
    unit/                         # port, claim_gate, routing, prompt render
    contract/                     # schemas, envelopes, MCP fixture contract
    replay/                       # lookup_v1 gold
    adversarial/                  # injection, missing data
```

Dependencies to add: `openai`, `langsmith`, `langchain-core` (only if needed for LangGraph message types — LangGraph 1.x already pulls it). Avoid adding full `langchain` hub unless prompt-hub push is required.

---

## 8. Agile implementation sequence

Philosophy for every increment: **Build → Trace → Evaluate → Identify failure modes → Improve → Regression test → Release → Repeat.**

### Sprint 0 — Foundation (traceable hello-LLM)

**Build:** settings, Key Vault *interface* notes, `LLMPort` + Azure adapter + Fake adapter, PromptRegistry, LangSmith bootstrap, structlog redaction, `GET /health` and `POST /v1/llm/ping` (dev-only) that completes one traced call.  
**Trace:** confirm spans + token usage in LangSmith.  
**Evaluate:** unit tests for settings (no default secrets), fake adapter, redaction.  
**Exit:** a ping run is visible with `model`, `latency_ms`, no key in logs.

### Sprint 1 — Lookup vertical slice (the prototype)

**Build:** Coordinator structured classify, deterministic route, Commerce allowlist, Observer + fixture MCP, evidence persist, claim gate, synthesizer + numeric audit, `POST /v1/missions` + `GET /v1/missions/{id}`.  
**Trace:** full User→…→Final path with required metadata.  
**Evaluate:** 10-item lookup gold (exact numbers).  
**Failure modes to hunt:** date parse, metric synonym, missing day, schema fail, synthesizer number drift.  
**Release:** `lookup_v1` pinned prompts.

### Sprint 2 — Eval harness and quality bar

**Build:** eval CLI, datasets above, LangSmith experiment helper, CI job for deterministic suite.  
**Evaluate:** routing unsupported queries; injection tests.  
**Improve:** prompt v2 only if Sprint 1 failure log justifies it.  
**Exit:** documented baseline metrics in `eval/baselines/lookup_v1.json`.

### Sprint 3 — Resilience and productization

Timeouts/retries already in adapter; add mission-level deadline, Postgres checkpointer optional, `INSUFFICIENT_EVIDENCE` UX, comparison query class **or** second metric (`gross_sales`) if lookup is stable.  
**Not** Anomaly yet unless lookup exact-match stays 100% on gold.

### Sprint 4+ — Resume existing Phase 2+ roadmap

Anomaly → Diagnostic/DoWhy → Prediction → Strategy/Skeptic-full → A2A HTTP → live MCP. Each phase keeps the same loop and new datasets.

---

## 9. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Llama 4 poor JSON / schema adherence | Constrained schema, repair retry, FakeLLM in tests, fail closed |
| Azure AI Inference ≠ Azure OpenAI quirks | Isolate in one adapter; contract test against a recorded ping |
| LangSmith becomes a prod SPOF | Tracing must be best-effort: queue/drop spans, never fail the mission if LangSmith is down |
| Fixture MCP diverges from real MCP | Shared `MCPGateway` + contract tests; live MCP is a later adapter |
| Synthesizer hallucinates numbers | Deterministic post-audit; fallback to structured-only response |
| Scope creep to full swarm | Explicit V1 non-goals; coordinator returns `unsupported` |
| Prompt injection via fixtures | Treat tool text as untrusted; policy in system prompt + tests |
| Token/cost blow-up | Budgets, temperature 0, small max_tokens, eval cost dashboard |

---

## 10. Measurable success criteria (prototype done)

1. `POST /v1/missions` for the canonical question returns `status=completed`, a numeric claim for `metric.net_sales`, matching fixture value, with `evidence_id` and `trust_label` in `{VERIFIED, STRONG}`.
2. LangSmith parent run shows the seven span types in section 6, with all required metadata keys.
3. Removing `support_refs` in a unit test causes claim gate failure (existing test stays; extend for forecasts unused in V1).
4. Gold lookup set: **100%** numeric exact-match; **100%** schema valid; routing exact-match ≥ **95%** on classify set.
5. Unsupported query (“Why did CAC increase?”) does **not** call commerce MCP and returns `ROUTING_UNSUPPORTED` / limitations, not a guessed narrative.
6. Missing fixture date → `INSUFFICIENT_EVIDENCE`, not `0`.
7. `FakeLLMAdapter` suite green without network. Live ping is optional/manual.
8. `grep` of logs from a traced run finds no API key.
9. Swapping providers requires a new adapter + env; coordinator/observer tests still pass against `LLMPort`.
10. PR template includes LangSmith experiment link before changing pinned prompt versions.

**Architecture-review extras (document in PR, not extra code):** adapter vs LangChain-everywhere (chose port + wrap_openai); in-process vs A2A HTTP (chose in-process); fixtures vs live MCP (chose fixtures); LLM-as-judge limited to synthesis eval.
