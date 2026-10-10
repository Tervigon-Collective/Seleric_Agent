# Seleric architecture review — 2026-10-10

**Scope.** `Seleric_Agent@gaurav` (`e469ffe`) and `Seleric_Agent_Core@main` (`6dbc821`), read in full where it
matters. Production behaviour comes from the repos' own records: `docs/OPEN_GAPS_2026-10-0{8,9}/10.md`, the
2026-09-29 audits, the 2026-10-07/08 run-failure report, and the incident comments in the code. I could not log
into the servers, so wherever I say "production", that is what it rests on.

**The ask.** Find the gaps and the architecture improvements, and challenge the theory "make what can be deterministic
deterministic, and pass the right information between steps". The goal is a reasoning system whose engines
(diagnosis above all) explain a business. A retrieval system that fetches and shows data is not enough.

---

## 1. Verdict

**The foundations are better than "vibe coded".** These are the right bones, and plenty of production systems do not have
them:

- Cube is the only source of numbers.
- One governed catalogue (v2: conformed dimensions, one id per number, `valid_for`, bindings).
- Evidence with provenance for every figure.
- Deterministic engines: diagnosis with Shapley identities, Simpson checks and DoWhy refuters; exploration with
  FDR control.
- Composition checks (`verify_compositions.py`, 28/28 exact).
- A 30-question regression suite checked against ClickHouse.

**The weakness is the middle of the system: how meaning travels between stages.** Specifically:

- **Intent is decided in about six places.** `QUERY_SPEC_MIGRATION.md` says so itself: regex windows, value
  filters, grain, breakdown fallbacks, answer checks, plus the understand call.
- **The LLM writes answers as free prose with numbers in them.** About **3,800 lines of validators** then police
  that prose with regex. When they reject it, the agent re-runs. In the 08-10 audit, 66 of 182 runs hit that loop.
- **Every incident becomes a new local guard.** The code holds 166 `Live 2026-…` incident comments,
  39 `re.compile` patterns in agent/toolsets, and 35 KB of system instructions. The repo had 119 commits since
  2026-10-01, 54 of them in one day.

A system like this converges by patching. Each patch fixes the case that was seen, the next failure shows up at
another seam, and the patches interact. Your sense that "it was fine yesterday" fits that pattern.

**Your theory is right in principle and inverted in practice:**

| Job | Who does it today | Who should |
|---|---|---|
| Decide what the user *meant* (period, entity, filter, grain) | regex, word lists, gates, plus one LLM call | **one LLM call → typed spec → code validates it** |
| Arithmetic, totals, shares, reconciliations | **the LLM, in prose**, then regex audits it | **code**, recorded as derivations |
| Words in the answer | the LLM | the LLM, filling a structure code renders |

Regex is deterministic but not *correct* for meaning. LLM arithmetic in prose is the least reliable thing
you can ask of a model. Move each job to the side that is good at it.

---

## 2. Challenging the theory

### 2.1 "More intelligence" does not mean "more agent autonomy"
Production analytics agents that publish their numbers are **constrained pipelines**:

- **Snowflake Cortex Analyst:** classification agent, then context enrichment (verified queries plus matched
  literals), then several generator agents.
- **Databricks Genie:** trusted assets and benchmarks.
- **Uber QueryGPT and Finch:** an intent agent narrows the domain, a supervisor routes to specialists, and
  metadata and aliases sit in a search index.

Anthropic's own guidance says the same thing. Use *workflows* (fixed code paths with LLM steps) when the task shape
is known. Use *agents* only where the step count cannot be known in advance.

For Seleric: about 90% of questions (lookup, breakdown, comparison, funnel, P&L bridge, why-did-X-move) have a
known shape and should run as **workflows**. Only open exploration ("what should I look at?") and multi-hop
investigations need agent-style control, and even there the loop should be a **controller written in code** with
LLM calls at the decision points, not a free tool loop.

### 2.2 Diagnosis depth is limited by what it knows, not by its maths
`causal/diagnosis.py` (2,948 lines) is sophisticated: event sizing against same-weekday references,
lineage-verified identities with exact Shapley splits, segment localisation with mix and rate effects, and DoWhy
estimation with refuters and temporal-precedence tests. What it cannot do:

