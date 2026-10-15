# Diagnosis engine rebuild: plan

2026-10-15. Status: **PLAN, nothing built.** Written from the owner interview of 2026-10-15; where it differs from `RESEARCH_REPORT_2026-10-15.md` §4.5 the interview wins and the override is recorded in §1. Open items need an owner ruling before the phase that depends on them (§9).

## 0. Objective

Given "metric X moved on window W (scope S)", return the smallest set of explanations the data support, each with an evidence grade, the unexplained share, and what data would decide the rest. Correct cause when findable; "located, not identified" when not; never a confident wrong cause. A person acts on the result; the engine suggests levers, it does not act.

## 1. Decisions (owner rulings and overrides of the report)

| ID | Ruling | Source | Overrides report? |
| --- | --- | --- | --- |
| D-1 | **Always deep**; no wall-clock target; the investigation is bounded by evidence, plus fixed safety bounds (D-2) | interview | **Yes**: report deferred scheduler/recursion/async; they are now core |
| D-2 | Termination: stop when the unexplained share improves < 5 points over 2 consecutive rounds, or all open hypotheses are resolved. Safety bounds (defaults, change by ruling only): depth 4, branching 3, 60 operator runs, 400 `causal_queries`, 20 min wall clock. Hitting a safety bound is reported as such | advisor-recommended defaults | new |
| D-3 | Scheduler is **deterministic engine code**. The LLM only narrates the final structured result (no LLM in the loop; avoids Azure 429 mission restarts and OpenRCA-type failure) | interview + research §3.6 | refines |
| D-4 | Deep missions run on the **mission queue** with stage events, not the interactive turn. `MISSION_TIMEOUT_S=300` and the ~150 s turn limit are incompatible with D-1, so deep missions need their own timeout class and the agent prompt rule changes from "call diagnose" to "start investigation, stream stages" | interview + code | new |
| D-5 | Day-one question shapes: short event, period vs period, slow drift, filtered scope | interview | **Yes**: GCM `distribution_change` and `ruptures` are core, not spike/small |
| D-6 | One **global reviewed graph**, owned and reviewed by the owner; per-brand data overlay only | interview | confirms |
| D-7 | Discovery libraries (causal-learn, tigramite) are **offline audit only**, never in the answer path | interview | confirms; GPL review for tigramite before any use |
| D-8 | Anomaly: one shared detector module; migrate diagnosis + exploration; business_state and forecasting get thin adapters, behaviour unchanged | interview | confirms |
| D-9 | Build on **catalogue v2 behind ports**; Cube v3 = new adapter + regenerated graph | interview | confirms |
| D-10 | Pricing and inventory evidence: define the evidence port now; data adapters later when the views exist in serve/Cube v3 | interview | confirms |
| D-11 | Keep the 4 labels (`supported_cause`, `likely_contributor`, `correlation`, `insufficient_evidence`); add `unexplained_share` and `what_would_decide_it` to the report | interview | confirms |
| D-12 | Acting = report + ranked lever suggestions; no automation | interview | confirms |
| D-13 | **Big-bang worktree deploy** (no shadow run). Therefore the replay/eval gate is a **hard pre-deploy gate** and a rollback image is tagged first | interview + advisor | **Yes**: report suggested shadow-run |
| D-14 | Eval data: brand 20 only. Truth comes from injection into real series, independent records, and null worlds; unknown real incidents get stability checks, not an accuracy number | interview + advisor | new |
| D-15 | Multiple-testing control spans the **whole investigation** (Benjamini-Hochberg over the ledger), not per operator | advisor | new |
| D-16 | Weight overlays are fitted on **pre-event windows only**, enforced by a test | advisor | new |
| D-18 | **Live investigation panel in chat.** As soon as a question routes to diagnosis, the chat shows a causal section that streams the full detail of what the engine is doing, WHEN through WHY: graph subgraph generated and every node/edge traversed (with depth and branch), hypotheses opened / tested / resolved with evidence and BH-adjusted p, each operator run (inputs, scope, window, queries issued, duration, result), weight overlay used, termination reason and bounds consumed, unexplained share over time. Full detail, not a summary; the narrated answer comes last | owner, 2026-10-15 | new |
| D-19 | D-2 safety bounds accepted as proposed (depth 4, branching 3, 60 operators, 400 `causal_queries`, 20 min) | owner, 2026-10-15 | closes O-1 |
| D-20 | The investigation panel is expandable and has a Cancel / Stop button. Stop is cooperative: the scheduler checks between operators, emits `investigation_cancelled`, and returns the partial ledger with whatever is already graded; the narration then says the investigation was stopped early | owner, 2026-10-15 | closes O-5 |
| D-17 | Existing rules apply to all new code: no hardcoded metric names/keywords, no regex, verify with live runs cross-checked in ClickHouse, run only the golden 6 plus relevant items (never the golden 30) | memory | n/a |

