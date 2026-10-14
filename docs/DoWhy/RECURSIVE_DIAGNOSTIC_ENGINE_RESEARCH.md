# Seleric Autonomous Diagnostic Engine — Research and Design

As of 2026-10-14, written against `gaurav` / `claude/laughing-tesla-66wt5o` at `454339c` (the two branches are the same commit).
This builds on `docs/DoWhy/DOWHY_DIAGNOSIS_ENGINE.md`, which describes the engine as it is. This document covers:

1. how far the current code is from the goal (a single agent that investigates any "why?" recursively),
2. how RiskLoc decides *where* to look, and where it fits,
3. what ruptures, PyRCA, tigramite and causal-learn each contribute, and what they cannot do on our data,
4. a hypothesis ledger with at least three competing hypotheses per question,
5. whether and how the loop should recurse over *when / where / what / why*,
6. how the causal graph is built, and how a conclusion is reached and defended.

Library descriptions are from the published papers and documentation as known at writing; pin versions and check
the APIs named here before coding against them.

---

## 1. Where the codebase stands against the goal

| Goal requirement | Current state (code) | Gap |
| --- | --- | --- |
| Single agent | Yes. `agent/loop.py`: one PydanticAI agent, Core MCP tools + local helpers | none |
| No fixed workflow / decision tree | The LLM chooses tools freely, but `agent/prompt.py` hard-wires "why → `escalate` + `diagnose_metric_change` in the first turn", and the tool itself runs a **fixed** 4-layer pipeline (event → decomposition → segments → drivers) | The adaptive part is one LLM choosing one big tool. Inside the tool nothing adapts to what it found |
| Recursive investigation | **No.** One call, one report. `explore_data` emits typed `FollowUp`s (`exploration/engine.py`), but nothing walks them; budgets are 10 LLM steps / 20 tool calls / 150 s (`loop.py`) and one diagnosis costs 15–25 s | No investigation state carried between steps, no child questions, no stopping rule other than budget |
| ≥ 3 hypotheses | Candidate drivers come from lineage (`_plan_drivers`, cap 8 + 2×breadth) plus user-named `drivers`. They are metrics, not hypotheses: no data-quality, mix, calendar or "no real change" alternative is stated and tested side by side | No hypothesis ledger, no competing explanations scored against each other |
| WHEN (onset) | Event window is given by the caller. `exploration/insights.py::change_point` finds the single strongest mean shift (Bonferroni t-scan) | No multi-change-point detection; the DoWhy history window can straddle a regime change (listed as a limit in §7.2 of the engine doc) |
| WHERE (localisation) | Layer 3 splits **one dimension at a time** (additive / rate mix-vs-rate / ratio split, Simpson check). `explore_data` uses Adtributor (also one dimension at a time) | No multi-dimensional root cause (e.g. *channel = Meta AND state = Karnataka*). This is what RiskLoc adds |
| WHAT (accounting) | Strong: verified identities, exact Shapley, additive bridge, rate chains | none worth noting |
| WHY (causal) | DoWhy backdoor OLS on a **declared** star DAG per driver, HAC/HC3 interval, five refuters, Granger-style direction tests, rule-based labels | The graph is assumed, not discovered; drivers are estimated one at a time against the outcome; no multi-hop chain (A → B → outcome); no latent-confounder handling; drivers only for windows ≤ 7 days |
| Libraries asked for | `pyproject.toml` has `dowhy`, `statsmodels`, `scipy`, `numpy`, `pandas`. **No** ruptures, PyRCA, tigramite, causal-learn, RiskLoc anywhere in `src/` | all to add |
| Explainable, quantified | Strong: grounding check on every figure (`agent/grounding.py`), evidence rows per metric-day, `CausalArtifact` tiers | keep |

Summary: the parts that measure and decompose a change are sound. What is missing is the investigation around them:
the engine answers one question in one pass, with one assumed graph, and cannot follow up on its own findings.
The right move is to keep layers 1–3 and the DoWhy boundary and add (a) an investigation state, (b) discovery and
localisation operators, and (c) a bounded recursion that the agent drives.

---

## 2. RiskLoc: how it decides where to look

**Paper:** M. Kalander, *RiskLoc: Localization of Multi-dimensional Root Causes by Weighted Risk* (2022/23,
arXiv 2205.10004). The reference implementation (repo `shaido987/riskloc`) also ships Squeeze, Adtributor, HotSpot,
AutoRoot and R-Adtributor implementations, which is useful for benchmarking against our own layer 3.