| Missing | Evidence | Effect |
|---|---|---|
| **Business context** | `config/calendar_in.yaml` (Indian festivals, paydays) and `data_incidents.yaml` are read **only by forecasting**; `causal_graphs.example.yaml` and `diagnostic_ontology.yaml` are read by nothing; the `paid_media_changes` fact (campaign/budget edits) is never consulted | "Sales jumped" cannot be explained by Navratri, a budget change or a tracking outage; only by arithmetic |
| **A declared driver tree** | identities come from `formula.depends_on` plus "the data verify it"; causal candidates are ranked by lineage hub score | No business view of spend → impressions → clicks → sessions → orders → AOV → returns; drivers are guessed per run |
| **Recursion** | one composite call; localisation stops at the top segments | "Revenue fell → Meta → one campaign → its CTR collapsed after a creative change" needs diagnose → localise → re-diagnose in the segment → check the change log |
| **Data-quality gate first** | business-state freshness exists but is not a diagnosis input | A broken pixel looks like a business event |
| **A premise check in the pipeline** | `OPEN_GAPS_2026-10-08` §4: "Sales haven't been great" is never verified | Explaining a drop that did not happen |

### 2.3 "A leaner semantic layer": lean in the right dimension
Lines of YAML are not the problem. Core's catalogue is 231 generated metric files, about 13k lines, and that is
fine because it is generated. **The number of places that decide what a word means is the problem:**

```
Core:   catalogue_v2/aliases.yaml (102) · glossary/terms.yaml (1,164) · concepts/ (2,025) ·
        catalogue_v2_src/concepts.yaml (603) · value_index.py (live values) · catalogue search
Agent:  config/metric_registry.yaml (1,346, its own aliases) · understand prompt · concept resolver ·
        value-word candidates · scope.py word logic
```

That is nine vocabularies. PLAN.md §1 already found "four resolvers that disagree: 77 of 163 phrases resolve to
different ids". A lean v2 means **one vocabulary and one resolver**, with everything else generated from it.
§5 below proposes a shape.

### 2.4 There are two agent brains
Core contains `gateway/analyst.py`, "the single agent brain for the dashboard's in-page chat": an OpenAI
tool loop with its own scratchpad, LLM router and sanitisers. `Seleric_Agent` is a second brain. The same question
asked on the dashboard and in Seleric chat gets two different reasoning paths, two sets of guards, and two bug
backlogs. **Pick one.** Core should be the semantic and data service (MCP, catalogue, planner, insights);
Seleric_Agent should be the only reasoning layer, and the dashboard should call it.

---

## 3. Gaps, by system aspect (most important first)

### S1 — A typed contract between every stage
**Today.** Stages pass prose hints (`[values in the data …]`, `[follow-up]`, `[advisory plan]`) and re-read the raw
question. `QuerySpec` exists in **shadow** mode.

**Target.** One intermediate representation per stage, like a compiler (parse → IR → plan → execute → render →
verify):

```
QuestionSpec  (what is asked: measures, entities, filters incl. exclusions, windows, grain, shape, follow-up edit)
  → AnalysisPlan  (which engine/template, which queries, which derivations)
  → ResultSet     (handles to evidence tables, never ids through the LLM)
  → Claims        (each number = evidence ref or derivation ref + label)
  → Answer        (narrative with claim placeholders; code renders numbers/tables)
```

**Rules.** Each stage reads only the previous stage's object, never the raw question. Validation is a function of
the object, not of prose. Exclusions ("exclude exchange orders") become a field. Today `scope.py:42` says
"not captured". Follow-ups become **spec edits** (you started this with `apply_spec_edit`).

**Next steps.** Flip `QUERY_SPEC_MODE=enforce` per consumer and **delete** the regex paths it replaces. A
migration that never deletes the old path adds a seventh decision point.

### S2 — Answers that are correct by construction, not policed afterwards
**Today.** Generate, then audit with regex (`answer_audit.py`, `grounding.py`, `signals.py`), then revise. This
was the dominant failure in the 10-07/08 report: data fetched, answer rejected.

**Target.** The model returns `{claims: [{ref, label, role}], narrative: "Net sales fell {c1} to {c2} …"}`.
Code resolves every ref against evidence or derivations, formats the numbers, renders tables from ResultSets, and
rejects only structural problems (an unknown ref, a role mismatch). Grounding becomes a lookup, not a regex.
`QUERY_SPEC_MIGRATION.md` step 4 ("structured claims") is this; I would make it the top priority, because it
deletes the most code and most of the revision loop.

