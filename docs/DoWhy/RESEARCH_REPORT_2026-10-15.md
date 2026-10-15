# Diagnosis engine rebuild: research report (pre-plan)

2026-10-15. Purpose: settle what the module is *for* and what a lean, accurate version looks like, before any plan is written. Nothing here is built. Items marked **[recommendation]** are my judgement and are for the interview to confirm or overturn. Items marked **[unverified]** I could not confirm from a primary source.

Inputs read: `docs/DoWhy/DOWHY_DIAGNOSIS_ENGINE.md` (current engine, HLD/LLD), `docs/DoWhy/deep-research-report.md` (external report), `docs/DoWhy/rebuild.md` as of commit `bd8dfde` (the larger rebuild design, deleted in the working tree, last 65 lines only survive in git), Agent Core commit `e37eecc` (dependency graph), `catalogue_v2_src/causal_priors.yaml`, `doc/dependency_graph/*`, `docs/EXPLORATION_MODEL.md`, plus the web sources listed at the end.

`Seleric Metric Graph.html` is on a Windows path I cannot open from this machine. I used the committed `doc/dependency_graph/explorer.html` + `dependency_graph.json` instead. **Please confirm they are the same graph.**

---

## 1. What this module is actually for

> Given "metric X moved on window W (for scope S)", return the smallest set of explanations that the data support, each tagged with *how strongly* it is supported, plus an honest statement of what is still unexplained.

Three properties matter more than any feature list:

1. **Correct root cause when it is findable.** Measured as: on events where the cause is known, how often is it ranked first / in the top 3.
2. **Silence or "not identified" when it is not.** Measured as: false-attribution rate on pure noise, on causes hidden from the catalogue, and on false-premise questions. A wrong confident cause is worse than "located, cause not identified".
3. **Depth when asked.** Follow the chain WHEN → WHAT → WHERE → WHY far enough that the answer is actionable (campaign / segment / lever), not stop at "conversion fell".

Everything in the 2026-10-08/10 post-mortems (TH-383, sign inversion, near-tautology "cause", window pin) is a failure of property 2 or a depth failure, not a missing algorithm.

## 2. What exists today (verified in code and docs)

- A four-layer engine: event reality (robust z vs level-adjusted same-weekday reference) → exact decomposition (verified identities, Shapley, additive bridge) → localisation (single-dimension splits, mix vs rate, Simpson check) → drivers (DoWhy backdoor regression on a hand-built time-series DAG, HAC/HC3, five refuters, direction tests). ~4.9k lines in `causal/`, `exploration/`.
- DoWhy's real contribution is small: the stated graph, identification bookkeeping, two of five refuters. The load-bearing safeguards are engine-side (pre-treatment-only adjustment, placebos, direction tests, did-it-move gate, co-measure exclusions).
- **Candidate drivers are chosen by lineage heuristics** (`_plan_drivers`: hub score, shared dimensions, round-robin across views) and **co-measures are rejected by correlation thresholds** (r ≥ 0.95, CV < 2%). Nothing consumes the new dependency graph: `grep dependency_graph src/ config/` returns nothing.
- Anomaly / "is it unusual" logic exists in at least four separate places: `services/business_state/detectors.py` (median/MAD), `causal/diagnosis.py` layer 1, `exploration/insights.py`, `forecasting/quality.py` (robust z > 6). Same idea, different thresholds, no shared module. None is multi-dimensional.
- Drivers only for events ≤ 7 days; period comparisons stop at layers 2–3. Estimation sample ≈ 55 rows. Change log is narrative text only, not evidence.

## 3. What the research says

### 3.1 Learned causal graphs are not reliable enough to be the foundation (strong evidence, wrong domain)

RCAEval / ASE'24 ("How far are we?", Pham et al.) evaluated 9 causal-discovery methods and 21 RCA methods on microservice failures:
- Discovery F1 with defaults ranged ~0.1–0.54; edge *orientation* was the weak point; accuracy fell sharply from 10 to 50 nodes; runtime grew 7× to thousands of times.
- Many causal-graph RCA methods (PC, FCI, Granger, LiNGAM, fGES + PageRank/random walk) scored **about the same as a random baseline**. BARO and NSigma, which use no graph at all (robust statistical tests per metric), were among the best *and* fastest.
- Rankings on synthetic data did not predict real-system rankings.
- Authors recommend reducing graph size with **domain knowledge** and evaluating with a real anomaly detector, not given failure times.
- PCMCI accuracy *declined* with more data in their runs; PCMCI+ assumes causal stationarity and no hidden variables (tigramite docs).

