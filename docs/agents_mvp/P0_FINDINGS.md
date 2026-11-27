# D-247 P0 findings (2026-11-26)

## Loop defects (commit 918b9f1, branch agents-mvp)

| # | Defect | Status |
|---|---|---|
| 1 | Bot tool budget ignored (all runs capped at AGENT_MAX_TOOL_CALLS=20) | FIXED: `TurnPolicy`; bots use `limits.max_tool_calls` (40), steps = calls/2, 480 s (`BOTS_TURN_BUDGET_S`) |
| 2 | Earlier runs' payloads counted as checked figures (shared thread `bot-<id>`) | FIXED: standing runs use `bot-<id>-<run>`; chat keeps one thread |
| 3 | Bots offered `draft_*`/`send_email` | FIXED: `ANALYSIS_TOOLS` allowlist; a test fails if a new local tool is not classed |
| 4 | "Large results cut silently" | NOT A DEFECT: cut lists carry an explicit marker, and query results / live ad reads keep full rows in `run_python` by query id. `evidence_get` dropped from the plan. |
| 5 | Scratchpad written, never shown | FIXED: rendered in the dynamic instructions each step |

Tests: bots_tests + agent_loop 236 pass; full unit suite 1218 pass. Failures that predate this work: `test_auto_chart` (1) and two email tests that need `openpyxl` (not installed in the worktree venv).

## Objective metric (decision 1): CONFIRMED

On `serve.ssot_ad_pnl_daily` (brand 20, Meta), `net_profit` equals `net_sales − net_cogs − ad_spend` to the rupee for Sep, Oct and Nov 2026.

`gross_profit` in this table is neither `contribution_before_ads` nor the D-194 meaning: p43 is not deployed. **Do not use gross_profit.**

| Month (Meta channel) | Net sales | Net COGS | Ad spend | Net profit |
|---|---|---|---|---|
| Sep | 4.74 L | 2.92 L | 8.55 L | −6.72 L |
| Oct | 12.83 L | 5.43 L | 11.68 L | −4.28 L |
| Nov (to 26th) | 15.39 L | 6.13 L | 9.49 L | −0.23 L |

Returns land in lumps. For example, 09-28 has `returns_closed` 10.7 L, which makes net sales −10.2 L that day. 09-05 has 85 k. Day-level net metrics are therefore unusable as a reference without lump handling. This matters for outcome scoring (P2) and for any "usual" baseline.

## RCA (decision 6): PARTLY usable, so the decision reopens

Live `diagnose_metric_change` (legacy engine, live catalogue/MCP) was run on 3 recorded brand-20 events. Script: `rca_live_check.py`.

| Event | Event size | Decomposition ("bridge") | Localisation ("where") |
|---|---|---|---|
| CTR halving 10-04..05 (Meta) | ✔ −49.8 % (CH 1.87 → 0.89/1.09) | ✔ impressions +231 % vs clicks +66 % | ✔ new AWARENESS ad took 52 % of impressions at 0.04 % CTR (the known AWARENESS-03OCT launch) |
| Spend outage 09-15..16 (Meta) | ✔ −100 % | ✘ writes ad_spend through a P&L identity (`ad_spend = gross_sales − cogs − gross_profit`, or `−net_profit + contribution_margin`), giving "85 % of the spend change came from gross sales". Causally backwards. | ✔ "broad-based" vs usual; compare mode names the top campaign (15 %) |
| Net sales drop 10-02 | ✘ vs "usual": **+73.8 %** against a usual of ₹25.6 k. Real cause, traced 11-27: the 09-14..22 outage became the reference (09-16..20) and the whole recovery 09-21..10-01 was flagged abnormal. Four negative booking days (returns lumps) forced the linear scale, where a −75 % outage looks milder than the +300 % recovery after it. ✔ vs 09-25 (compare mode): −51 % | identity correct but trivial (net_cogs + contribution) | ✔ compare mode: Meta channel 72 % of the change (CH: 58,746 → 25,030) |

Also wrong: compare mode labels short windows "within normal day-to-day variation" even for −100 % spend. With 1–2 days per period the overlap test means nothing.

Usable as-is: **localisation** (where) and **ratio levers** (CTR/CPC/CVR-style rate splits).
Not usable as-is: the "exact bridge" for input metrics such as spend; event size vs "usual" after a multi-day outage on a series with a few negative days; the "normal variation" label in short compare windows.

## Engine fixes (owner 2026-11-27: "fix the engine first")

- (b) `_transform`: log scale for a mostly positive series (at most `DIAG_LOG_MAX_NEGATIVE_SHARE` = 20 % negative days); `_err` clamps negatives to zero on the log scale. On the real 10-02 series the reference moves from 09-15..20 (₹18.6 k) to 09-25..10-01 (₹77.6 k). A wider outlier seed was tried and was not needed.
- (a) `_bridge_parts`: bridge candidates computed from the outcome, or from a data twin of it, are dropped (gross/net profit are built on `order_pnl_ad_spend`, the twin of `ad_spend`). Residual: `ssot_net_sales = ssot_net_cogs + ssot_contribution_before_ads` stays, because the SSOT catalogue metrics carry no `depends_on`. That is a catalogue gap (Core change) and is not fixed here.
- (c) Compare mode with fewer than 3 days per period: the change is sized against the 1-day noise profile instead of reading 0 ("overlap a lot").
- Tests: `tests/unit/test_diagnosis_live_20261126.py` (3; all fail on the old engine). Full unit suite 1222 pass; only `test_auto_chart` fails, as before this work.