## 2. Build location and graph consumption

- Worktree `~/work/agent_diag_v4` off `gaurav` `bd8dfde` (name is a proposal). **Never** edit or build from `/opt/seleric/Seleric_Agent` (a teammate once built the live dir with WIP into production). Live dir `rebuild.md` deletion is untouched.
- Dependency graph: commit `e37eecc` is on `origin/main` of Agent Core (local `main` was just behind). The engine reads a **vendored copy** of `dependency_graph.json` placed in the worktree with its `catalogue_version` and `schema_version`, verified at load against the live catalogue. No push to Core `main` is needed (a push there deploys). Open item O-3 decides the long-term path (artifact vs MCP).
- Core-side changes (new edge kinds, causal_priors edits) are made in `~/work/agent_core_*` worktrees and are separate, owner-approved pushes.

## 3. Target shape (lean)

```
ports:      GraphPort  SeriesPort  ChangeLogPort(+evidence kinds: lever, price, stock, incident)
detect:     one robust detector (median/MAD, reference-aware)  <- diagnosis, exploration; adapters for the others
operators:  WHEN (ruptures) | IS_REAL | WHAT (identities, bridge, Shapley) | WHERE (splits; RiskLoc if justified)
            | WHY_EVENT (graph-DAG DoWhy + refuters + intervention evidence)
            | WHY_PERIOD (dowhy.gcm distribution_change on the graph)  | MEASUREMENT (co-measure divergence)
graph:      candidate planning from reviewed edges; r/CV thresholds demoted to checks; measured-weight overlay
ledger:     hypotheses with status, evidence, tests; BH over all tests; unexplained share; residual hypothesis "not in data"
scheduler:  deterministic; next operator from ledger state; D-2 bounds; stage events
output:     structured report -> LLM narrates -> existing grounding check
```

The existing layer 1-3 maths and the four-label classification are kept, moved behind operator interfaces, and must reproduce current output before they change (Phase 2).

### 3.1 Investigation trace (D-18)

The trace is a first-class engine output, not logging bolted on later. Every operator, ledger change and graph step emits a typed, ordered, versioned event (`seq`, `ts`, `investigation_id`, `depth`, `branch`, `parent_id`, `kind`, payload) from **Phase 1 onward**, so each phase's work is visible in the panel when it lands. Event kinds (initial set): `investigation_started`, `window_resolved`, `graph_loaded` (version, catalogue version), `subgraph_built` (nodes, edges, excluded edges with reason), `node_visited` / `edge_traversed` (kind, sign, lag, tier, prior strength, overlay weight), `operator_started` / `operator_finished` (WHEN, IS_REAL, WHAT, WHERE, WHY_EVENT, WHY_PERIOD, MEASUREMENT: inputs, query count, duration, result digest), `query_issued` (cache hit or not), `hypothesis_opened` / `tested` / `resolved` (status, evidence, raw and BH-adjusted p), `refuter_result`, `unexplained_share_updated`, `bound_consumed` (depth / branch / operators / queries / wall clock), `investigation_finished` (termination reason), `narration_started`.