### 2.1 The problem it solves

An additive KPI (sales, orders, spend) is the sum over **leaf elements**: every combination of dimension values,
e.g. `(channel, campaign, state, device)`. When the total deviates from its forecast, find the smallest set of
**elements** (a leaf or a coarser combination like `channel=Meta ∧ state=KA`, with the other dimensions free)
that explains the deviation. With *d* dimensions there are ∏(|values|+1) candidate elements, so the search has to
be pruned.

### 2.2 The algorithm (at the level needed to adapt it — check exact formulas in the paper)

1. **Deviation per leaf.** Each leaf has an actual *v* and a forecast *f*. RiskLoc uses a relative deviation score
   (the Squeeze-style `d = 2(f − v)/(f + v)`), so small and large leaves are comparable.
2. **Two-way partition: normal vs abnormal leaves.** Normal leaves are assumed to scatter around zero. The cutoff is
   taken from the spread on the *non-anomalous side* (the most extreme deviation in the opposite direction to the
   anomaly). Leaves past the cutoff in the anomaly's direction are abnormal. No threshold is tuned per dataset.
3. **Weighting.** Abnormal leaves are weighted by how far past the cutoff they lie, so a leaf barely over the
   line counts less than one that collapsed. This handles the case Squeeze/HotSpot get wrong: root-cause leaves
   with very different magnitudes.
4. **Risk score per element.** For an element *e* (a set of leaves), the score combines
   - a **purity / risk-ratio** term: how much of *e*'s (weighted) leaf mass is abnormal compared with the leaves
     outside *e*, and
   - a **coverage** term: how much of the total abnormal mass *e* captures.
   An element made entirely of abnormal leaves that also covers most of the abnormal mass scores highest. A broad
   element (all of `channel=Meta`) that mixes normal and abnormal leaves scores lower than the narrower element
   that is purely abnormal.
5. **Layered search with explanatory-power stop.** Search cuboids from 1 dimension upward. In each layer, rank
   elements by risk score; accept the best element when its **explanatory power** (its share of the total
   deviation `Σ(f−v)` over the element ÷ total) passes a threshold. Stop at the first layer that yields one, so
   the answer is the coarsest explanation, not an over-fitted leaf list. Pruning by element support keeps the
   search tractable.

**Why this is "deciding where to look":** the partition (step 2) says *which leaves are broken* without any
per-dataset threshold; the risk score (step 4) says *which description of those leaves is the most specific
and complete*; the layer order (step 5) says *how fine to go*. It turns "where" into a ranked, quantified answer
with a share of the change attached.

### 2.3 How it fits Seleric

| RiskLoc needs | Seleric already has |
| --- | --- |
| Forecast *f* per leaf | `Referencer` in `causal/diagnosis.py`: level-adjusted same-weekday reference per series. Run it per leaf |
| Additive KPI | The catalogue knows aggregation per metric (`MetricMeta`) |
| Leaf table | `metrics_query` with several dimensions at `grain=day`; today's segment cap is 6,000 rows (`toolsets/diagnosis.py`) |

Limits to design around:

- **Additive only.** For a ratio (ROAS, CVR, AOV) run RiskLoc separately on numerator and denominator, then use
  the existing exact ratio split (`_analyse_ratio_dimension`) on the elements it returns. Do not run it on the
  ratio directly.
- **Leaf explosion and sparsity.** Daily leaves for 4–5 dimensions are mostly zeros. Aggregate the event window to
  one value per leaf (sum over event days vs sum over references), and restrict dimensions to the ones the
  existing `space.plan_dimensions` ranks highest (hierarchy first). Fetch leaves for 2–3 dimensions at a time, not
  all of them.
- **Snapshot attributes** (campaign status, the open item in §7.3 of the engine doc) give fake localisations
  here too. They must be excluded before RiskLoc sees them.
- **Noise calibration.** Our layer 3 already flags segments against their own noise; use the same per-leaf noise to
  sanity-check the RiskLoc cutoff on low-volume leaves.

Recommendation: implement RiskLoc as a pure function `localise_multidim(leaves) -> [Element]` beside layer 3
(around 200 lines; the algorithm is simple and porting it avoids a dependency on research code with unclear
packaging). Keep layer 3 as the one-dimension view and use RiskLoc as the WHERE operator when one dimension is not
`localised`.

---

## 3. The libraries: what each one is for

