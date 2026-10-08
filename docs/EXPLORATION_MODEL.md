# Exploration model — from fetching metrics to exploring data

**Status:** v1 engine + `explore_data` tool on branch `gaurav-exploration` (2026-10-08).
**Code:** `src/seleric_swarm/exploration/` (pure), `src/seleric_swarm/toolsets/exploration.py` (tool),
tests `tests/unit/test_exploration_{engine,tool}.py`.

## 1. The problem

The agent's loop today is *question → resolve metric → fetch → answer*. It only ever looks at what
the question names, so it cannot answer "anything unusual this week?", "how are we doing?" or "what
should I look at?" — and even for a named metric it never notices the segment, the step change or
the co-moving metric next to it. `diagnose_metric_change` is excellent once someone knows *what*
moved; nothing finds *that* it moved.

Exploration needs a different shape: **look at the data space, test many patterns, keep the ones
that survive, and offer the next probe.**

## 2. What the research says

| Work | Idea we take | Where it lands |
|---|---|---|
| Tang et al., *Extracting Top-K Insights from Multi-dimensional Data*, SIGMOD 2017 ([paper](https://www.microsoft.com/en-us/research/publication/extracting-top-k-insights-multi-dimensional-data/)) | An insight = (subspace, breakdown, extractor, type). Score = **impact × significance**: impact = the subspace's share of the whole, significance = 1 − p under a per-type null hypothesis. *Point* insight: H0 "values follow a power law + Gaussian noise", test the top value's residual. *Shape* insight: H0 "slope ≈ 0", weighted by r². | `insights.outstanding_top`, `insights.trend`, the ranking in `engine.explore` |
| Bhagwan et al., *Adtributor*, NSDI 2014 ([paper](https://www.microsoft.com/en-us/research/?p=166175)) | For a change in an additive KPI: per element **explanatory power** EP = (Aᵢ − Fᵢ)/(A − F) and **surprise** = Jensen-Shannon term between prior and current share. Greedy *succinct* set per dimension: most surprising first, each element ≥ T_EEP (10%) of the change, until the set explains T_EP (67%). The dimension whose *distribution changed* localises the cause, not the one that is merely biggest. Derived (ratio) measures need a finite-difference EP. | `insights.distribution_shift` (paper's own example is a unit test) |
| HotSpot / Squeeze / RiskLoc / CMMD ([survey in PSqueeze](https://arxiv.org/pdf/2305.03331)) | Multi-attribute root-cause combinations explode combinatorially (MCTS, clustering); **cross-metric** context matters (CMMD). | v1 is single-dimension (Adtributor's finding: multi-dimension root causes are rare); cross-metric = `co_movement`; multi-attribute combos are roadmap |
| MacroBase DIFF, VLDB 2019 ([paper](https://people.eecs.berkeley.edu/~matei/papers/2019/vldb_macrobase_diff.pdf)) | Explanations as attribute combinations ranked by risk ratio with **minimum support** (default 0.2) and minimum ratio (1.5). | the practical floors (`EXPLORE_MIN_*`), the 1.5× ratio on outstanding |
| QUIS, EMNLP 2024 ([paper](https://arxiv.org/abs/2410.10270)) | Two stages: generate questions from **metadata only**, then answer each with a statistical insight search (beam search over subspaces). No training, adapts to any schema. | catalogue-driven planning (`space.py`); drill-down follow-ups are the beam |
| InsightPilot, EMNLP 2023 ([paper](https://ar5iv.labs.arxiv.org/html/2304.00477)) | The LLM picks analysis *intents* (understand / summarise / explain) and issues them to a **statistical insight engine**; the engine, not the LLM, computes. | `explore_data` is the engine; the agent chooses among `follow_ups` |
| Zhao et al., *Controlling False Discoveries During Interactive Data Exploration*, SIGMOD 2017 ([paper](https://www.arxiv.org/pdf/1612.01040)) | Exploration *is* multiple hypothesis testing; without control, false discoveries are guaranteed. α-investing controls mFDR across a session. | Benjamini-Hochberg over every test in a run; α-investing across turns is roadmap |
| InsightBench, ICLR 2025 ([paper](https://arxiv.org/abs/2407.06423)) | Evaluate analytics agents on datasets with **planted insights**, end to end. | the test strategy (planted patterns must be found, noise must not be) and the eval roadmap |

The common pattern: **the LLM decides what to look at and how to talk about it; a statistical engine
decides what is real.** That is this repo's golden rule already ("the LLM never generates production
analytical SQL"), applied to exploration.

## 3. The model

```
               catalogue (metrics, lineage, dimensions, views)
                                 │
                    plan subspace (space.py)
     headline metrics by lineage hub score · breakdowns by hierarchy/cardinality
                                 │
                fetch daily series via certified Cube path
     current window + previous window + K earlier windows ("normal variation")
                                 │
               test every pattern (insights.py) → p-values
   period_change · trend · change_point · distribution_shift · outstanding · co_movement
                                 │
          Benjamini–Hochberg over ALL tests  +  practical-size floors
                                 │
       rank: kind prior × impact (subspace share) × significance  → top-k
                                 │
     Findings (evidence-backed) + typed follow-ups ── agent picks the next probe
            │                                   │
   explore_data(filters={dim: seg})     diagnose_metric_change(metric, window)
        (drill into a subspace)              (why did it move?)
```

### 3.1 The data space (`exploration/space.py`)

Exploration walks a graph the catalogue already describes:

- **Nodes:** metrics (additive or ratio, view, grain), dimensions (hierarchy level, enumerated
  cardinality, time axis), views.
- **Edges:** lineage (`formula.depends_on`), "can be broken down by" (`supported_dimensions`), shared
  dimensions (join paths), date twins.
- **Headline metrics** = additive metrics with the highest lineage in-degree (the base quantities
  other metrics are built from), round-robin across views, date twins skipped. Nothing is named.
- **Breakdowns** = the metric's dimensions minus scope keys (carried by ≥80% of views) and time axes,
  hierarchy roots first. Shared with `diagnose_metric_change` (moved out of `toolsets/diagnosis.py`).

### 3.2 Insight types (`exploration/insights.py`)

| Kind | Null hypothesis | Test | Practical floor |
|---|---|---|---|
| `period_change` | this window's change is like earlier window-to-window changes | normal prediction-interval t-test vs the K−1 earlier changes (df = K−2) | ≥3% |
| `trend` | slope = 0 | OLS on day index + day-of-week dummies, HAC (Newey-West, 7 lags); significance = (1−p)·partial R² (Tang) | ≥1% of level / week |
| `change_point` | no mean shift | max-t single split on day-of-week-adjusted values, Bonferroni over splits | ≥5% |
| `distribution_shift` | the segment mix changed no more than it usually does | Adtributor set; prediction-interval t-test of log JS(prev, cur) vs JS between earlier consecutive windows | a share moved ≥2 pts |
| `outstanding` | segment values follow a power law | log-space power-law fit on ranks 2..n, residual of rank 1 (Tang) | top share ≥30%, ≥1.5× runner-up |
| `co_movement` | day-to-day changes are uncorrelated | Pearson on differenced, day-of-week-adjusted series, Fisher z with a Bartlett effective n; pairs related by lineage or the same units are not tested | \|r\| ≥ 0.4 |

Why these choices:

- **History windows, not a fixed threshold.** "−8%" is news for a stable metric and noise for a
  volatile one; every test compares with the metric's own variation (the same principle as
  diagnosis's same-weekday reference).
- **Small-sample tests.** An exploration has five or six earlier windows. A median/MAD z with a normal
  tail turned a real 21% drop into p ≈ 1e-270 and, worse, ran over the false-discovery budget under
  pure noise. The exact prediction-interval t-test is calibrated at that sample size.
- **Calibration, measured.** On 200 pure-noise datasets 2.5% of runs reported anything (budget: 10%;
  11.5% before the small-sample and Bartlett corrections). A segment falling to 70% of normal (≈9% off
  the total) is detected 97/100 times and localised to the right segment 100/100. Both are test-locked.
- **Differencing for co-movement.** Two growing series always correlate; day-to-day changes don't
  unless something links them. Still reported as an ASSOCIATION, never a cause.
- **Additive only for segment tests.** A ratio's segments don't sum to its total (Simpson's paradox);
  ratio decomposition is diagnosis's job (mix vs rate effect) and is a follow-up.

### 3.3 Multiple testing and ranking (`exploration/engine.py`)

- Every test performed counts toward Benjamini–Hochberg (q ≤ 10%), not only the reported ones — the
  more the engine looks, the more evidence it demands (see the calibration numbers above).
- Survivors must also clear a practical-size floor (statistically real but tiny is not news).
- Score = **kind prior × impact × significance**. Impact is Tang's subspace share (1 at the root, the
  segment's share in a drill-down). The kind prior ranks *what moved now* above structural facts the
  operator already knows ("the biggest channel is the biggest"); tunable in `policy_config.EXPLORE_KIND_PRIOR`.
- One insight per movement: a trend and a step on the same series, or a step inside the window that
  is already reported as the window change, are deduplicated.

### 3.4 The walk: follow-ups

Each insight carries typed `follow_ups` (`tool` + `args` + `reason`): drill into the segment
(`explore_data` with `filters={dim: seg}`), explain the move (`diagnose_metric_change` with the
window and direction), or see the series (`query_metrics` weekly). The tool summary lists them as
NEXT PROBES; the agent runs one when the question needs more depth. This is QUIS's beam expansion and
InsightPilot's intent selection, with the LLM choosing and the engine computing.

### 3.5 Evidence and claims

Every reported insight is a `Finding` (`finding_type="exploration.<kind>"`) backed by the daily
`EvidenceArtifact` rows it rests on; the current window of every explored metric is cited even when
nothing stood out, so "nothing unusual — revenue was X" is grounded. All findings are OBSERVATIONs
except co-movement (ASSOCIATION). The tool's reporting rules forbid causal wording unless a
`diagnose_metric_change` result says "a cause" — the ADR-003 evidence policy, unchanged.

### 3.6 Latency and depth

| depth | metrics × breakdowns | queries (≈) | intent |
|---|---|---|---|
| `scan` | 3 × 2 | 9 | "anything unusual?" at chat speed |
| `focus` (default) | 5 × 3 | ≤20 | normal exploration |
| `deep` | 8 × 5 | ≤48 | "go deep", background-friendly |

Queries run 6 at a time through the per-mission cache and Cube budget; the engine itself is
~15 ms per run. One tool call replaces the dozens of `query_metrics` round-trips an LLM would need to
assemble the same picture, which is also the latency win from the earlier analysis.

## 4. Roadmap

1. **Runner routing** — route open-ended questions ("how is the business doing", the deferred P0-2
   in `STRESS_TEST_BUG_BACKLOG.md`) to `explore_data` deterministically instead of relying on the
   model reading the instructions; give them tools even when Jev says `conversation`.
2. **Exploration map per thread** — persist what was explored and found (TurnRecord / artifact store)
   so "what else?" continues the walk, novelty down-weights repeats, and **α-investing** (Zhao et al.)
   carries the false-discovery budget across turns instead of resetting per call.
3. **Precomputed profiles** — run `explore_data(depth="scan")` on the domain-health scheduler and keep
   the result in the snapshot store; the chat answer becomes a read (sub-second) and the live call only
   refreshes stale subspaces.
4. **More insight types** — outstanding *last*, seasonality break (weekday pattern changed),
   ratio-metric segment attribution (Adtributor §4 finite-difference EP), funnel step anomalies via
   `funnel_decomposition`.
5. **Multi-attribute localisation** — two-dimension combinations (HotSpot/Squeeze style) where the
   catalogue declares a hierarchy; needs cross-tab support and row-count limits confirmed in seleric-mcp.
6. **Evaluation** — an InsightBench-style suite in `eval/` with planted drivers on synthetic tenants:
   recall of planted insights, false-finding rate on null tenants, queries and latency per depth.

## 5. Constraints to confirm at the MCP seam

- Large breakdowns: `query_metrics` redirects/ranks large ones; `explore_data` skips a breakdown above
  `EXPLORE_MAX_SEGMENT_ROWS` daily rows and says so. Confirm the gateway's own row caps.
- A day with no row is read as 0 for additive metrics (Cube omits empty buckets) — confirm this holds
  for every serve view, or sparse segments will look like collapses.