Delivery: the engine writes events through the existing mission event path (`api/async_missions.py` events, mapped by `conversations/event_mapping_v1.py`) so no new transport is needed; the UI extends the existing causal graph panel (`office-ui/src/components/CausalGraphPanel.tsx`) with a live view: growing graph with the current node highlighted, a depth/branch tree of hypotheses, an operator timeline, and an expandable raw-event log. The events are persisted with the mission so a finished investigation can be replayed and used by the eval harness to assert on behaviour (e.g. termination, bounds, which edges were traversed). Cancelling an investigation (O-5) emits `investigation_cancelled` and returns the partial ledger.

## 4. Phases

Each phase is gated on the eval (Phase 0) plus a live re-run of the relevant questions with a ClickHouse cross-check. A phase does not start until the previous gate passes.

| Phase | Work | Gate |
| --- | --- | --- |
| **0 Eval harness** (first) | See §5. No engine code is touched. Baseline = current engine scored on the full suite, recorded | Suite runs end to end; baseline numbers committed |
| **1 Ports, graph adapter, shared detector, trace events** | `GraphPort`/`SeriesPort`/`ChangeLogPort`; graph loader with version checks; one detector module; diagnosis + exploration migrated; business_state/forecasting adapters; **trace event schema + emitter (§3.1) wired into the existing layers** | Detector parity on existing unit tests; graph loader rejects stale/mismatched versions; no catalogue names in engine source (existing test); every existing layer emits ordered events that a test can replay |
| **2 Operator split** | Split `_diagnose` into operators sharing the fetch cache; `diagnose_metric_change` stays as a wrapper | Replay output identical to baseline on the suite |
| **3 Graph-driven candidate planning** | Replace lineage-hub scoring and r ≥ 0.95 / CV < 2% exclusions with graph edge kinds (co_measure, twin, allocation, subset, tier order); thresholds remain as data checks that raise a *measurement* hypothesis | Fewer false causes on null and measurement worlds; no regression on injected-cause top-k |
| **4 WHEN, period, drift** | `ruptures` onset and regime-safe history; GCM `distribution_change` for period vs period; drift handling for multi-week slides; first measured-weight overlay (pre-event only, D-16) | Period/drift injection worlds pass; the 30d-vs-30d ROAS case reproduces the known split |
| **5 Ledger, scheduler, deep mode, live panel** | Ledger with BH (D-15), residual hypothesis, reconstruction check, D-2 termination, mission-queue integration (D-4), prompt rule change; **live investigation panel in `office-ui` (D-18): graph traversal view, hypothesis tree, operator timeline, raw log, cancel** | Termination test (always stops inside bounds); deep missions survive an LLM 429; false-attribution rate does not rise vs Phase 4; panel shows every event kind in a recorded investigation (UI test on a replayed trace) and the log is complete with no gaps in `seq` |
| **6 RiskLoc (conditional)** | Run the cross-dimension residual test on the suite first. Port only if cross-dimension combinations explain material unexplained change that layer 3 misses | Test result recorded; port only on a positive result |
| **7 Offline discovery audit** | causal-learn / tigramite (after licence review) as a nightly job that diffs data against the reviewed graph and proposes edges for owner review | Report produced; nothing enters the answer path |

Deploy: only after Phase 5 passes the hard gate (D-13). Tag a rollback image of `seleric_agent-api` before deploy; deploy from the worktree build, not the live directory.

## 5. Eval design (Phase 0) and its blind spots

Truth sources, in order of strength (D-14):

1. **Injection into real brand-20 series** with known size and date: spend cut in one campaign, CTR collapse, conversion drop in one channel, mix shift (COD vs prepaid), launch of a new low-click campaign.
2. **Independent-record incidents** (cause verified by a record the engine does not use as a statistic): the 2026-09-14..22 ad outage (spend = 0); the Amazon SP ingest that returned 0 rows 09-14..22 while reporting COMPLETED (a measurement cause); the AWARENESS-03OCT launch (change log). The engine still gets scored only on what the record proves.
3. **Unknown real incidents**: no accuracy score. Measure stability (same answer under window shifts and row subsampling), reconstruction gap, and unexplained share.