| Library | Licence | Use in Seleric | Do not use it for |
| --- | --- | --- | --- |
| **ruptures** | BSD-2 | WHEN: change points in outcome and driver series | causal claims |
| **tigramite** | **GPL-3.0** | WHY: time-series causal discovery (PCMCI / PCMCI+ / LPCMCI) | short windows with many variables |
| **causal-learn** | MIT | WHY: discovery on panel / cross-sectional data (PC, FCI, GES, LiNGAM) with background knowledge | raw autocorrelated daily series |
| **PyRCA** | BSD-3 | WHY-chain: root-cause ranking on a graph (random walk, hypothesis-testing/CIRCA-style, ε-Diagnosis, Bayesian) | graph discovery on our data (its builders wrap the above) |
| **DoWhy** (already in) | MIT | Identification, estimation, refuters; **`dowhy.gcm`** for anomaly and distribution-change attribution | discovery |

**Licence note:** tigramite is GPL-3.0. Running it inside our own service is not distribution, but if the agent
image is shipped to customers or on-prem, legal needs to review it. The repo has `LICENSE_PLACEHOLDER.md`, so this
has not been settled yet. causal-learn provides a non-GPL alternative (PC with a time-lag design, Granger) if
tigramite is ruled out.

### 3.1 ruptures — WHEN

- Algorithms: `Pelt` (exact, penalised, O(n) average), `Binseg`, `BottomUp`, `Window`, `Dynp` (exact with known
  number of breaks). Cost models: `l2` (mean shift), `l1` (robust), `normal` (mean + variance), `rbf` (kernel, any
  distribution change), `ar` (autoregressive), `linear`.
- **Use 1: event window.** Run `Pelt(model="l1" or "rbf")` on the day-of-week-adjusted outcome (we already
  remove weekday effects in `insights.change_point`). The last break before "now" is the onset. This replaces a
  user-guessed window and decides *short event vs regime shift*: a level that stepped and stayed is a regime
  change (period mode); a spike that returned is an event (driver mode).
- **Use 2: clean estimation windows.** Fit DoWhy only on history inside the current regime, or add a regime dummy.
  This removes the "effect stable over 56 days" assumption the engine doc lists as a limit.
- **Use 3: temporal precedence across metrics.** If driver X has a break at *t₁* and the outcome at *t₂ ≥ t₁* (within
  a plausible lag), that is evidence for the `intervention` direction label, with fewer assumptions than the
  current "≥ 2 abrupt jumps" rule. A break in Y *before* X is evidence against X.
- **Penalty choice.** Penalty ≈ `σ² · log(n)` (BIC-like) on the robust-scaled series. With 56 days, cap at 3
  breaks and require a minimum segment of 5–7 days. Report break dates with a bootstrap or a simple
  before/after test (the existing Bonferroni t-scan can serve as the significance check on each break).

### 3.2 tigramite — WHY (graph discovery on time series)

- **PCMCI** (Runge et al., *Science Advances* 2019): PC-style condition selection per variable (`PC₁`), then a
  momentary conditional independence test (MCI) that conditions on the parents of both ends. It controls false
  positives under autocorrelation, which plain Granger or PC on raw series do not.
- **PCMCI+** (Runge 2020) adds **contemporaneous** links (X[t] → Y[t]), which daily business data needs: spend and
  clicks move the same day.
- **LPCMCI** allows **latent confounders** and returns a time-series PAG (some edges left undirected or marked
  "possibly confounded").
- Conditional-independence tests: `ParCorr` (linear partial correlation; the right default at our sample size),
  `RobustParCorr` (rank-transformed, heavy tails), `GPDC` / `CMIknn` (non-linear, need far more data).
- **Background knowledge**: `link_assumptions` lets us forbid or require links per (variable, lag). This is where
  the catalogue enters (§6.1).

**Sample size is the main constraint.** PCMCI+ with *N* variables and `tau_max` lags tests on the order of
*N² · tau_max* links. With 56 days, 10 variables and `tau_max = 7` there is almost no power, and many false
negatives. To make it usable:

- raise history for discovery to 180–365 days (fetching is batched and cached already; the cost is one bigger
  query per view);
