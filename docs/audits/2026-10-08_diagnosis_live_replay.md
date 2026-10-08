# Diagnosis engine — live replay of real incidents (2026-10-08)

Method: find real recent incidents in ClickHouse (`serve.*`, brand 20), establish
ground truth by hand, then run `diagnose_metric_change` on the same question via
the live MCP/Cube path (in-container harness, patched src) and compare.

## Ground truth found in ClickHouse

| # | Incident | Root cause (verified) |
|---|----------|-----------------------|
| 1 | Meta CTR 1.87% → 0.89% (Oct 4), 1.09% (Oct 5) | `AWARENESS-03OCT` (OUTCOME_AWARENESS) launched 2026-10-03 15:59 UTC, paused 10-06 08:59 UTC (`serve.ad_changes`): 288,898 impressions (52% of Oct 4–5), ~0 link clicks. Sales campaigns' CTR held (1.96% → 2.00%). Mix effect, not creative fatigue. |
| 2 | Meta gross ROAS ~1.44x → 1.16x (Oct 5–7) | Spend scaled ~+55% while sales +34%. Concentrated in TH-445-PROSUSPENDERBOOTS-25SEP (ROAS 1.71x → 0.24x) and TH-383-SUSPENDER-29SEP (1.58x → 0.86x, spend 6.6k → 19.3k), plus lower unattributed Meta sales. Within daily noise overall. |
| 3 | Meta add-to-cart rate 4.49% → 3.53% (Oct 6–7) while sessions rose | Mix: TH-383-SUSPENDER-UGC grew to 21% of Meta sessions at 1.76% ATC (was 2.77%); unattributed Meta sessions 4.48% → 2.06%; other campaigns held (~4.4–4.7%). Within daily noise overall. |

All engine figures after the fixes match ClickHouse exactly (impressions 556,304; awareness 288,898; ad-level spend/sales 9,024/2,167 and 19,349/16,648; ATC rates per segment).

## What was wrong (before) → fixed

1. **Conformed dimensions treated as tenant keys.** `_scope_dimensions` = "on ≥80% of views"; after the 10-08 conformed-dims work campaign_*, ad_platform, finance_channel are on every view, so campaign was never examined and their tokens were stripped from grain co-measure checks. → scope = the runtime's brand key (`_BRAND_DIM_KEYS`) only.
2. **Hierarchy ranked last.** Levels ≥2 (campaign/adset/ad) sorted after unranked dims and fell off the cap. → hierarchy first, coarsest level first.
3. **Rate segments weighted by the numerator** (clicks for CTR). → weight = denominator of the data-verified `a / b` identity (`_rate_parts`), any view.
4. **Same-weekday reference forced** on series with no weekly pattern → noise band 3x too wide (CTR −52% called "normal", z −1.3; nearest days z −5.3). → Kruskal-Wallis weekday test on detrended history picks the scheme.
5. **Level factors applied to nearest-day references** (double-counted the recent trend; ATC "usual" 3.71% vs real 4.49%). → refs inside the anchor's 7-day level window are unscaled.
6. **Mix effect attributed to the segment that lost share.** → mix priced vs overall reference rate `(s1−s0)·(r̄−R0)` (same totals); new mix-driven localisation when rates held; narrative names share shift and "new in this period".
7. **Ratio-of-sums metrics (ROAS) never localised** (sales with no spend broke the weighted-average check; components in other views were refused). → exact split `(ΔN_s − R0·ΔD_s)/D1`; unattributed rows kept as a `(not set)` segment.
8. ROAS printed as 116% → ratios whose data exceed 1 print as multiples (1.16x).
9. Dimensions from breakdown tables (segments sum to 5x) counted as whole → coverage must be within [0.9, 1/0.9].
10. A driver with too little history for its own profile was "ruled out: did not move" → now insufficient evidence; drivers need ≥2 observed reference days.
11. Wording: "for that weekday" / "the day before" now follow the reference kind and window length.