### S3 — A derivation engine, so arithmetic is never done in prose
Sums, subtotals, shares, deltas, per-day averages, reconciliations and ratio recomputation should be **declarative
ops over ResultSets**: `sum(rs, where=…)`, `share(a, of=b)`, `delta(w2, w1)`, `reconcile(parts, total)`. Code
executes them, and the output is recorded as citable evidence.

`run_python` stays as the escape hatch, not the main path. The 08 failure chain (the model is told to use
`run_python`, it refuses the finding, the model sums in prose, grounding fails) disappears by design.

### S4 — An investigation controller (the "reasoning" you want)
A small state machine in code, not a prompt:

```
1 premise      — did the stated change happen? (event vs same-weekday reference)        [engine]
2 data health  — is the data complete/fresh for the window? known incidents?            [engine]
3 decompose    — metric tree identities: which component moved? (Shapley)               [engine]
4 localise     — which segment(s)? (Adtributor/segment shift)                           [engine]
5 recurse      — re-run 3–4 inside the top segment until it stops concentrating          [controller]
6 context      — change log, calendar, promos, spend, price, stock around the event      [engine + LLM ranks]
7 drivers      — test the remaining hypotheses (DoWhy on the declared graph)            [engine]
8 stop/answer  — stop when explained share ≥ threshold or budget hit; say what is unexplained   [controller]
```

The LLM's job at steps 5–7 is to **choose and name hypotheses** and to **write the story**. The engines decide what
is true. The state (hypotheses tried, explained share, open branches) is persisted, so "dig deeper" resumes the
walk instead of restarting. `explore_data` (Oct 8) is the scan step this controller would call first for open
questions.

### S5 — A business knowledge layer (the missing component)
Three things the engines need and nobody owns:

1. **Metric tree.** Identity edges (`net_sales = gross − discounts − deductions`, `orders = sessions × CVR`) plus
   *causal* edges with expected sign and lag (`ad_spend → impressions → clicks → sessions`). Compositions now
   exist in Core (28 exact); add the causal edges and expose both as one graph. **DoWhy-GCM's
   `attribute_anomalies` and `distribution_change`** are built for exactly this: root-cause attribution on a declared
   causal graph, with Shapley contributions per mechanism.
2. **Event and annotation store.** Ad changes (already a fact), festival and payday calendar (already a file),
   promotions, price changes, stock-outs, site and tracking incidents (`data_incidents.yaml`, currently empty),
   and manual notes. Each event has a time window, scope and source.
3. **Data health per fact per day.** Freshness, completeness against the expected volume, and known gaps (e.g. the
   checkout-timing metrics PROGRESS.md marks as structurally zero).

### S6 — Evaluation as a product, not a manual ritual
**Today.** The 30-question regression is run by hand around deploys (`OPEN_GAPS_2026-10-10`). CI runs unit tests
plus a dataset-loader test. 1,000+ unit tests pass while production regresses, so they test code, not behaviour.

**Target:**
- A suite per question shape with expected values computed from ClickHouse at run time, because windows move with
  the date.
- Run on every PR touching agent/catalogue, as a **deploy gate**, plus a nightly full run.
- Replay of real production threads, which covers follow-ups too.
- Report correctness, completeness (scope covered), groundedness, revisions, latency (p50/p90), cost and
  model-fallback count per shape.