- keep *N* ≤ 8–10 by screening first (existing co-measure exclusions + lineage);
- `tau_max` 2–3 for daily data, with one weekly lag (7) only if the catalogue says the effect is weekly;
- run it on de-seasonalised residuals (the engine's calendar fit), not on raw series;
- **pool across segments** (§3.3) when the per-segment series share a structure.

### 3.3 causal-learn — WHY (panel and cross-sectional discovery)

- `pc` (with `fisherz`, `kci`, ...), `fci` (latent confounders → PAG), `ges` (score-based), `lingam` variants
  (identifies direction with non-Gaussian noise), Granger tools, and `BackgroundKnowledge` (forbidden/required
  edges, tiers).
- **Where it beats tigramite for us:** the data we have in quantity is the **panel**: campaigns × days, or products
  × days, which gives thousands of rows instead of 56. After removing per-unit means (fixed effects) and calendar
  effects, rows are close to exchangeable, and PC/FCI with `fisherz` has real power. Example: 120 campaigns × 56
  days ≈ 6,700 rows to learn the spend → impressions → clicks → purchases → revenue structure, which the
  account-level series alone cannot support.
- Use **FCI** rather than PC as the default: we have unmeasured confounders by construction (promotions, price
  changes, competitor activity), and FCI says so in its output (`o→`, `↔`) instead of drawing an edge that may not
  be causal.
- Use **tiers** from the funnel order (§6.1), not just pairwise forbids.

### 3.4 PyRCA — ranking root causes on a graph

PyRCA (Salesforce, built for AIOps metric graphs) has two halves:

- **Graph builders**: PC, GES, LiNGAM, FCI and Granger wrappers (largely over causal-learn), plus a
  domain-knowledge config with root nodes, leaf nodes, forbidden and required links.
- **RCA analyzers**: `RandomWalk` (walk backwards from the anomalous node along the graph, weighted by
  correlation), `HT` (regression-based hypothesis testing in the style of CIRCA, Li et al. KDD 2022: fit each node
  on its parents on normal data, then score how anomalous each node's *residual* is at the event; the node whose own
  mechanism broke is the root), `EpsilonDiagnosis` (two-sample test per metric), `BayesianInference`, `RCD`
  (root-cause discovery by intervention detection, no prior graph).

**What to take:** the **HT / CIRCA** idea is the most valuable piece, because it directly answers "which node's
mechanism changed" rather than "which node correlates". It is also what `dowhy.gcm.attribute_anomalies` does
with Shapley values over noise terms, and DoWhy is already a dependency. Recommended order:

1. use `dowhy.gcm` (structural causal model on the learned DAG; `attribute_anomalies` for short events,
   `distribution_change` for period comparisons, which removes the "no drivers for windows over 7 days" limit);
2. use PyRCA's `HT` and `RandomWalk` as a second opinion and benchmark; agreement between two methods raises
   confidence, disagreement becomes a stated uncertainty;
3. check PyRCA's maintenance status before depending on it in production; it can stay a dev/eval dependency if
   needed.

---

## 4. Hypotheses: at least three, from fixed families

LLMs anchor on the first plausible story. To force real alternatives, every investigation opens a **hypothesis
ledger** with at least one entry from each of these families. Which families apply is decided by the catalogue
and the data, not by a fixed list of metric names.

| Family | Example statement | Falsifiable prediction | Operator that tests it |
| --- | --- | --- | --- |
| H0 **No real change** | "Sales are within normal variation" | \|z\| < 1.5 vs reference; no ruptures break | Layer 1 (exists) |
| H1 **Measurement / data** | "Tracking, a late pipeline or a definition change, not the business" | Two systems that count the same events diverge (order ledger vs web tracker); freshness warnings; break in a co-measure only | Co-measure comparison (exists as an exclusion rule; promote it to a test), freshness from MCP warnings |
| H2 **Mix / composition** | "Total fell because the mix moved toward a low-AOV channel" | Mix effect > rate effect; Simpson flag | Layer 3 rate split (exists) |
| H3 **Localised break** | "One segment (or combination) broke" | RiskLoc element with explanatory power ≥ threshold | RiskLoc (new) |
| H4 **Upstream lever** | "Ad spend was cut, fewer clicks, fewer orders" | Lever moved before the outcome (ruptures); a directed path exists in the discovered graph; GCM attribution assigns the anomaly to it | ruptures + discovery + DoWhy/GCM (new / extended) |
| H5 **Funnel-stage rate** | "Traffic held, conversion fell" | Identity split puts the change on a rate, not on a volume | Layer 2 rate chain (exists) |
| H6 **Calendar / external** | "Festival week last year / payday / weather" | The same weekday last year or a known calendar event explains the gap; no internal lever moved | Calendar fit (exists in part); change log (exists as text only) |

Each ledger entry carries:

```text
id, family, statement, scope (metric, segment, window),
predictions[]            # what must be true if it holds
tests[]                  # operator, args, result id, verdict (supports / contradicts / inconclusive)
evidence_label           # supported_cause | likely_contributor | correlation | located_only | ruled_out | untested
explained_share          # quantified share of the change, with interval
status                   # open | supported | ruled_out | superseded | merged
children[]               # sub-hypotheses spawned (recursion)
```

Rules:

- At least **three non-null** hypotheses from different families must be opened before any is accepted; H0 and
  H1 are always opened and must be explicitly ruled out before a business cause is stated.
- A hypothesis is accepted only if its predictions passed **and** the competing ones that make overlapping
  predictions were tested. This is where contradictions are found: two hypotheses both "supported" with
  shares adding to more than 100% means the shares overlap (linked drivers, which the engine doc already warns
  about), and the graph has to separate them (§6.3).
- The LLM proposes statements and predictions; the **operators** produce the verdicts. The LLM never sets a verdict.

---

## 5. Should it recurse? Yes, bounded, over WHEN → WHERE → WHAT → WHY

### 5.1 The operators

| Question | Operator (deterministic, pure over fetched data) | Exists? |
| --- | --- | --- |
| **IS IT REAL** | event z vs reference; premise check | yes (layer 1) |
| **WHEN** | ruptures on outcome; onset; event vs regime | new |
| **WHAT** | identities, Shapley, bridge, rate chains | yes (layer 2) |
| **WHERE** | layer 3 one-dimension split; RiskLoc multi-dimension | layer 3 yes, RiskLoc new |
| **WHY** | discover graph (tigramite / causal-learn, catalogue priors) → estimate (DoWhy) → attribute (GCM / PyRCA HT) → refute | estimate + refute exist; discovery and attribution new |
| **CONTEXT** | change log, freshness, calendar events | partial |

Today all of these run in a fixed order inside one tool call. The change is to expose them as **separate,
typed operators over one shared investigation state**, so the order depends on what was found.

### 5.2 The loop

```mermaid
flowchart TD
    Q["Why-question"] --> R["IS IT REAL? (layer 1)"]
    R -- "not unusual" --> STOP0["Answer: no real change (H0)"]
    R -- "unusual" --> L["Open ledger: H0..H6, at least 3 non-null"]
    L --> P["Pick next test: the open hypothesis with the highest<br/>expected information per second (cost-aware)"]
    P --> OP{"Operator"}
    OP --> W1["WHEN: ruptures"]
    OP --> W2["WHAT: identity split"]
    OP --> W3["WHERE: layer 3 / RiskLoc"]
    OP --> W4["WHY: graph, estimate, attribute, refute"]
    W1 & W2 & W3 & W4 --> U["Update ledger: verdicts, shares, contradictions"]
    U --> C{"Child question?"}
    C -- "localised to segment S" --> SUB["Recurse: same question scoped to S<br/>(depth + 1)"]
    C -- "change sits on component M" --> SUB2["Recurse: why did M change?<br/>(depth + 1)"]
    C -- "no" --> S{"Stop?"}
    SUB & SUB2 --> R
    S -- "explained share ≥ 70%, competitors ruled out" --> A["Conclude: causal chain"]
    S -- "budget or no testable hypothesis left" --> B["Conclude: located / insufficient evidence,<br/>name the missing data"]
    S -- "otherwise" --> P
```

**Recursion happens in two directions**, and both are already latent in the engine output:

- **Down the WHERE axis:** layer 3 / RiskLoc says "the drop is in `channel=Meta ∧ state=KA`". The child question is the
  same "why" with that scope as `filters`. In the child, the levers of that segment (its spend, its campaigns, its
  change log) become the candidates. This is the "why often stops at where" open item.
- **Down the WHAT axis:** the identity says "revenue fell because orders fell, AOV held". The child question is
  "why did orders fall", whose identity may say "sessions held, conversion fell", whose child is "why did
  conversion fall" (checkout errors, a price change, a stock-out). Each step moves one level up the funnel.

### 5.3 Who chooses the next step

The goal says "no predefined workflows". A fully free LLM choice is not reliable enough on our model stack
(`gpt-5-mini` at low effort per `agent/model.py`), so split the choice:

- **The LLM** opens hypotheses (statements + predictions, from the families), names user-suggested suspects, and
  writes the final explanation.
- **A deterministic scheduler** picks the next operator among those whose preconditions hold (e.g. RiskLoc only for
  an additive metric with ≥ 2 dimensions; discovery only with ≥ 120 rows), ranked by
  `expected_information / estimated_cost`, where expected information is high for a hypothesis that is open,
  untested, and whose test would rule out the most competitors.

So no sequence is fixed: the sequence depends on the ledger state. But every step is a governed, reproducible
operator, and the trace shows why each step was taken.

### 5.4 Bounds

| Bound | Value to start with | Reason |
| --- | --- | --- |
| Depth | 3 | revenue → orders → conversion → (one more) covers the funnel; deeper is usually noise at daily grain |
| Branching | top 2 children per node | the third explanation rarely changes the action |
| Operator calls | ~25 per investigation | |
| Wall time | interactive ≤ 60 s; "deep" mode as an async mission | the current 150 s turn budget cannot hold a 3-deep tree at 15–25 s per diagnosis; `api/async_missions.py` (lease + heartbeat, `recovery` container) already supports long runs |
| Data reuse | shared per-investigation query cache (exists per turn) | children re-read the same views with an extra filter |

A memoised fetch layer (the existing `_prefetch_daily` batching and cache) matters more than any algorithm here:
children mostly re-read the parent's views with one more filter.

---

## 6. How the causal graph is formed

The current engine **declares** one star DAG per driver (calendar, lags → X, Y; X → Y) and unions them for the UI
(`graph_chart.py`). That is safe, because all adjustment variables are pre-treatment, but it cannot represent
A → B → Y, cannot say which of two linked drivers is upstream, and cannot express doubt about a link. The proposed
graph is built in five steps, each of which shrinks what the data has to decide.

### 6.1 Step 1: structure from the catalogue (no data)

| Catalogue fact | Graph consequence |
| --- | --- |
| Verified identity `Y = A · B` or `Y = Σ parts` (layer 2) | **Deterministic edges** A → Y, B → Y, kept **out of** the CI tests. Deterministic relations break faithfulness: PC/PCMCI would delete or misorient other edges around them. Treat Y as a function node and discover on A, B and their causes instead |
| `depends_on`, rate chains | Required or tiered edges |
| Funnel order (spend → impressions → clicks → sessions → add-to-cart → orders → revenue → returns) | **Tiers**: no edge from a later tier to an earlier one at lag 0. This is the "causal order derivable from lineage" in the engine doc's open questions; it can be derived from unit tokens + `depends_on` instead of hand-coding names, keeping the no-hardcoding rule |
| Co-measures (two systems counting the same events) | Merge into one node, or mark as measurement of a latent node; never an edge |
| Date basis (order date vs booked date) | Variables on different bases get a lag constraint, not a lag-0 edge |
| Levers (spend, budget, price, discount: things a person sets) | Root nodes: no incoming edges except from calendar and their own past |

This gives `link_assumptions` for tigramite or `BackgroundKnowledge` for causal-learn.

### 6.2 Step 2: discovery on data

- **Account-level time series:** PCMCI+ (`ParCorr`, `tau_max` 2–3, `pc_alpha` chosen by the built-in AIC
  option, α = 0.01 for MCI), on calendar-residualised series, 180+ days, with Step 1 constraints. Use LPCMCI if
  the PAG output can be handled.
- **Panel (campaign × day, product × day):** FCI from causal-learn with Fisher-z on within-unit-demeaned,
  calendar-residualised rows, with the same tiers. This is where most of the statistical power is.
- **Stability:** repeat on 50–100 block bootstraps (blocks of 7 days, so weekly structure is kept) and keep edges
  that appear in ≥ 60% of runs. The edge frequency becomes the edge weight shown in `CausalGraphPanel`.

### 6.3 Step 3: reconcile into one graph and a set of competing graphs

- Edges both methods find with the same orientation → **confirmed**.
- Edges with undetermined orientation (PAG `o-o`, `o→`) or below the stability threshold → **ambiguous**. Each
  plausible orientation that changes the conclusion becomes a **graph hypothesis** in the ledger (e.g. "G₁: spend →
  clicks → orders" vs "G₂: a promotion drives both spend and orders"). The conclusion must hold in all of them, or
  the answer says it depends on which one is true.
- `↔` (latent confounding) between X and Y → X → Y can only reach `likely_contributor`, never `supported_cause`,
  unless an intervention (a ruptures break in X with no break in other parents, or a change-log entry) supplies
  the identification.

### 6.4 Step 4: estimate and attribute on the graph

- **Effects:** DoWhy `identify_effect` on the reconciled DAG now returns a *real* adjustment set (parents, not "every
  other metric lagged"), and can also give front-door or mediation estimands where the graph allows. Keep the
  existing HAC/HC3 interval and the five refuters; add `dowhy` graph refutation (`refute_graph` / implied
  conditional independence checks) so the graph itself is tested against the data.
- **Attribution of this event:** fit a `dowhy.gcm` structural causal model on normal days (linear / additive-noise
  mechanisms at this sample size), then
  - short event: `gcm.attribute_anomalies(target=outcome, anomaly_samples=event_days)`: Shapley split of the
    outcome's anomaly over the **noise terms** of its ancestors, so the root node whose own mechanism broke gets the
    share, not the nodes that only passed it along;
  - period comparison: `gcm.distribution_change(old=reference_period, new=event_period)`: which node's
    *mechanism* changed between the periods. This removes the current "no drivers for windows over 7 days" limit.
- **Second opinion:** PyRCA `HT` on the same graph. Agreement → stated; disagreement → reported as uncertainty.

### 6.5 Step 5: refute the conclusion, not just the estimate

- Placebo event: run the same attribution on a normal day; it should find nothing.
- Graph perturbation: drop each ambiguous edge; the top root cause should not change.
- Hold-out segment: a cause found at account level should also show in the segments the localisation says were
  affected, and **not** in those that were not. This is a strong check because it uses the WHERE result against
  the WHY result.

---

## 7. How a conclusion is reached and defended

A conclusion is a **chain**, and every link has its own evidence standard (keeping the engine's rule that an
accounting split is never presented as a cause). Illustrative example; the figures are invented:

```text
[IS IT REAL]  sales -18.4% vs usual, z = -3.1                         statistical
[WHEN]        onset 2026-10-09, level step (not a spike)               ruptures break, p < 0.01
[WHAT]        orders -17.9%, AOV -0.6%                                 exact identity, Shapley
[WHERE]       Meta ∧ Karnataka carries 81% of the drop                  RiskLoc, EP 0.81
[WHY]         Meta spend in Karnataka cut 52% on 10-08 (change log)    intervention + GCM attribution 0.74 [0.55, 0.88]
              → clicks → orders                                        graph path, stable in 87% of bootstraps
[RULED OUT]   H1 tracking (ledger and tracker agree), H6 calendar      tests t3, t5
[UNEXPLAINED] 19%
```

**Acceptance rule (proposal):**

| Verdict | Condition |
| --- | --- |
| `explained` | A path from a root node to the outcome where every edge is confirmed or holds in all graph hypotheses; GCM attribution share ≥ 50% with CI excluding 0; direction from an intervention or a ruptures break that precedes the outcome; H0 and H1 ruled out; the hold-out-segment check passes |
| `partially_explained` | As above with share 20–50%, or one ambiguous edge on the path |
| `located_cause_not_identified` | WHEN/WHAT/WHERE are solid but no root node clears the bar. Name the segment and the data that would decide it (e.g. "no campaign-level change log for Karnataka") |
| `insufficient_evidence` | Fewer rows than discovery needs, partial days, or H1 (data issue) cannot be ruled out |

The existing labels (`supported_cause`, `likely_contributor`, `correlation`, `insufficient_evidence`) stay per
edge; the verdict is about the chain. The grounding check (`agent/grounding.py`) keeps working unchanged if each
operator registers its figures in the `Finding.metrics` dictionary like `_finding_metrics` does today.

---

## 8. Data realities that decide what is feasible

| Fact | Consequence |
| --- | --- |
| 56 days of daily history per run today | Enough for layers 1–3 and DoWhy with few covariates; **not** enough for PCMCI+ on 10 variables. Fetch 180–365 days for discovery, and cache the discovered graph per (brand, metric family) for a week instead of re-discovering per question |
| Panel data exists (campaigns, products, states) | Use it for discovery (§3.3); this is the main way to get power |
| Unmeasured promotions, price changes, competitor moves | Prefer FCI / LPCMCI; never let a `↔` edge become `supported_cause` without an intervention |
| Change log exists but only as text lines (`_change_log_lines`) | Make it structured intervention evidence: a dated lever change is the best identification we have, better than any discovery algorithm |
| Lags: ad effects spread over days | `tau_max` 2–3 plus an adstock-transformed spend variable as a candidate node (open question 2 in the engine doc) |
| ~10 drivers tested per run at α = 0.05 | Add Benjamini–Hochberg across the ledger's tests, as `explore_data` already does |

---

## 9. Implementation plan

Order is by value per effort; each step is usable on its own.

| # | Step | Files | Notes |
| --- | --- | --- | --- |
| 1 | **Investigation state + ledger** (dataclasses, persisted per mission in `v3_artifacts`) | new `causal/investigation.py` | Nothing else recurses without it |
| 2 | **Split `_diagnose` into operators** (`is_real`, `decompose`, `localise`, `drivers`) sharing the fetch cache; keep `diagnose_metric_change` as a wrapper that runs them all | `toolsets/diagnosis.py`, `causal/diagnosis.py` | No behaviour change; regression set must match |
| 3 | **ruptures WHEN operator** + regime-aware DoWhy history | `causal/when.py`, `dowhy_service.py` | Small; fixes a listed limit |
| 4 | **RiskLoc WHERE operator** (ported, pure) | `causal/riskloc.py` | Benchmark against layer 3 and the riskloc repo's synthetic sets |
| 5 | **Catalogue → graph priors** (tiers, deterministic nodes, co-measure merge) | `causal/priors.py` | Pure function of lineage; unit-testable; keeps the no-hardcoding test |
| 6 | **Discovery**: FCI on panel (causal-learn), PCMCI+ on account series (tigramite, if licence OK), bootstrap stability, graph cache | `causal/discovery.py` | Run offline/nightly per brand first; on-demand later |
| 7 | **GCM attribution** (`attribute_anomalies`, `distribution_change`) on the cached graph; PyRCA HT as an eval-only cross-check | `causal/attribution.py` | Lifts the 7-day limit on drivers |
| 8 | **Scheduler + recursion** (preconditions, info/cost ranking, depth/branch/time bounds), exposed as an `investigate` tool; interactive and async "deep" modes | `causal/scheduler.py`, `agent/local_tools.py`, `agent/prompt.py` | Prompt rule changes from "call diagnose" to "open ≥ 3 hypotheses, call investigate" |
| 9 | **Evaluation**: synthetic worlds with a known root cause (planted spend cut in one segment, planted tracking break, planted mix shift, pure noise), plus the existing `eval/regress` sets | `tests/unit/causal/`, `eval/` | Measure precision of `explained` and how often H1/H0 are wrongly rejected |

Dependencies to add: `ruptures`, `causal-learn`, `tigramite` (after licence review), `pyrca` (dev/eval).
Run discovery and attribution in the existing forkserver pool (`causal/estimate_pool.py`) so they do not block
the event loop.

---

## 10. Short answers to the questions asked

- **RiskLoc, how it decides where to look:** relative deviation per leaf → two-way normal/abnormal split with a
  cutoff taken from the noise on the non-anomalous side → abnormal leaves weighted by distance past the cutoff →
  a risk score per element combining purity and coverage → layered search from coarse to fine, stopping at the
  first element whose explanatory power passes the threshold. In Seleric it is the multi-dimension WHERE
  operator, fed by the existing per-series reference, run on numerator and denominator for ratios.
- **PyRCA, ruptures, tigramite, causal-learn:** ruptures = WHEN (and regime-safe estimation, temporal precedence);
  tigramite = discovery on time series (PCMCI+, LPCMCI); causal-learn = discovery on the campaign/product panel
  (FCI with tiers); PyRCA = root-cause ranking on the graph (HT/CIRCA, random walk), best as a cross-check next to
  `dowhy.gcm` attribution. None is in the codebase today.
- **At least 3 hypotheses:** a ledger opened from seven families (no change, data issue, mix, localised break,
  upstream lever, funnel rate, calendar/external); ≥ 3 non-null, H0 and H1 always tested; the LLM writes
  hypotheses, operators decide verdicts.
- **Recursive loop:** yes, bounded (depth 3, 2 children, ~25 operators), recursing down WHERE (scope to the
  located segment) and down WHAT (move up the funnel to the component that moved). The next step is chosen from the
  ledger state, not a fixed order. A 3-deep tree does not fit the current 150 s turn, so it needs an async
  "deep investigation" mode on the existing mission queue.
- **Causal graph and conclusion:** catalogue priors (identities as deterministic nodes, funnel tiers, co-measure
  merges, levers as roots) → discovery (FCI on panel, PCMCI+ on series) with bootstrap stability → reconciled DAG
  plus competing graph hypotheses for ambiguous edges → DoWhy estimation + GCM anomaly / distribution-change
  attribution → refutation of the conclusion (placebo event, graph perturbation, hold-out segments). The
  conclusion is a WHEN → WHAT → WHERE → WHY chain with an evidence standard per link, alternatives ruled out, and
  the unexplained share stated.