Circularity guard: injection that propagates through the same reviewed graph tests only self-consistency. The suite therefore must also contain:
- causes **not in the graph or catalogue** (expected verdict: located, not identified);
- worlds where a mechanism is **absent or reversed** relative to the graph;
- **measurement breaks** (co-measures diverging, tracking loss);
- **null injections** (nothing caused it; expected: no cause, no unusual change);
- **false-premise questions** (the metric did not move the way the user says).

Metrics, scored separately: top-1 and top-3 hit rate on findable worlds; **false-attribution rate** on null / hidden / measurement worlds; correct "not identified" rate; unexplained-share error against the planted effect; run-to-run stability. Phase gates compare against the Phase 0 baseline of the current engine.

## 6. Graph usage and weights

Per D-6 and the report §4.1-4.2:
- Per question the engine uses a **subgraph**: the outcome's accounting tree, ancestors along mechanism edges (feedback only at lag ≥ 1), and the segment dimensions. Node and lag choices come from the reviewed graph (`confidence`, `sign`, `lag_days`, `tier`).
- Three separate artefacts: reviewed prior strength (YAML), measured per-brand overlay (effect, lag, stability, `n`, window, timestamp, **pre-event only**), and a question-time ranking score. The ranking score orders tests; it is never evidence.
- Test for D-16: an overlay built for an event whose window overlaps the fit window must raise an error.

## 7. Library roles

| Library | Role | Phase |
| --- | --- | --- |
| `dowhy` (+ `dowhy.gcm`) | event DAG estimation and refuters; period attribution | 2, 4 |
| `statsmodels`, `scipy`, `networkx` | tests, HAC, graph ops, BH correction | all |
| `ruptures` | onset and regimes | 4 |
| `riskloc` | ported (MIT) only if Phase 6 justifies | 6 |
| `causal-learn`, `tigramite` | offline audit only; tigramite licence (believed GPL-3) checked first | 7 |
| `pyrca` | eval cross-check, dev dependency only | 0 / 7 |
| `CausalPy` | candidate for the intervention operator (ITS / synthetic control); decide at Phase 4 | 4 |

## 8. Risks

- Big-bang deploy with no shadow run: the eval gate and the rollback tag are the only safety nets. Do not weaken the gate to meet a date.
- A deep investigation multiplies tests; D-15 and the D-2 bounds are the control. If false-attribution rises between Phase 4 and 5, Phase 5 does not ship.
- The graph encodes today's business; a brand that differs gets a wrong mechanism list. Accepted under D-6; the Phase 7 audit is the detector.
- Hidden causes (promos, creatives, stock, price) remain invisible until their adapters exist (D-10). The engine must say so, not guess.
- Deep missions change the product behaviour (minutes, streamed stages). The UI and prompt need matching changes (Phase 5), including what the user sees while it runs.

## 9. Open items needing an owner ruling

- ~~**O-1 Safety-bound defaults**~~ accepted as D-19 (2026-10-15).
- **O-2 Worktree name/path** and whether it branches from `gaurav` `bd8dfde` including the unrelated working-tree change (`rebuild.md` deleted).
- **O-3 Long-term graph delivery**: vendored copy now; artifact in the image, or MCP resource, later.
- **O-4 Who reviews `causal_priors.yaml` changes and how often** (owner is reviewer per D-6; cadence undefined).
- ~~**O-5 Deep mission UX**~~ decided as D-20 (2026-10-15): the panel is **expandable** (a row in the chat that expands to the full live view) and has a **Cancel / Stop button** that ends the investigation and returns the partial ledger.
- **O-6 tigramite licence** (GPL) before Phase 7.
- ~~**O-7**~~ closed: the owner pointed to `/opt/seleric/Seleric_Agent_Core/doc/dependency_graph` as the graph; its `explorer.html` is titled "Seleric Metric Graph" (the `C:/Users/...` copy was not reachable and is not needed).
