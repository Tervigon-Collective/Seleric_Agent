# 44. Robustness, Scalability, Production-Readiness & Adaptability Backlog

> **Purpose:** Ticket backlog produced from a live audit-and-fix session plus a direct
> "rate the system" review against this project's own stated goals
> (`00_PROJECT_CHARTER.md`, README golden rule: *"LLMs decide what to investigate
> and how to interpret evidence; data/stat/ML/causal systems produce or validate
> the evidence"* — never fabricate, always replayable, dynamic domain leadership).
> Live status for these tickets is tracked in `45_TICKET_TRACKER.md` — this file
> is the durable rationale/evidence record; that file is the thing to update as
> work proceeds.

## Context

Some gaps below are confirmed bugs found live during the session (reproduced against
the running app, not just read from code). Others are structural risks inferred
from the codebase (ontology hand-authoring, LLM-call chain growth, external-
dependency fragility) that were flagged but not chased down to a fix. Ticket IDs
follow the same convention as `SELERIC_SWARM_PROJECT_TASK_SHEET.md`'s existing
registers (`# 38 Blocker Register`, `# 39 Risk Register`, `# 42 Bug Register`) so
these can be merged into that sheet directly.

Status legend (matches the task sheet): ⬜ NOT STARTED · 🟦 IN PROGRESS · 🟨 REVIEW
· 🟥 BLOCKED · 🟩 DONE · ⏸ DEFERRED. Priority: P0 blocking/critical · P1 required
for MVP · P2 important after MVP · P3 optimization/future.

---

## A. Robustness / correctness

| ID | Title | Priority | Evidence / rationale | Acceptance criteria |
|---|---|---|---|---|
| ROB-001 | Fix hardcoded `direction_bad="up"` for every metric in the real MCP provider | P1 | `swarm/providers/mcp_data.py:223` hardcodes "up is bad" for all metrics. Makes the `adverse`/`adversity_score` fields on live (non-fixture) anomalies silently wrong for revenue/profit-type metrics, where "up" is good. | `direction_bad` is sourced per-metric (e.g. from `metric_registry.yaml` or catalogue metadata), matching how the fixture path already does it via `Evidence.provenance.direction_bad`. A regression test asserts a revenue-type metric moving up is never flagged `adverse=True`. |
| ROB-002 | Wire real observation data into the swarm_v2 causal estimator | P1 | `causal/estimator.py` caps confidence to a low tier whenever `ctx.request.observations is None`; the live swarm_v2 path never populates `observations`, so causal confidence is permanently ceilinged (`ASSOCIATION_ONLY`) regardless of real evidence quality — confirmed live via CAC diagnostic queries staying `partial`. | A real diagnostic mission with strong live evidence can reach `STRONGLY_SUPPORTED`/`CAUSALLY_SUPPORTED_UNDER_ASSUMPTIONS`, not just `ASSOCIATION_ONLY`, without `trust_metadata_causal` being set. |
| ROB-003 | Load-test multi-domain metric comparisons | P2 | `propose_handoff_node` chases one foreign-metric owner per handoff; a 3+ domain compare could exhaust `max_leadership_transfers`/`max_agent_calls` before resolving all metrics. Flagged as a risk, never confirmed broken or fixed. | A scripted mission asking for 3+ cross-domain metrics in one query completes (or degrades to a clearly-labeled `partial`) without silently dropping a metric. |
| ROB-004 | Resolve the outstanding test failures from commit `0e10033` | P1 | 6 failures held constant through the whole session (`test_reference_mission_full_diagnostic`, `test_reference_mission_full_prediction`, `test_health_combo_never_returns_running`, `test_prescriptive_with_full_diagnostic_runs_diagnostic`, `test_async_rejects_unknown_scenario`, `test_as_of_extends_scenario_observation_window`) — all traced to one commit (stricter `as_of`/scenario validation) that never got reconciled with the tests it broke. | Full suite passes at 341/341 (currently 335/341), not just "stable at the same 6 failures." |
| ROB-005 | Add a per-mission LLM-call budget/circuit-breaker for orchestration loops | P2 | One instance of this bug class was fixed this session (`apply_skeptic_gate`'s REJECT branch missing a stall guard, causing 5 identical diagnostic+skeptic reruns before giving up). The fix was local to that one branch — no general guarantee every remediation/retry loop has both a round cap and a stall/no-new-information guard. | Audit every loop that can re-activate an agent (remediation, leadership transfer, decomposition refinement) for both a hard round cap and a signature-based stall guard; add the missing ones. |

---

## B. Scalability of core logic patterns

| ID | Title | Priority | Evidence / rationale | Acceptance criteria |
|---|---|---|---|---|
| SCL-001 | Make the diagnostic ontology data-driven instead of hand-authored Python | P1 | `agents/diagnostic/ontology.py`'s `_ONTOLOGY` dict covers exactly 3 outcome metrics (`purchase_cvr`, `cac`, `net_sales`). Every other metric — confirmed live when leadership transferred to `funnel_agent`'s `checkout_rate` — gets 0 hypotheses and a "nothing to diagnose" result, not because nothing is wrong, but because the metric was never modeled. Biggest gap between "the catalogue has N metrics" and "the system can actually diagnose N metrics." | Adding a new outcome metric's candidate mechanisms is a config/catalogue change, not a Python code change — same shift already made for metric/dimension resolution (LLM + real catalogue data, grounded, not hardcoded). |
| SCL-002 | Move causal-graph wiring (metric→graph_id, metric→node, treatment→events) into config | P2 | Already de-duplicated into one source (`ontology.py`) but still three Python literal dicts, not data. Same scaling ceiling as SCL-001, smaller blast radius since it's already single-sourced. | Graph wiring lives in `causal_graphs.yaml` (or equivalent), addable without a code change. |
| SCL-003 | Bound and parallelize the per-mission LLM call chain | P2 | A single diagnostic/prescriptive mission can now sequentially call: classify → decompose → metric_map → dimension_map → hypothesis enrichment → strategy generation → swarm synthesis. Nobody has measured or bounded the resulting latency/cost stack, and independent calls aren't parallelized. | Per-mission LLM call count and latency are logged/traced (LangSmith already wired — confirm it captures this chain end-to-end); independent calls run concurrently where the dependency graph allows it. |
| SCL-004 | Formalize the "add a new domain" checklist as a tested workflow | P2 | Domain wiring is mostly registry-driven (`agent_registry.yaml`, `metric_registry.yaml`), but Inventory/Procurement/Technical are still `enabled: false` per the project's own M11 milestone status. The remaining manual step (SCL-001/002) is exactly what blocks a clean "add a domain" story. | A documented, single-file checklist for onboarding a new domain agent; a smoke test that instantiates a domain purely from registry entries with no code change required beyond MCP module wiring. |

---

## C. Production-grade hardening

| ID | Title | Priority | Evidence / rationale | Acceptance criteria |
|---|---|---|---|---|
| PRD-001 | Add resilience (retry/backoff, graceful degradation) around the external MCP dependency | P1 | `mcp.seleric.com` returned 502 mid-session, taking the whole test suite from 6 to 52 failures for one run before self-recovering. Currently a live-MCP outage surfaces as an opaque `LLM_UNAVAILABLE`/502 propagation with no retry. | Transient MCP failures are retried with backoff before failing the mission; a sustained outage produces a clear, distinct error/status rather than generic `LLM_UNAVAILABLE`. |
| PRD-002 | Add LLM-call cost/latency tracking and a per-mission budget | P1 | Same root cause as SCL-003, framed as a cost-control/observability gap — no visible ceiling on what one mission can spend on LLM calls. | Each mission's total LLM spend (calls, tokens, latency) is recorded against `MissionBudget`; a configurable hard cap exists and is enforced (mirrors the existing `max_agent_calls`/`max_llm_calls` budget pattern already in `coordinator_policies.yaml`). |
| PRD-003 | Close the observability gap tracked in the project's own M12 milestone | P2 | `SELERIC_SWARM_PROJECT_TASK_SHEET.md` already tracks this: M12 "Production hardening / observability — 🟦 IN PROGRESS — LangSmith tracing + budgets; OTel/SLOs/deploy pending." Linked here to the concrete gaps found this session (PRD-001, PRD-002). | OpenTelemetry spans cover the full mission lifecycle; SLOs defined for mission latency/success rate; deploy path documented. |
| PRD-004 | Require branch protection + CI status checks before merge | P1 | Observed directly this session: a teammate committed straight to the same shared branch (`gaurav`) being actively modified, with no PR/review gate in view — confirmed via `git log` showing interleaved commits from two authors on one branch mid-session. Not a code bug, but a real risk to anything calling this "production." | Direct pushes to the main working branch are blocked; merges require the test suite to pass and at least one review. |
| PRD-005 | Verify every user-facing text-generation path is covered by the numeric-fabrication audit | P2 | `services/numeric_audit.py::unaudited_numbers` now guards both `orchestration/synthesize.py` (lookup_v1) and `coordinator/synthesis/llm_response.py` (swarm_v2) — but nothing enforces that a *future* LLM-prose call site remembers to add this guard. | A test (or lint rule) fails CI if a new `runtime.llm.complete(...)` call whose output reaches `final_response`/API output isn't wrapped in the numeric audit. |

---

## D. Adaptability

The project's own charter frames adaptability as: domain leadership moves
dynamically to whichever agent is closest to the causal frontier (not a fixed
script), and metrics/domains should be addable via the registries, not code.
Recent fixes delivered on that for the *lookup/classification* layer (LLM-
grounded metric/dimension/domain resolution). The gap is that the
*diagnostic/causal* layer didn't get the same treatment — it still hard-codes
what it can reason about.

| ID | Title | Priority | Evidence / rationale | Acceptance criteria |
|---|---|---|---|---|
| ADP-001 | Make "can this metric be diagnosed" scale with the metric catalogue, not with engineering time | P1 | Restates SCL-001 from the adaptability angle: adding a metric to `metric_registry.yaml` makes it instantly lookup/comparison/breakdown-capable, but NOT diagnosable — that still needs a hand-written `_ONTOLOGY` entry. Sharpest gap between the system's stated dynamism and its actual behavior. | Same as SCL-001's acceptance criteria; tracked here as the adaptability framing of the same root fix. |
| ADP-002 | Confirm the business-constraint layer (`ConstraintStore`) is genuinely pluggable per business/tenant | P2 | `agents/skeptic/services/business_rules.py`'s `ConstraintStore` is the documented seam onto real finance/inventory/procurement systems, but only `InMemoryConstraintStore` exists today. Unverified whether swapping in a second business's constraints is a config change or a code change. | A second, distinct `ConstraintStore` implementation (e.g. a second fixture set) can be swapped in via config/DI alone, with a test proving both configurations produce different, correct rule violations from the same strategy input. |
| ADP-003 | Audit that Autonomy Levels 0–6 are a config flip, not a rearchitecture | P2 | Charter formalizes Autonomy Levels in `swarm/autonomy.py` with Level 6 (execute business action) explicitly disabled for v0. Never verified whether raising the enabled level later is actually a config change end-to-end, or whether write-path code doesn't exist yet and would need building regardless. | A written audit (or a stub) confirms which autonomy levels are "flip a flag" vs. "build the write-path first," so the adaptability claim is honest rather than aspirational. |
| ADP-004 | Document vocabulary/language assumptions baked into prompts | P3 | The classify/synthesis prompts (`prompts/coordinator/*.yaml`, `prompts/synthesizer/*.yaml`) assume English business vocabulary and this catalogue's specific metric-naming conventions. Not a blocker per the charter (no i18n requirement stated), but worth documenting as a known adaptability boundary rather than discovering it later. | A short doc note listing the vocabulary/language assumptions, so a future team evaluating a new market/language knows the actual scope of change required. |

---

## Where these tickets came from

Every ticket above traces to either (a) a bug reproduced live against the running
app during the session that authored this doc, or (b) a structural risk read
directly from the current source tree, cited by file path. None are speculative
"best practice" filler — each row names the exact file/behavior that motivated it.