Caveat: microservice telemetry, not e-commerce. The transfer is plausible (small N, autocorrelation, hidden confounders are worse for us: promotions, creatives, competitors) but unproven for Seleric. We have ~56–180 daily rows per brand; the benchmarks use thousands of points.

**Implication [recommendation]:** the graph must come from reviewed business knowledge first; data *verify and weight* it. Discovery (FCI / PCMCI+) is at most an offline audit that proposes edges for a human to accept. It should not sit in the answer path.

### 3.2 Simple per-metric statistical tests are a strong baseline

BARO (median/IQR shift test) and NSigma beat most graph methods. This supports keeping our layer 1 style (robust, non-parametric) and making it **one module**, and it means any new component must beat this baseline on our own replay set to earn its place.

### 3.3 RiskLoc and relatives answer a *localisation* question, not a *cause* question

RiskLoc (Kalander, 2022; MIT, `shaido987/riskloc`): finds the set of attribute combinations (e.g. `platform=Meta ∧ campaign=X`) that explain a deviation in multi-dimensional data. Two-way partition of leaf deviations, weights growing with distance from the cut, risk score from weighted purity + deviation change ("ripple effect"). Claims up to +57% F1 over the second-best baseline. PSqueeze (JSS) generalises the ripple effect and runs ~10 s/case. Both are the successors to HotSpot / Squeeze / Adtributor, which our `EXPLORATION_MODEL.md` already cites; our layer 3 is Adtributor-like single-dimension splitting.
- The repo is **research code, not a library**: CLI `run.py`, CSV in, no package, deps pandas/numpy/scipy/kneed/loguru. Using it means **porting** the algorithm (MIT, keep attribution), not `pip install`. There is a `--derived` mode for ratio measures **[unverified how well it handles CTR-style ratios]**.
- Its value depends on whether Seleric's real events are *multi-attribute combinations*. Our dimensions are mostly hierarchical (platform ⊃ campaign ⊃ adset ⊃ ad), where a one-dimension split at the right level already finds them. RiskLoc earns its keep on *cross* dimensions (platform × payment method × region, product × channel). **This is testable before building:** on the past events we have, how much of the change does layer 3 leave unexplained, and does a cross-dimension combination explain it?
- It is localisation only. It says *where*, never *why*.

### 3.4 DoWhy-GCM is the right tool for "which node changed", with limits

`gcm.attribute_anomalies` (one anomalous point) and `gcm.distribution_change` (period vs period, Budhathoki et al. AISTATS 2021, attributes the change to changed *causal mechanisms*) are exactly the period-comparison attribution we lack beyond 7 days. Requirements and limits from the docs/notebook:
- needs a **DAG you supply**, a fitted mechanism per node, and assumes causal sufficiency (no unmodelled confounders) and i.i.d. samples; the docs say nothing about time series and a lagged graph is not a DAG over rows without unrolling **[unverified: any built-in time-series support in gcm]**;
- the notebook's own caveats: attributions depend on the graph and mechanism fit, bootstrap CIs can include zero, and "the method isolates which node changed but does not identify why".

So GCM consumes a graph, which is why the graph question is the real decision.

### 3.5 Dated interventions are the strongest identification we have

Quasi-experimental tools (interrupted time series, synthetic control, CausalImpact-style) are built for "something was changed on date D". CausalPy (PyMC Labs) covers ITS, synthetic control, DiD; its own notebook shows ITS vs CausalImpact vs synthetic control can disagree by more than an order of magnitude on the same data, so the counterfactual choice must be deliberate. CausalImpact-style tools require covariates the intervention did not affect. Seleric already has a change log (`budget_changes`, `status_changes`, `bid_strategy_changes` are graph roots in `causal_priors.yaml`) that is only used as narrative text. A budget edit on day D followed by spend/impressions moving on D+0..1 is better evidence than any regression on 55 rows.

### 3.6 LLMs should not be the root-cause engine; they should read structured results