Tests: `tests/unit/test_diagnosis_live_20261008.py` (6 new); unit suite 983 passed (production_hardening excluded). No metric names, keywords or regex added (existing `test_engine_source_hardcodes_no_catalogue_names` passes).

## Gaps / open tasks

- **E2E not verified through the agent.** Two real-LLM replays of "Why did our Meta CTR drop on October 4th and 5th?" were starved by Azure 429s on gpt-5-mini (agent made 4 diagnose calls, no answer). Re-run when quota allows; check the agent relays the WHERE line (awareness campaign, 52% share).
- **Not deployed.** Code is committed on `gaurav`; the api image was not rebuilt.
- **"Why" stops at "where".** Verdict is `located_cause_not_identified`: the engine localises the launch but does not read the change log. Next: join localised segments to `status_changes` (paid_media_changes: first_seen / status / budget changes) as intervention evidence.
- **Driver planning is direction-blind.** For an ad-delivery rate it proposes sessions, purchases, net sales (downstream of clicks). Needs a causal order (e.g. from lineage/date basis) so only upstream metrics are candidates.
- **Snapshot attributes used as history splits.** ad_status/adset_status/campaign_status hold the current status, so "PAUSED 1.97% → 0.32%" lines are artefacts. Need a catalogue flag (slowly-changing / current-state dimension) to exclude them.
- **Event-end ambiguity.** `event_end` at midnight is read as exclusive; a model passing a date-only inclusive end ("2026-10-05") loses the last day. Consider `date` typed params or an explicit inclusive flag.
- **Rate segment duplicates.** `(not set)` merges null and empty values by summing — correct for additive metrics, approximate for rate segments (guarded by the reconstruction check).
- **Unattributed Meta traffic** (fb_feed sessions with no campaign; ATC fell 4.5% → 2.1%) is a tracking/attribution issue worth a separate look (UTM/fbclid stitching).

## Related failure: thread_fa3eb0a9 (not fixed here)

The UI link pointed at MS3-c8200c969d, but that mission belongs to another thread: the UI keeps a stale `missionId` across threads (`office-ui` store.ts `snap.missionId ?? s.missionId`). The failed turn is **MS3-dd829538c9**, "TH-149-SCRATCHLOUNGE-25SEP why has this Campaign performance declined" (27.6s, completed, validation PASS trust 0.85, evidence_ids []).

- **The question was framed wrong.** The plan was `why_single_metric` on `ctr`, inherited from the prior turn. The question names no metric.
- **The agent did no work.** Its only calls were `find_metrics` and `get_metric_definitions`. It never ran `diagnose_metric_change` or `query_metrics`, then re-shipped the prior turn's CTR table (plan adherence 0.0).
- **The validator passed it.** `agent/validation/__init__.py` `_unbacked_figures` returns `[], 0.0` when the value pool is empty, so an answer with zero evidence passed as STRONG.
- **The prior turn was also wrong.** "Top 5 Meta campaigns' CTR" was ranked by CTR, so it picked tiny or new campaigns. It also showed "No data" for Oct 5–7 for a campaign that spent 21.6k on Oct 5.
- **The re-ask failed differently.** MS3-05bfae3a62 resolved "campaign performance" to `payment_amount` and returned insufficient data.
- **Ground truth.** The campaign's ROAS fell 2.04 → 1.36 (Sep 29–Oct 3 vs Oct 4–8). Spend went 15.7k → 32.8k, including 5 budget changes and 21.6k spend on Oct 5, while orders went 15 → 17. A duplicate campaign, TH-149-SCRATCHLOUNGE-6OCT, launched Oct 6. CTR barely moved.

Open tasks:
- (a) Provenance gate: a data mission with an empty value pool whose answer contains figures must be revised or failed.
- (b) Gate a why-plan that never called the diagnosis tool.
- (c) Map "campaign performance" to the campaign's efficiency set (ROAS, spend, orders) instead of inheriting the prior metric or resolving to a payment metric. Derive it from the catalogue, not hardcoded names.
- (d) UI: reset `missionId` when the thread changes.