- An LLM judge only for narrative quality, calibrated against human grades.
- Keep the benchmark separate from any examples the model sees (Genie's rule), or it measures memorisation.

### S7 — Verified queries ("trusted assets"): the biggest accuracy lever you don't use
Cortex Analyst retrieves semantically similar **verified question → query pairs** as references, and attributes
much of its accuracy to them. Genie marks answers built from trusted assets as *Trusted*. You already have about
30 golden questions with checked answers.

Store them as `question → QuestionSpec → plan` and retrieve the nearest ones into the understand call as
examples. Mark answers whose plan came from a verified template, and grow the set from production threads users
confirm. That is how Snowflake's December 2025 "optimize with verified queries" closes the loop.

### S8 — Observability that survives a log rotation
**Today.** The failure report could not recover exception text (logs rotated, no Langfuse traces). Prompts,
model outputs and tool I/O per stage are not stored. The fix I pushed (`_failure_detail`) is a patch.

**Target.** OpenTelemetry GenAI spans (or Langfuse/LangSmith, you have hooks for both) for each stage: inputs,
outputs, tokens, model actually served, latency, and the spec and plan objects. Keep 30 days. A "replay
this mission" command. This is what makes S6 and every postmortem cheap.

### S9 — Change management (the real reason "yesterday was fine")
Evidence:
- Two developers and AI assistants push directly to `gaurav`, and that branch is what gets deployed.
- 54 commits in one day.
- The live checkout carries uncommitted WIP (`OPEN_GAPS_2026-10-08` §7, §11).
- Jenkins sync wipes uncommitted Cube edits.
- `compose up` has left the recovery worker missing twice.
- Rollback tags are the safety net.

**Target:**
- PRs to `gaurav`, with CI that runs unit tests plus the golden gate (S6).
- A staging stack on read-only production data.
- Feature flags for behaviour changes (`QUERY_SPEC_MODE` is the right pattern; use it everywhere).
- A post-deploy health check of all services.
- One behavioural change per deploy, measured against the eval.
- Fix *classes*: every fix names the failure class and, ideally, deletes a special case. If a fix only adds a
  guard, ask what contract would have made the bug impossible.

### S10 — LLM operations
- **Fallback across model families mid-mission.** gpt-5-mini → gpt-4o-code → grok-4-20 can change a mission's
  behaviour halfway through. Pin one model family per mission and fall back only by restarting the stage.
- **One TPM quota is shared** by understand and the agent. Give each role its own deployment (gap 9 in the 10-10
  doc), or rate limits will keep reshaping behaviour under load.
- **Structured outputs (JSON schema) for every LLM step**, not only understand.
- **A 35 KB system prompt is a smell.** Each incident adds a rule the model may or may not follow. Move rules
  into contracts and code (S1–S3) and shrink the prompt; version it, and tie each version to an eval score.
- **Prompt caching:** keep the static prefix stable (tools plus instructions) so the provider cache hits.

### S11 — Clarification and uncertainty
When the resolver returns several plausible concepts ("revenue by channel": attributed vs P&L), the system picks one,
and the validators punish the guess later. Make ambiguity an explicit outcome of the spec stage: ask one short
question, or answer with the assumption stated. Track how often you ask, so it doesn't become a crutch.

### S12 — Memory
Per thread: the spec history (S1) and the investigation state (S4). Per brand and user: preferred bases (net vs
gross, P&L vs commerce net sales; PROGRESS.md notes two different "net sales on order date"), default
windows, and the channels they care about. This removes a class of follow-up and "which number?" errors.

### S13 — Latency
The 10-10 regression measured median 55–84 s and p90 117–196 s. Most of that is revisions and LLM rounds.
S2 and S3 remove revisions, and S7 plus tiering (the 10-07 analysis) remove rounds. Don't optimise latency
separately; it follows from the architecture.

---

## 4. Fundamentals worth adopting (the "why" behind the gaps)

1. **An intermediate representation.** Compilers don't let codegen re-parse source text. Every stage consumes a
   typed object from the stage before. Most of your seam bugs are re-parsing bugs.
2. **Correct by construction beats generate-then-verify.** Verification is for what construction cannot
   guarantee. Regex verification of free text is the most expensive way to get correctness.
3. **The determinism boundary.** The LLM resolves ambiguity in language and writes language. Code does lookups,
   arithmetic, policy and state. Hold that line in both directions.
4. **Fail open or fail closed, chosen per stage.** Today an understand-call failure **adds** constraints
   (every value word becomes a required filter), so an outage makes answers stricter and wronger. Write down the
   failure policy per stage.
5. **Evals-driven development.** No behavioural change ships without an eval delta. Without that, every
   change is a guess.
6. **Identity is not cause.** Accounting identities say *what* moved, and localisation says *where*. *Why* needs a
   declared causal graph plus events plus tests. Your engine already labels these honestly; give it the inputs.
7. **Statistical hygiene in exploration.** Multiple testing, comparable windows and seasonality. You have most
   of it; extend it to every "notable" claim.
8. **Data contracts.** Facts declare grain, date axis, freshness SLA and completeness checks. Reasoning reads
   them before trusting a number.

---

## 5. Target architecture

```
                         ┌──────────────── Seleric_Agent (the only reasoning layer) ───────────────┐
 user ─► Understand ─► QuestionSpec ─► Router ─► Workflow templates (lookup, breakdown, compare, funnel, P&L bridge)
          (1 LLM call,       │           │      └► Investigation controller (premise→health→decompose→localise→recurse→context→drivers)
          + verified-query   │           │      └► Explorer (open questions; FDR-controlled scan)
          exemplars)         │           ▼
                             │      AnalysisPlan ─► Executor ─► ResultSets ─► Derivation engine ─► Claims
                             │                                                                     │
                             └──────────── follow-up edits ◄── Thread memory ◄── Answer renderer ◄─┘ (LLM writes narrative
                                                                                                    around claim refs)
                         └──────────────────────────────────────────────────────────────────────────┘
                                                    │ MCP
                         ┌──────────── Seleric_Agent_Core (semantic + data service, no agent) ──────┐
                         │ one vocabulary/resolver · catalogue (generated from Cube meta + overlay) │
                         │ metric tree (identities + causal edges) · event store · data health     │
                         │ planner → Cube → ClickHouse · provenance · verified queries store       │
                         └──────────────────────────────────────────────────────────────────────────┘
```

**Lean semantic v2: a possible shape.**
- **Physics** live in the Cube model: views, measures, joins, hierarchies, `meta` for unit, date axis and
  additivity. The catalogue's structural fields are *generated* from Cube `/meta`, as you already do partly.
- **Semantics** live in **one overlay file per business concept**: synonyms (the only vocabulary), which
  metric(s) it maps to under which axes, tree edges, `valid_for`, a description for humans and LLMs, and verified
  example questions.
- **Delete** the agent-side `metric_registry.yaml` aliases (Core already requires "no own aliases", PLAN §5),
  the separate glossary, the alias file and the v1 replacement maps once cutover is done. Keep `value_index`
  as *data* (live values), never as vocabulary.
- **CI gates.** Every synonym resolves to exactly one concept, every concept to metrics that exist, every
  composition reconciles (you have `v2_gates.py` and `verify_compositions.py`; make them blocking).

---

## 6. Sequenced roadmap (each step has an exit check)

| # | Step | Exit check |
|---|---|---|
| 0 | **Process first:** PRs + CI golden gate, staging stack, tracing (S6, S8, S9) | A regression is caught before deploy; any mission can be replayed |
| 1 | **QuestionSpec enforce + delete regex intent paths**; exclusions; follow-ups as edits (S1) | Golden suite unchanged or better; decision points ≤ 1 per field |
| 2 | **Structured claims + derivation engine** (S2, S3) | Revision rate < 5 %; validation code roughly halved |
| 3 | **One brain** (S2.4) and **one vocabulary** (§5) | The same question on dashboard and chat gives the same answer; resolver disagreements = 0 |
| 4 | **Knowledge layer:** metric tree with causal edges, event store, data health (S5) | Diagnosis cites events and data health on replayed incidents |
| 5 | **Investigation controller** with recursion + verified-query exemplars (S4, S7) | "Why" suite (planted and replayed incidents): root cause found at the right depth ≥ X % |
| 6 | Memory, clarification, latency tiers (S11–S13) | p50 / p90 targets per tier |

## 7. Stop doing
- Adding a guard per incident without asking which contract would have prevented it.
- Pushing behaviour changes straight to the deployed branch.
- Growing the system prompt as the place where fixes live.
- Letting the model do arithmetic in prose.
- Running two agents for one product.

## References
- Anthropic, *Building effective agents* (workflows vs agents; summary: https://simonwillison.net/2024/Dec/20/building-effective-agents)
- Snowflake, *Cortex Analyst: behind the scenes*: https://www.snowflake.com/en/blog/engineering/snowflake-cortex-analyst-behind-the-scenes/ ;
  verified-query optimisation: https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst/analyst-optimization
- Databricks Genie, trusted assets and benchmarks: https://docs.databricks.com/aws/en/genie/concepts
- Uber, QueryGPT: https://www.uber.com/en-AT/blog/query-gpt ; Finch: https://www.uber.com/en-GB/blog/unlocking-financial-insights-with-finch
- Wren AI (open source, semantic-layer-first GenBI): https://getwren.ai/post/why-the-semantic-layer-is-essential-for-reliable-text-to-sql-and-how-wren-ai-brings-it-to-life
- DoWhy-GCM (anomaly and distribution-change attribution on causal graphs): https://arxiv.org/pdf/2206.06821
- Exploration and diagnosis research behind `explore_data`: `docs/EXPLORATION_MODEL.md` (Tang et al. 2017, Adtributor 2014,
  QUIS 2024, InsightPilot 2023, Zhao et al. 2017, InsightBench 2025)