OpenRCA (ICLR'25): best model + purpose-built agent solved ~11% of 335 software failures; a 2026 follow-up reports exact root-cause-set recovery ~21% across 11 frontier models, and critics argue some labels aren't recoverable from the telemetry at all. This matches our own history (revision loops, tautological causes). Keep the existing split: engine decides, LLM narrates, grounding check verifies. **The OpenRCA 2.0 numbers are from a very recent paper I did not verify.**

### 3.7 Tooling facts

| Library | Role | Maturity / licence | Note |
| --- | --- | --- | --- |
| `dowhy` (MIT) | identification, estimation, refuters, `gcm` attribution | mature, already a dependency (≥0.14) | keep; use `gcm` only for attribution |
| `statsmodels`, `scipy`, `networkx` | tests, HAC, graph ops | already deps | enough for most of the lean design |
| `ruptures` | change-point detection (WHEN) | BSD **[unverified]** | small; fixes "estimation sample includes abnormal days" |
| `causal-learn` | PC/FCI/GES discovery | MIT **[unverified]** | audit use only, per 3.1 |
| `tigramite` | PCMCI+/LPCMCI | **GPL-3 [unverified, check before any use]** | licence + small-N power both argue against the answer path |
| `pyrca` (Salesforce) | RCA ranking, graph building with domain constraints, StatsDetector | BSD-3 **[unverified]** | eval cross-check only |
| `riskloc` | multi-dim localisation | MIT research code, CLI | port, not install |
| `CausalPy` / CausalImpact ports | ITS / synthetic control for dated interventions | active (PyMC Labs) | candidate for the intervention operator |

## 4. Answers to your specific questions

### 4.1 Should there be a dependency graph from the start, and how do causal graphs use it?

**Yes, as the first input, not as an afterthought [recommendation].** Reasons: (a) §3.1; (b) our worst failures (funnel_purchases as "cause" of gross_sales, allocated metrics as drivers, campaign-name resolver, near-tautologies) are exactly what the graph's edge kinds encode (`co_measure`, `twin`, `allocation`, `subset`, tiers, "no lag-0 against funnel order"); (c) it replaces threshold heuristics (r ≥ 0.95, CV < 2%) with reviewed facts, and keeps them only as a *check* that the reviewed fact still holds in the data.

The committed graph (233 metrics, 464 edges) has three kinds of content with different standards of evidence, and they should be used differently:

| Edge group (counts) | Meaning | How the engine uses it |
| --- | --- | --- |
| composition, ratio_component, declared_input (76/66/36) | exact accounting | layers 2–3 (identities, Shapley, bridge); never "causes" |
| twin, co_measure, subset, allocation (198/27/9/12) | same quantity / part / model output | **exclusion rules** for candidate drivers; a divergence between co-measures is a *measurement* hypothesis |
| mechanism, feedback (37/3) | reviewed causal direction, sign, lag window, confidence | **the candidate-cause graph**: DoWhy's DAG, ordering by tier, lag windows for the treatment lags |

Only ~40 metrics sit on mechanism edges. The causal graph the engine uses per question is a **subgraph**: outcome's accounting tree + ancestors along mechanism edges (+ feedback at lag ≥ 1) + the segment dimensions. That is far smaller than 233 nodes, which is what keeps estimation inside our ~55-row budget.

### 4.2 Weights

Three different things get called "weights"; keep them separate **[recommendation]**:

1. **Prior strength** (already there): `structural > strong > moderate`, sign, lag range. Reviewed, versioned, edited in YAML by people.
2. **Measured weight**: per brand and per history window, the estimated effect size, lag, and stability (e.g. bootstrap or rolling-window sign agreement) of each mechanism edge, using the same estimator the engine uses. Stored as a separate **overlay** artefact with `n`, window and timestamp, **never written back into the reviewed YAML**.
3. **Ranking score at question time**: prior strength × measured weight × "did it move" × tier proximity decides *which hypotheses to test first*. It orders the work; it is **not evidence**. Evidence still comes only from the tests on the event (precedence, intervention shift, placebo, refuters).

Risk to design around: a weight estimated on history that includes the event, then used to explain the event, is circular. Overlays must be computed on pre-event windows.

### 4.3 Anomaly system and RiskLoc

- You are right that anomaly detection exists, four times. The rebuild should **consolidate to one detection module** (robust, non-parametric, reference-aware; BARO-style) used by diagnosis, exploration, business state, forecasting QA. That is a deletion-heavy change.
- RiskLoc is not the anomaly detector. It is a multi-dimensional *localiser* that needs a per-leaf forecast/real pair, i.e. it sits on top of the detector's reference. Whether to add it depends on the cross-dimension test in §3.3. If layer 3 already explains >~80% of change on the events we have, RiskLoc is not worth its port cost.

### 4.4 Keeping it Cube-v3 / catalogue agnostic

What already helps: the dependency graph is *generated* from the catalogue by `scripts/build_dependency_graph.py`, validates that every id in `causal_priors.yaml` exists, and refuses structurally invalid edges, so a catalogue change fails the build loudly rather than silently mis-wiring causes. The engine (`causal/diagnosis.py`) is already "no I/O, no metric names" with a test enforcing it.

What to add **[recommendation]**: a thin set of ports the engine depends on, nothing else: `GraphPort` (nodes with tier/unit/aggregation/grain/date-axis, typed edges, `same_quantity_group`), `SeriesPort` (fetch daily series by id × scope × dimension), `ChangeLogPort`. Cube v3 / new catalogue = new adapter + regenerated `dependency_graph.json`; a metric-id rename map lives in the adapter, not the engine. The graph file gets a `schema_version` and a `catalogue_version` it was built from, checked at load.

### 4.5 What to cut from the larger rebuild design (`rebuild.md`)

| Item | Verdict | Why |
| --- | --- | --- |
| Investigation ledger + hypothesis generator | **Keep, small** | It is the mechanism for "more than one candidate, null and measurement always included"; the graph supplies hypotheses |
| Split `_diagnose` into operators | **Keep** | Needed for testing and for reuse; no behaviour change |
| Catalogue → graph priors | **Keep, first** | §4.1 |
| ruptures WHEN | **Keep, small** | regime-safe history, onset date |
| Structured change-log / intervention evidence | **Add** | §3.5; best identification available |
| Evaluation with planted causes + replay of our real incidents | **Keep, and build first** | §3.1 last bullet; without it we cannot tell any change from a regression |
| RiskLoc port | **Conditional** | only if §3.3 test shows unexplained cross-dimension residual |
| GCM `distribution_change` for period comparisons | **Conditional / spike** | needs a graph and mechanisms; try on the 30d-vs-30d ROAS case |
| FCI / PCMCI+ discovery, nightly graph caches, bootstrap stability | **Defer** | §3.1; GPL; N too small; graph is reviewed instead |
| Recursive scheduler + async "deep" mode | **Defer** | depth is mainly "WHERE then WHAT" which the operator split already allows; add only after evals show shallow answers are the remaining failure |
| PyRCA | **Eval cross-check only** | not in the answer path |

## 5. Risks and open unknowns

- No benchmark of our own exists; the replay set must be made from real incidents (brand 20: 2026-09-14..22 ad outage, 2026-10-02 −56% day, AWARENESS-03OCT launch, TH-383) plus synthetic worlds with planted causes **and causes hidden from the catalogue**.
- The graph encodes today's business model (Meta/Google, COD/prepaid, Shopify). Brands that differ need per-brand overrides or the mechanism list is wrong for them.
- Unmeasured promotions / creative / competitors remain invisible to every method above. The honest output for those is "located, not identified".
- `causal_priors.yaml` is reviewed by whom, and how often? Governance of the prior is the new single point of failure (the external report's central point).
- `rebuild.md` is deleted in the working tree (still in `bd8dfde`); only its tail was recoverable. If its earlier sections (§1–8) still exist elsewhere I have not seen them.

## 6. Sources

- Pham, Ha, Zhang, *Root Cause Analysis for Microservices based on Causal Inference: How Far Are We?*, ASE 2024: https://arxiv.org/abs/2408.13729 ; RCAEval benchmark: https://arxiv.org/abs/2412.17015
- Kalander, *RiskLoc*: https://arxiv.org/abs/2205.10004 ; code: https://github.com/shaido987/riskloc
- *Generic and Robust Root Cause Localization for Multi-Dimensional Data (PSqueeze)*: https://arxiv.org/abs/2305.03331
- Budhathoki et al., *Why did the distribution change?*, AISTATS 2021: https://proceedings.mlr.press/v130/budhathoki21a.html
- DoWhy-GCM: https://jmlr.org/beta/papers/v25/22-1258.html ; online-shop RCA notebook: https://www.pywhy.org/dowhy/v0.12/example_notebooks/gcm_online_shop.html
- PCMCI+: https://arxiv.org/abs/2003.03685 ; tigramite: https://pypi.org/project/tigramite/
- PyRCA: https://opensource.salesforce.com/PyRCA/latest/pyrca.html
- CMMD (cross-metric RCA, Microsoft): https://www.microsoft.com/en-us/research/?p=863736
- CausalPy: https://causalpy.readthedocs.io/ ; ITS vs CausalImpact notebook: https://causalpy.readthedocs.io/en/latest/notebooks/interrupted-time-series-causalpy-vs-causalimpact.html
- OpenRCA (ICLR 2025): https://iclr.cc/virtual/2025/poster/32093
