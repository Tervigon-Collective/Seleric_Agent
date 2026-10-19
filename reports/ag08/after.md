# AG-08 after — Full Funnel Diagnostic (1–11 October)

Compared with [before.md](before.md). Live dump: [after_response.md](after_response.md). Cassette: `eval/cassettes/reports_after.json`.

## Question (unchanged)

> I NEED Full Funnel Diagnostic (1-11th October). Drop Off Analysis, Top Learnings, Meta Ads, Top Performer and Net Profit Performer, and Overall Data.

## Before → after

| | Before | After |
|---|---|---|
| Route | `failed` (`no_data`) | **`v2` answered** (`partial`) |
| Windows | **empty** → all `unknown_window_role` | **`event 2026-10-01..2026-10-11`** |
| Plan | single `funnel` (9 steps) | **multi-section** `funnel+ranking+lookup` (6 sections, 17 steps) |
| Evidence | none | funnel purchases/revenue, net_profit, net_sales, … |
| Step errors | 5× `unknown_window_role` | **0** |
| Derive errors | unit_mismatch / zero_whole | funnel_stages (typed) |
| Sections covered | n/a | **0.83** (5/6; one unsupported honestly) |
| Chart reconciles | n/a | **1.0** |
| Numbers grounded | 0 | **1.0** |
| Query calls | (failed before usable calls) | **14** (batched/cached path) |
| Latency | 50.3 s (then fail) | ~117 s (full multi-section + answer) |
| Output | empty / fallback | Narrative + KPI grid + funnel insights + ranking tables + coverage |

## What fixed the wrong answer

1. **Multi-objective ReportPlan** — objectives decompose into independent sections (funnel, ranking, lookup); one unsupported section no longer sinks the mission.
2. **Window inference** — `1-11th October` → `2026-10-01..2026-10-11` when the draft omits slots.
3. **ReportCoverage** — section-level `complete` / `partial` / `unsupported` / `query_failed`; headline KPIs preserved when another section declines.
4. **Funnel-safe derive** — `funnel_stages` with compatible grains; missing/incompatible stages are not zeroed.
5. **Structured parts** — KPI_GRID / CHART / TABLE with evidence ids; charts are no longer title-only.

## Remaining honesty / follow-ups

- Expectation `status: completed` still fails when any section is unsupported — correct; report is `partial`.
- LLM sometimes maps “Meta top performer” to funnel purchase rankings when ad-spend ranking metrics are ambiguous; ranking template still labels the criterion explicitly.
- Session-funnel rates without Core `ratio_components` — see [CORE_COORDINATION.md](CORE_COORDINATION.md).
- Vitest for `KpiGrid` is present; this host has no Node binary in PATH (`office-ui/node_modules/.bin/vitest` ready when Node is available).

## Provenance

Every displayed figure in the after answer resolves through `Evidence` → certified `ResultSet` / `Derivation` rows (evidence ids on KPI cards and chart points). Internal step ids (`q1`, `d1`) are stripped from business-facing coverage text.
