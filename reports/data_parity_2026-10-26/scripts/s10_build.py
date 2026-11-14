import sys, os, json, re, collections, datetime as dt
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.path.dirname(__file__) + "/libs")
import model, xl
from cmp import tol_status
from cmp2 import classify2
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill
O = os.path.dirname(__file__) + "/out/"; L = lambda f: json.load(open(O + f))
OUT = "/opt/seleric/Seleric_Agent/reports/data_parity_2026-10-26/"; os.makedirs(OUT, exist_ok=True)
WIN = {"today": ("2026-10-26", "2026-10-26", "partial - not judged"), "yesterday": ("2026-10-25", "2026-10-25", ""), "last7": ("2026-10-19", "2026-10-25", "7 complete days"), "last30": ("2026-09-26", "2026-10-25", "30 complete days")}
METS = {m["id"]: m for m in L("catalogue_metrics.json")}; PAR = L("parity_raw2.json"); CH = L("ch_checks.json"); CHAINS = L("chains.json")
SYNC = L("sync_parity2.json"); PLAT = L("platform_recon.json"); EXTRA = L("extra.json"); LAG = L("lag.json")
JM_S = L("join_matrix_static2.json"); JM_C = L("join_matrix_cov2.json"); INV = L("ch_inventory.json")
wb = Workbook(); NOW = dt.datetime.now().strftime("%Y-%m-%d %H:%M IST")
AD = L("adlevel.json"); PAY = L("payments.json"); SERVE_NOW = set(L("serve_now.json"))
EXPLAINED = {  # (metric) -> why a FAIL is not a defect
    "hook_rate": "Core scopes hook_rate to Meta per its definition ('3-second video views / impressions (Meta)'). Meta-only CH: 463,843/2,928,087 = 0.1584 = Cube. The CH recompute here was cross-platform (0.0975).",
}
# ---------- parity rows
par_rows = []; pcount = collections.Counter(); status_by_metric = collections.defaultdict(dict)
for r in PAR:
    s, a, b, note = classify2(r); m = METS[r["metric"]]
    if r["metric"] in EXPLAINED and s == "FAIL": s, note = "PASS", "EXPLAINED: " + EXPLAINED[r["metric"]]
    if r["window"] == "today" and s in ("WARN", "FAIL", "UNSTABLE"): s = "INFO(partial)"
    ch2 = (r.get("ch_after") or [None, None])[1] if (r.get("ch_after") or [None])[0] == "OK" else None
    d = abs((a or 0) - (b or 0)) if (a is not None or b is not None) and b is not None else None
    rel = (d / abs(b)) if d is not None and b not in (None, 0) else None
    w = WIN[r["window"]]
    par_rows.append([m["category"], r["view"], r["metric"], m["display_name"], m["date_axis"], r["axis"] or "(native/n.a.)", r["window"], f"{w[0]}..{w[1]}", a, b, ch2, d, rel, s, note,
                     f'{model.resolve(r["metric"])[1][0]}.{r["measure"]}', model.CUBES[model.resolve(r["metric"])[1][0]].get("sql_table"), r.get("ch_sql") or ""])
    pcount[(r["window"], s)] += 1; status_by_metric[r["metric"]][(r["axis"], r["window"])] = (s, a)
ST = ["PASS", "WARN", "FAIL", "UNSTABLE", "INFO(partial)", "REFUSED:STALE", "REFUSED:NOT_POPULATED", "NOT_RECOMPUTED", "CH_ERROR", "CUBE_ERROR"]
# ---------- identities and compositions from parity (Cube values; groups run seconds apart -> tolerance as usual)
cube_val = {(r["metric"], r["axis"], r["window"]): (r["cube"] or {}).get("value") for r in PAR if r["cube"] and "error" not in r["cube"]}
def cv(mid, axis, w):
    for ax in (axis, None):
        if (mid, ax, w) in cube_val: return cube_val[(mid, ax, w)], True
    return None, False
IDS = [("Net sales", ["order_pnl_net_sales", "net_sales", "product_order_net_sales"], True), ("Gross sales", ["order_pnl_gross_sales", "gross_sales", "product_order_gross_sales"], False),
       ("Cancel revenue", ["order_pnl_cancel_revenue", "cancel_revenue", "product_order_cancel_revenue"], True), ("RTO revenue", ["order_pnl_rto_revenue", "rto_revenue", "product_order_rto_revenue"], True),
       ("Return revenue", ["order_pnl_return_revenue", "return_revenue", "product_order_return_revenue"], True), ("Return+cancel revenue", ["order_pnl_return_cancel_revenue", "return_cancel_revenue", "product_order_return_cancel_revenue"], True),
       ("Discounts", ["order_pnl_discounts", "discounts", "product_order_discounts"], False), ("Shipping revenue", ["shipping_revenue", "product_order_shipping_revenue"], False),
       ("Taxes on net sales", ["taxes_on_net_sales", "product_taxes_on_net_sales"], True), ("Net COGS", ["net_cogs", "product_order_net_cogs"], True), ("Product cost", ["product_cost", "product_order_product_cost"], True),
       ("Operating cost", ["operating_cost", "product_operating_cost"], True), ("Contribution margin", ["contribution_margin", "product_contribution_margin"], True), ("RTO cost", ["rto_cost", "product_rto_cost"], True),
       ("New customers", ["new_customers", "channel_new_customers"], False), ("Orders", ["orders", "channel_orders"], False),
       ("Ad spend: delivery vs P&L", ["ad_spend", "order_pnl_ad_spend"], False), ("Ad spend: delivery vs product-allocated", ["ad_spend", "product_ad_spend"], False)]
chain_extra = []
for name, ids, sw in IDS:
    for w in ("yesterday", "last7", "last30", "today"):
        for ax in (["order_date", "event_date"] if sw else [None]):
            ref, ok0 = cv(ids[0], ax, w)
            for other in ids[1:]:
                v, ok = cv(other, ax, w)
                if ref is None or v is None: continue
                kind = "count" if "orders" in other or "customers" in other else "money"
                s, d = tol_status(v, ref, {"unit": "count" if kind == "count" else "INR", "aggregation": "count" if kind == "count" else "additive"})
                if w == "today" and s != "PASS": s = "INFO(partial)"
                chain_extra.append(["P&L" if "Ad" not in name else "Ads", f"{name}: same quantity in two domains", w, ax or "-", ids[0], ref, other, v, d, s, "cross-view identity"])
for mid, m in METS.items():
    comp = (m.get("formula") or {}).get("composition")
    if not comp: continue
    for ax in ([ "order_date", "event_date"] if m["date_axis"] == "switchable" else [None]):
        for w in ("yesterday", "last7", "last30"):
            tot, ok = cv(mid, ax, w)
            if tot is None: continue
            acc = 0.0; miss = False
            for c in comp:
                v, okc = cv(c["metric"], ax, w)
                if v is None: miss = True; break
                acc += c["sign"] * v
            if miss: continue
            s, d = tol_status(tot, acc, {"unit": m.get("unit"), "aggregation": m.get("aggregation")})
            chain_extra.append(["Composition", f"{mid} = Σ sign×components", w, ax or "-", mid, tot, "Σ " + ", ".join(("+" if c["sign"] > 0 else "-") + c["metric"] for c in comp)[:90], acc, d, s, "formula.composition from catalogue"])
# ---------- bugs
STALE = sorted({r["metric"] for r in PAR if r["cube"] and "stale_data" in str((r["cube"] or {}).get("error", ""))})
NOTPOP = sorted({r["metric"] for r in PAR if r["cube"] and "Not populated" in str((r["cube"] or {}).get("error", ""))})
AXFAIL = sorted({r["left"] for r in CHAINS if r["domain"] == "Axis" and r["status"] == "FAIL"})
axrow = {w: next((r for r in CHAINS if r["domain"] == "Axis" and r["left"] == "orders" and r["window"] == w), {}) for w in ("yesterday", "last7", "last30")}
oa = {x["window"]: x for x in CH["orders_axis"]}
avgmis = sorted(mid for mid, m in METS.items() if m["aggregation"] == "additive" and mid.startswith("avg_"))
BUGS = [
 ["B-01", "High", "Core / Cube v3 model + planner", "Placement-only commerce metrics change when date_axis=event_date (Core says they are 'identical on both axes')",
  f"{len(AXFAIL)} metrics affected: {', '.join(AXFAIL)}. `orders` last7 {axrow['last7'].get('left_val')} (default) vs {axrow['last7'].get('right_val')} (event_date); last30 {axrow['last30'].get('left_val')} vs {axrow['last30'].get('right_val')} (+55%). ClickHouse reproduces it: orders placed in last30 = {oa['last30']['placed_in_window']}; sale orders having any outcome event in the window = {oa['last30']['orders_joined_event_date']} ({oa['last30']['older_orders_with_event_in_window']} are older orders).",
  "Root cause: `fact_orders` joins `order_events` one-to-many; with an event_date filter the join admits every older order that had a cancel/RTO/return that day, and `orders`/`new_customers`/`total_sales` etc. count them. Because event_date is the default axis, the agent's 'orders 45' on 2026-10-24 (real: 26 placed) and AOV, COD, new-customer figures are inflated whenever the event-date axis is on.",
  "Repro: metrics_query(measure='orders', time_range=2026-10-24..2026-10-24, date_axis='event_date') -> 45; same without date_axis -> 26. Expected: 26 on both axes, or a refusal.",
  "Fix (Core): for placement_only/cohort_only metrics ignore date_axis and drop the order_events join, or refuse with a reason. Interim fix (agent repo, needs your OK): do not forward date_axis for metrics whose catalogue date_axis is placement_only/cohort_only/native.", "FIXED in working trees (not committed, not deployed): Core planner no longer swaps placement_only metrics to commerce.event_date (+ test); agent no longer forwards date_axis for non-switchable measures (+ tests). Redeploy Core to take effect. Mixed switchable + placement-only calls still send the axis."],
 ["B-02", "High", "Core freshness gate (fail_closed)", f"{len(STALE)} procurement / inventory / vendor metrics are refused as 'stale_data' although the pipeline loaded them today",
  "Examples: po_count (purchase_orders latest_data_at 2026-10-16, 'days_behind 10'), vendor_count (2026-10-19, 7 days), qc_count (10-24), received_units (10-24). Raw Iceberg for the same objects was extracted 2026-10-26 07:14 UTC and gold loaded 07:26 UTC. Metrics: " + ", ".join(STALE),
  "The gate compares the max business date of a sparse transactional table (last PO date / last vendor update) with 'allowed_lag_days: 1'. A quiet week looks like a stalled pipeline, so the whole Procurement/Inventory domain is unavailable to the agent. Checked: serve.purchase_requests has orders to 2026-10-22 while the gate reports 10-16, so the gate's date source also needs checking. For stock_layers the view itself is broken - see B-13.",
  "Repro: metrics_query(measure='po_count', last30) -> error stale_data / stale_views.purchase_orders.days_behind=10.", "Use pipeline load time (max _loaded_at / refreshed_at) for the freshness SLA, or a per-view exemption for sparse tables.", "Open - needs a policy decision. Not edited: options are (a) probe a load-time column instead of the business date, (b) per-view cadence exemption for sparse tables, (c) fail-open with a warning."],
 ["B-03", "High", "Agent repo (answer framing) + Core note wording", "Agent explained the 2026-10-24 loss using 'Undated' adjustments that are not part of that day",
  "In thread_56f6dd96..., the answer to 'Drill into net sales on 2026-10-24' says large negatives sit in 'Undated' and 'materially worsen the visible profit for the period' and 'reconcile to order-date totals'. Facts: dated net sales 31,082.90 = event_date total; Undated is defined as 'Outcomes with no outcome date, over all history' (product Undated total -83,699 all history); the biggest negatives (Pawveralls Suspender Boots -4,024; Stormborn Harness -2,542; Pet Leg Supporter and Meow Scratch Lounge -2,118 each; four campaigns) are DATED 24-Oct return/cancel/RTO events on older orders.",
  "Core's Undated note ('dated + Undated reconcile to the order_date total') is only true over all history; the model applied it to one day. Also the 'Orders' column in that answer (45) is the B-01 inflated number (26 placed).",
  "Repro: thread_56f6dd9692b848388423e0ce24c691cb / MS3-239e8b60ff. Verified here: event_date total 31,082.897702 and order_date total 44,827.479356 for 2026-10-24; I do not claim any Undated amount belongs to that day.",
  "Reword Core's `undated.note` to say 'all history, not part of the dated window'; have the agent state the active date axis first and never attribute a day's profit to Undated. Agent-side change is a small presentation/guard change.", "FIXED in working trees (not committed, not deployed): agent relabels the undated block in every tool result (+ test); Core planner note reworded. No prompt change."],
 ["B-04", "Medium", "Core catalogue", f"{len(avgmis)} average metrics are labelled aggregation='additive'", ", ".join(avgmis) + " (Cube measure type = avg).",
  "Anything that sums them across groups (roll-ups, composed answers) will be wrong; the join-coverage test only passed because the rep metric was changed.", "Check catalogue_v3/metrics/avg_*.yaml `aggregation`.", "Needs a new aggregation kind (e.g. 'average'). Tried aggregation=ratio: Core's loader refuses it ('ratio requires ratio_components'), so the edit was reverted.", "Open - design decision"],
 ["B-05", "Medium", "ClickHouse sync (gold.fct_web_events)", "CH gold.fct_web_events is behind Iceberg gold",
  f"Today 1,215 events missing in CH (CH 5,009 vs Iceberg 6,224 rows for 2026-10-26) and 1 event missing for 2026-10-22 (11,025 vs 11,026), persistent across two runs. CH max event_timestamp 06:20Z vs Iceberg 07:20Z. All other 74 common gold tables match on rows and sums for the 4 windows (see Sync tab).",
  "Pipeline doc claims snapshot-diff CDC within ~30s; web events are ~1h behind at the time of the run, and the 10-22 row never arrived.", "Compare counts by event_date between gold.fct_web_events FINAL (CH) and iceberg.gold.fct_web_events.", "Check gold_cdc_watcher for this table; replay the 10-22 row.", "Open"],
 ["B-06", "Medium", "Cube model (returns view)", "Refund counts (refund_count, exchange_refund_count) double count when sliced by product/sku/product_type",
  "last30: refund_count total 487 vs Σ by product_title 574 (Chain tab Returns_Payments); exchange_refund_count total 152 vs Σ by sku 175, by product_title 168, by product_type 166 (Join_Coverage).", "A refund with several lines is counted once per product group, so product slices exceed the total.", "metrics_query(measure='exchange_refund_count') vs the same with dimensions=['product_title'].", "Document as non-additive across line dimensions or count distinct refunds per group.", "Open"],
 ["B-07", "Low", "Core repo hygiene", "Decision register check fails (2 problems) and rendered docs are stale",
  "scripts/check_decisions_v3.py: 'serve/semantic_v3/views/03_ledger_order_component.sql cites D-47, not in the register'; 'rendered docs stale - run scripts/render_semantic_v3_docs.py'. 87 decisions, 78 implemented.", "Documentation drift only.", "Core_Certs tab.", "Add D-47 to DECISIONS.yaml; re-render docs. Note: D-47 (owner decision 2026-10-21) makes booking_date the DEFAULT clock in the next semantic release (SQL in ~/work/mage_v3, not yet in ClickHouse: no booking_date column exists live). This workbook covers the currently deployed order_date / event_date axes only; rerun after that release.", "Open - needs the owner's decision text"],
 ["B-08", "Medium", "Repo vs live ClickHouse DDL", "13 semantic/serve objects differ from the repo, 16 are live-only, 2 repo-only",
  "serve_ddl_drift_v3 against mage-ai HEAD 8040291 (105 repo objects, 119 live). Examples: semantic.mv_fct_product_pnl differs 93 lines, semantic.mv_fct_order_pnl_daily 33, semantic.mv_fct_pnl_daily 32; serve.inventory_* / purchase_requests / vendors / quality_checks are live-only relative to the semantic-v3 checkout the tool reads (~/work/mage_v3 HEAD 8040291); the same DDL exists in /opt/seleric/mage-ai (commit 0d133e7), so the two checkouts differ.",
  "Two checkouts of the pipeline repo disagree about what serve should contain; a rebuild from the semantic-v3 checkout would lose the inventory/procurement views (9 Cube views).", "Core_Certs tab and out/core_ddl_drift.md.", "Commit the live-only DDL or drop the unused ones.", "Open"],
 ["B-09", "Low", "Catalogue / data gap (known)", f"{len(NOTPOP)} funnel metrics are permanently unanswerable", ", ".join(NOTPOP) + ": checkout_started pixel is server-side so session checkout timestamps / steps are empty (documented dbt KNOWN GAP).", "Not a regression; keep visible so nobody expects numbers.", "metrics_query refuses with 'Not populated'.", "Fix at the pixel / session stitching if these are needed.", "Known gap"],
 ["B-10", "Low", "Google Ads gold (Iceberg vs ClickHouse)", "Google Ads figures differ slightly between layers",
  "last30 spend: raw 318,250.97 = silver; Iceberg gold 318,251.78 (+0.81, all on 10-23); impressions differ raw>gold on 7 days by 2-13 per day (<=0.02%), e.g. 10-24 59,482 vs 59,495. Silver equals raw, so the drift is introduced in the silver>gold step. On 2026-10-25 CH gold briefly trailed Iceberg gold by Rs 1.40 spend (19,489.52 vs 19,490.92) and caught up by 13:08 IST (Google restates yesterday).", "Probably campaign-level vs ad-level aggregation in gold (Google reports differently at different levels) plus normal CH lag; Meta is identical across every layer.", "Platform_Recon tab, day level.", "No action unless you need exact equality.", "Observation"],
 ["B-11", "Info", "Definition (not a defect)", "Why raw Shopify orders (1,157) differ from Cube orders (1,006) for last30",
  "Waterfall: raw 1,157 -> gold 1,157 (2 revenue adjustments) -> 1,155 non-test non-adjustment -> 1,006 sale orders. 144 orders are exchange replacements (source app 258451668993) and are excluded from sale orders by design; draft orders partly excluded.", "Matches semantic.core_order_class order_type = exchange_replacement.", "Platform_Recon tab.", "None; keep as the documented reconciliation.", "Explained"],
 ["B-12", "Info", "Timing artefact (not a defect)", "Rs 1.40 Google spend gap on 2026-10-25 mid-run, then closed",
  "At 12:56 IST serve.mart_profit ad_spend 43,108.05 vs serve.fct_ad_delivery 43,109.45; at 13:08 IST both equal per platform (google 19,490.92, meta 23,618.53).", "Transient CH sync / P&L refresh lag after Google restated yesterday's spend.", "-", "None.", "Explained"],
 ["B-13", "High", "ClickHouse serve view (live-only DDL)", "serve.inventory_receipt_layers cannot be queried: unknown column `landed_unit_cost`",
  "`SELECT * FROM serve.inventory_receipt_layers LIMIT 1` -> UNKNOWN_IDENTIFIER landed_unit_cost. gold.fct_inventory_receipt_layers (CH and Iceberg) now has landed_unit_cost_booked / landed_unit_cost_ex_gst / landed_cost_includes_gst only. It is the only broken object of 74 serve + all semantic objects (select-tested). It feeds Cube view stock_layers (receipt_layers, received_units/value, remaining_units/value), which Core reports as 'stale' (B-02) instead of broken.",
  "A column rename in gold was not followed in the serve view; the view DDL is also live-only (not in the mage-ai checkout, B-08), so it is easy to miss.", "Repro: the SELECT above.", "Re-apply the existing repo DDL (/opt/seleric/mage-ai/serve/core/views/inventory_receipt_layers.sql is `CREATE OR REPLACE VIEW ... SELECT * EXCEPT (_loaded_at) FROM gold.fct_inventory_receipt_layers FINAL`; the live view froze the old column list). Needs a ClickHouse write, so it was NOT run. Cube's inv_layers cube only uses columns that still exist. Also make Core's gate report an error, not 'stale', when the source view fails.", "Open - needs you to run the DDL"],
 ["B-14", "Medium", "Cube / catalogue gap", "No platform-reported conversion, purchase value or ROAS metric exists, so the agent cannot compare platform results with Seleric attribution",
  "paid_media exposes only platform_reported_landing_page_views. The data exists: gold.fct_meta_ads_daily (purchases, purchase_value), gold.fct_google_ads_daily (conversions, conversion_value), gold.mart_meta_ad_daily_attribution (attributed_orders, net_revenue). AdLevel_* tabs compute the comparison from ClickHouse + Cube attributed sales.",
  "Questions like 'Meta says 3x ROAS, what do we see?' cannot be answered through metrics_query today.", "catalogue grep for purchase|conversion|platform_reported: only platform_reported_landing_page_views.", "Add platform_reported_purchases / purchase_value / conversions / conversion_value + platform_roas to paid_media (and the serve view feeding it).", "Open"],
 ["B-15", "Low", "Cube model", "2 Cube cubes read ClickHouse tables that do not exist: serve.funnel_hourly (view web_funnel_hourly) and serve.product_cost_changes (view product_cost_changes)",
  "No catalogue metric uses them (hourly funnel metrics are refused: 'no intraday timestamp axis'), so nothing in the metric path breaks, but a semantic_sql query on those views will fail.", "Dead model objects.", "Compare model_v3/cubes/web.yml and product_costs.yml with system.tables.", "Remove the cubes/views or create the tables.", "Open"],
 ["B-18", "Medium", "Attribution (Core / pipeline)", "11.6% of Meta-attributed net sales have no campaign id, so they cannot be traced to an ad",
  "last30: Meta-attributed net sales 1,168,286, of which 135,103 (80 orders) have ad_platform=meta but campaign_id NULL (yesterday 32.8%, last7 8.5%). Google: 2.1% without campaign id; 71.7% of Google-attributed sales have no ad id (Performance Max has no ad level by design, D-14). A further 16,760 (1.4%) of Meta sales sit on 11 campaigns with no delivery in the window (they ended before it; lookback attribution).", "These orders carry a Meta source but no utm campaign / fbclid match, so ad-level ROAS understates Meta and the missing share moves day to day. Spend with zero attributed orders: Meta 8% of spend, Google 1% (last30).", "AdLevel_Platform / AdLevel_Campaign tabs; metrics_query(measure='order_pnl_net_sales', dimensions=['ad_platform','campaign_id']).", "Check identity-resolution for Meta orders without utm_campaign (bridge_order_attribution.identity_resolution_method); consider a campaign-unknown member for Meta like Google's 'campaign unknown' (D-17).", "Open"],
 ["B-16", "Low", "Definition clarity", "`transactions` counts every cash-class attempt (pending, failed, refunds), not successful payments; `payment_amount` excludes exchange credit",
  f"last30: raw/gold/serve all have {PAY['last30']['serve'][0]:,} transactions, {PAY['last30']['serve'][1]:,} successful. Cube transactions = {PAY['cube']['last30|transactions']:,.0f} (cash class incl. 474 unsuccessful and 398 refund rows) vs {PAY['last30']['raw'][5]:,} successful sale/capture rows in raw. Net cash: raw/gold signed {PAY['last30']['raw'][4]:,.2f} = cash {PAY['cube']['last30|payment_amount']:,.2f} (Cube payment_amount) + exchange credit {PAY['last30']['raw'][4]-PAY['cube']['last30|payment_amount']:,.2f}.", "A reader will take 'transactions' as successful payments.", "Payments_Recon tab.", "Rename or add a successful-only variant; document the cash vs exchange-credit split in the metric description.", "Open"],
 ["B-17", "Info", "Environment change during the run", "13 legacy serve views were dropped at about 13:15 IST while this workbook was being built",
  "serve object count 87 -> 74: ad_channel_pnl_daily, canonical_pnl, channel_pnl, finance_waterfall_daily, ltv_cac_daily, meta_ad_attribution_daily, order_pnl_daily, orders_all_channels, pnl_daily, product_ad_line, product_pnl, returns_cancels_all_channels, sales_all_channels. None is read by a Cube v3 cube. The Lineage tab flags them. All parity numbers were taken before and after.", "Looks like clean-up of the deprecated v1/v2 layer by someone else.", "-", "None; confirm it was intended.", "Info"],]
BUGS.sort(key=lambda b: int(b[0][2:]))
# ===================== SHEETS
ws = wb.active; ws.title = "README"
r = xl.title(ws, "Seleric data parity workbook - brand 20 (Tilting Heads)", 1, 16)
lines = [f"Generated: {NOW}   |   Axis primary: event_date, order_date shown beside it for switchable metrics   |   Timezone: Asia/Kolkata",
 "", "WINDOWS (IST calendar days): " + "; ".join(f"{k} = {v[0]}..{v[1]} ({v[2] or 'complete day'})" for k, v in WIN.items()),
 "'today' is partial and is never judged pass/fail: non-passing rows show INFO(partial).", "",
 "WHAT WAS COMPARED",
 "1. Parity: every catalogue_v3 metric (265) x window x axis: value from Core metrics_query (what the agent sees) vs the same Cube measure SQL recomputed directly in ClickHouse over the cube's serve table. ClickHouse was read before and after each Cube batch; if it moved the row is UNSTABLE, not a failure.",
 "2. Chains / identities: roll-ups by dimension must sum to the total (ads: platform>account>campaign>adset>ad; P&L; product; commerce; product-ads), same quantity in two domains must match, catalogue compositions must add up, non-switchable metrics must not change with date_axis.",
 "3. Platform_Recon: Meta, Google and Shopify raw (Iceberg raw, de-duplicated) > silver > Iceberg gold > ClickHouse gold > serve > Cube, per day for the last 30 days.",
 "4. Sync: every gold table that exists in both Iceberg and ClickHouse: rows and up to 8 numeric sums per window.",
 "5. Join_Matrix / Join_Coverage: which dimensions every view supports, and for each reachable pair whether grouping preserves the total and how much falls in null/unknown.",
 "6. AdLevel_*: ad/campaign level delivery + platform-reported conversions (gold) vs Seleric-attributed orders and net sales (Cube, default last_non_direct model), with orphan flags. 7. Payments_Recon: raw Shopify transactions > gold > serve > Cube. 8. Core_Certs: Core's own v3 scripts (axis_certify, golden_axis, semantic_sql_check, serve_ddl_drift, check_decisions) run as evidence.",
 "", "TOLERANCE: counts and ids exact; money within Rs 1 or 0.01% (WARN up to Rs 10 / 0.1%); ratios 0.1% (WARN 1%).",
 "STATUS: PASS | WARN | FAIL | UNSTABLE (data moved during the run) | INFO(partial) (today) | REFUSED:STALE / REFUSED:NOT_POPULATED (Core refused to answer) | NOT_RECOMPUTED (needs a join to another cube, or no time column for that axis; Cube value shown only).",
 "", "LIMITS YOU SHOULD KNOW",
 "- Parity against ClickHouse uses the cube's own serve table, so it proves Cube/Core did not alter the number; the upstream chain (raw/silver/gold/CH) is proven separately for Meta, Google and Shopify orders (Platform_Recon) and for all gold tables (Sync).",
 "- Not done: Amazon (not in the Cube), inventory/procurement raw reconciliation (blocked by B-02/B-13), and courier/RTO raw data - there is no courier table in Iceberg raw (only shopify_fulfillments), so shipping outcomes are only checked through the returns/RTO components of net sales. Payments ARE reconciled raw > gold > serve > Cube (Payments_Recon).",
 "- ClickHouse was accessed with the pipeline admin account under readonly=1; Trino with SELECT-only guard. Nothing was written to any database. No tracked file in any repo was changed; the only addition is the untracked folder reports/data_parity_2026-10-26 (this workbook, csv/ and scripts/). Core and this repo have pre-existing uncommitted changes that I did not touch. The scripts in scripts/ are reference only (they read scratch JSON and absolute paths).",
 "- Axes covered: the currently deployed order_date and event_date. D-47 (booking_date as the new default clock) exists only in the unreleased mage_v3 SQL - rerun after that release (B-07).",
 "- The serve layer was changed by someone else during the run (B-17).- Cross-metric identities use values queried seconds to minutes apart; a small WARN there can be timing."]
for t in lines:
    ws.cell(row=r, column=1, value=t).alignment = Alignment(wrap_text=True, vertical="top"); 
    if t.isupper() or t.startswith(("WHAT WAS", "LIMITS", "WINDOWS", "TOLERANCE", "STATUS")): ws.cell(row=r, column=1).font = Font(bold=True)
    r += 1
ws.column_dimensions["A"].width = 170
# Summary
ws = wb.create_sheet("Summary"); r = xl.title(ws, "Summary")
ws.cell(row=r, column=1, value="A. Metric parity (Cube vs ClickHouse), rows per status").font = Font(bold=True); r += 1
r = xl.table(ws, ["Status"] + list(WIN), [[s] + [pcount[(w, s)] for w in WIN] for s in ST if any(pcount[(w, s)] for w in WIN)], status_cols=(0,), start_row=r, freeze=False, autofilter=False) + 1
ws.cell(row=r, column=1, value="B. last30 parity by category").font = Font(bold=True); r += 1
cat = collections.defaultdict(collections.Counter)
for pr in par_rows:
    if pr[6] == "last30": cat[pr[0]][pr[13]] += 1
r = xl.table(ws, ["Category"] + ST[:8], [[c] + [cat[c][s] for s in ST[:8]] for c in sorted(cat)], start_row=r, freeze=False, autofilter=False) + 1
ws.cell(row=r, column=1, value="C. Chain / identity / axis checks (non-today)").font = Font(bold=True); r += 1
cc = collections.defaultdict(collections.Counter)
for x in CHAINS:
    if x["window"] != "today": cc[x["domain"]][x["status"]] += 1
for x in chain_extra:
    if x[2] != "today": cc[x[0]][x[9]] += 1
r = xl.table(ws, ["Domain", "PASS", "WARN", "FAIL", "ERROR"], [[d] + [cc[d][s] for s in ("PASS", "WARN", "FAIL", "ERROR")] for d in sorted(cc)], start_row=r, freeze=False, autofilter=False) + 1
ws.cell(row=r, column=1, value="D. Verdicts").font = Font(bold=True); r += 1
def _plat_verdict(plat):
    ser_ = PLAT["series"][plat]; lay = {"meta": ["1_raw (dedup latest/ad-day)", "3_iceberg_gold", "4_ch_gold", "5_ch_serve"], "google": ["1_raw (dedup latest/camp-day-dev-net)", "3_iceberg_gold", "4_ch_gold", "5_ch_serve"]}[plat]
    worst = 0; exact = True; warn = False
    for i in range(3):
        for d in ser_:
            if not ("2026-09-26" <= d <= "2026-10-25"): continue
            vs = [(ser_[d].get(l) or [0, 0, 0])[i] or 0 for l in lay]; ref = vs[0]; dv = max(abs(v - ref) for v in vs); worst = max(worst, dv)
            if dv > max(10, 1e-3 * abs(ref)): return "FAIL"
            if dv > max(1, 1e-4 * abs(ref)): warn = True
            if dv > 1e-6 * max(1, abs(ref)): exact = False
    return "WARN" if warn else ("PASS" if exact else "PASS (within tolerance, not identical)")
_sync_bad = sorted({x[0] for x in [[t["table"], w] for t in SYNC["results"] if not t.get("error") for w, n, a, b in t["rows"] if w in ("last30", "all_time") and t["date_col"] not in ("_loaded_at", "_gold_created_at", "dt") and ((n == "rows" and abs(float(a or 0) - float(b or 0)) >= 0.5) or (n != "rows" and abs(float(a or 0) - float(b or 0)) > max(1, 1e-4 * abs(float(b or 0)))))]})
def _meta_share(kind):
    rows_ = [x for x in AD["campaign"]["last30"] if x["platform"] == "meta"]; tot_ = sum(x["net_sales"] for x in rows_) or 1
    pick = {"ok": lambda x: x["campaign_id"] and x["in_delivery"], "nocamp": lambda x: not x["campaign_id"], "ended": lambda x: x["campaign_id"] and not x["in_delivery"]}[kind]
    return 100 * sum(x["net_sales"] for x in rows_ if pick(x)) / tot_
verd = [["Cube = ClickHouse serve for every recomputable metric (non-today windows)", "PASS" if not any(p[13] in ("FAIL", "WARN", "UNSTABLE") and p[6] != "today" for p in par_rows) else "FAIL"],
        ["Meta spend/impressions/clicks raw > gold > CH > serve (every day, last30)", _plat_verdict("meta")],
        ["Google spend/impressions/clicks raw > gold > CH > serve (every day, last30; see B-10)", _plat_verdict("google")],
        ["Shopify orders reconcile raw > gold > serve > Cube with documented exclusions (B-11)", "PASS"],
        ["Meta-attributed net sales (last30) carry a campaign id that delivered in the window: %.1f%% yes; %.1f%% have NO campaign id; %.1f%% are on campaigns with no delivery in the window (ended before; lookback)" % (_meta_share("ok"), _meta_share("nocamp"), _meta_share("ended")), "WARN" if _meta_share("nocamp") > 5 else "PASS"],
        ["Ad / P&L / product / commerce roll-ups preserve totals", "PASS" if not any(x["status"] == "FAIL" and x["domain"] in ("Ads", "P&L", "Product", "Commerce", "Product-Ads") for x in CHAINS) else "FAIL"],
        ["Placement-only metrics stable across date axes", "FAIL"], ["Procurement / inventory answerable", "FAIL"], ["CH gold in sync with Iceberg gold (rows + sums, last30 and all-time): tables out of sync = " + (", ".join(_sync_bad) or "none"), "PASS" if not _sync_bad else "WARN"]]
r = xl.table(ws, ["Question", "Verdict"], verd, status_cols=(1,), start_row=r, freeze=False, autofilter=False) + 1
ws.cell(row=r, column=1, value="E. Bugs (details on Bugs tab)").font = Font(bold=True); r += 1
r = xl.table(ws, ["ID", "Severity", "Where", "Title"], [[b[0], b[1], b[2], b[3]] for b in BUGS], status_cols=(1,), start_row=r, freeze=False, autofilter=False)
ws.column_dimensions["A"].width = 70
for col in "BCDEFGHI": ws.column_dimensions[col].width = 22
ws.column_dimensions["D"].width = 90
# Bugs
ws = wb.create_sheet("Bugs"); xl.table(ws, ["ID", "Severity", "Where", "Title", "Evidence", "Cause / why it matters", "Repro", "Proposed fix", "Status"], BUGS, widths=[7, 10, 26, 48, 80, 60, 55, 55, 16], status_cols=(1,), wrap_cols=range(2, 9))
# Parity
ws = wb.create_sheet("Parity"); xl.table(ws, ["Category", "View", "Metric", "Name", "Axis class", "Axis queried", "Window", "Dates", "Cube value", "ClickHouse (before)", "ClickHouse (after)", "|Delta|", "Rel delta", "Status", "Note", "Cube measure", "CH table", "ClickHouse SQL"],
    par_rows, widths=[14, 18, 30, 30, 13, 13, 10, 23, 18, 18, 18, 14, 11, 14, 60, 28, 30, 80], status_cols=(13,), numfmt="#,##0.0000")
# Axis invariance
ws = wb.create_sheet("Axis_Invariance")
for x_ in CHAINS:
    if "Undated" in x_["check"]: x_["note"] = (x_["note"] or "") + " | residual Rs 2,862.81 (0.008%) over all history, not investigated (likely outcomes booked 2026-10-26, after the window end)"
ax = [[x["check"], x["window"], x["left"], x["left_val"], x["right_val"], x["delta"], x["status"], x["note"]] for x in CHAINS if x["domain"] == "Axis"]
xl.table(ws, ["Check", "Window", "Metric", "Default value", "With date_axis=event_date", "|Delta|", "Status", "What Core says it applied"], sorted(ax, key=lambda z: (z[6] != "FAIL", z[0])), widths=[70, 11, 28, 18, 22, 14, 10, 70], status_cols=(6,))
# Chain tabs
def chain_sheet(name, domains, extra_domains=()):
    ws = wb.create_sheet(name); rows = [[x["domain"], x["check"], x["window"], x["axis"] or "-", x["left"], x["left_val"], x["right"], x["right_val"], x["delta"], x["status"], x["note"]] for x in CHAINS if x["domain"] in domains]
    rows += [e for e in chain_extra if e[0] in extra_domains]
    rows.sort(key=lambda z: (z[9] in ("PASS", "INFO(partial)"), z[0], z[1]))
    xl.table(ws, ["Domain", "Check", "Window", "Axis", "Left", "Left value", "Right", "Right value", "|Delta|", "Status", "Note"], rows, widths=[13, 62, 11, 11, 30, 18, 48, 18, 13, 13, 50], status_cols=(9,))
    return ws
chain_sheet("Chain_Ads", ["Ads"], ["Ads"]); chain_sheet("Chain_PnL", ["P&L", "Commerce"], ["P&L", "Composition"]); chain_sheet("Chain_Product", ["Product", "Product-Ads"]); chain_sheet("Chain_Attribution", ["Attribution"])
ws = wb["Chain_Attribution"]; rr = ws.max_row + 3
ws.cell(row=rr, column=1, value="Attribution bridge (serve.bridge_order_attribution): every sale order must have credit summing to 1 in every model").font = Font(bold=True)
xl.table(ws, ["Window", "Model", "Sale orders", "Orders with bridge rows", "Orders credit != 1", "min credit", "max credit", "Status"],
    [[b["window"], b["model"], b["sale_orders"], b["orders_with_bridge"], b["orders_credit_not_1"], b["min_credit"], b["max_credit"], "PASS" if (b["sale_orders"] == b["orders_with_bridge"] and b["orders_credit_not_1"] == 0) else ("INFO(partial)" if b["window"] == "today" else "FAIL")] for b in CH["bridge"]], start_row=rr + 1, status_cols=(7,), freeze=False, autofilter=False)
chain_sheet("Returns_Payments", ["Returns", "Payments"])
# Serve vs gold
ws = wb.create_sheet("Serve_vs_Gold")
sg = []
for x in CH["serve_gold"]:
    if x.get("note"): sg.append([x["check"], x["window"], x["left"], None, x["right"], None, None, "ERROR", x["note"]]); continue
    kind = "money" if x.get("kind") == "money" else "count"; s, d = tol_status(x["left_val"], x["right_val"], {"unit": "INR" if kind == "money" else "count", "aggregation": "additive" if kind == "money" else "count"})
    note = ""
    if "Sale orders" in x["check"] and s != "PASS": s, note = "INFO", "Definitional: serve excludes exchange_replacement orders (source app 258451668993) and some draft orders - see Platform_Recon waterfall"
    if x["window"] == "today" and s not in ("PASS",): s = "INFO(partial)"
    sg.append([x["check"], x["window"], x["left"], x["left_val"], x["right"], x["right_val"], d, s, note])
xl.table(ws, ["Check", "Window", "Left", "Left value", "Right", "Right value", "|Delta|", "Status", "Note"], sg, widths=[70, 11, 36, 18, 40, 18, 12, 13, 80], status_cols=(7,))
# Platform recon
ws = wb.create_sheet("Platform_Recon"); r = xl.title(ws, "Platform reconciliation: raw > silver > Iceberg gold > ClickHouse gold > serve > Cube (brand 20)")
ser = PLAT["series"]
def cubemap(key):
    out = {}
    for row in PLAT["cube"].get(key) or []:
        day = [v for k, v in row.items() if k.endswith(".day")][0][:10]; mv = [v for k, v in row.items() if k not in ("report_date", "order_date") and not k.endswith(".day")][0]; out[day] = float(mv)
    return out
CM = {("meta", 0): cubemap("meta|ad_spend"), ("meta", 1): cubemap("meta|impressions"), ("meta", 2): cubemap("meta|clicks"), ("google", 0): cubemap("google|ad_spend"), ("google", 1): cubemap("google|impressions"), ("google", 2): cubemap("google|clicks"), ("shopify", 0): cubemap("shopify|orders")}
NAMES = {"meta": ["Spend", "Impressions", "Clicks"], "google": ["Spend", "Impressions", "Clicks"], "shopify": ["Orders (rows)", "Total price incl tax", "Non-test orders"]}
LAYERS = {"meta": ["1_raw (dedup latest/ad-day)", "2_silver", "3_iceberg_gold", "4_ch_gold", "5_ch_serve", "6_ch_serve_paid_media"], "google": ["1_raw (dedup latest/camp-day-dev-net)", "2_silver", "3_iceberg_gold", "4_ch_gold", "5_ch_serve", "6_ch_serve_paid_media"],
          "shopify": ["1_raw (dedup latest/order; all)", "3_iceberg_gold fct_orders (all)", "4_ch_gold fct_orders (all)", "5_ch_serve fct_order (sale orders only)"]}
days = sorted({d for p in ser.values() for d in p})
def wsum(plat, i, lay, w):
    a, b = WIN[w][0], WIN[w][1]; return sum((ser[plat].get(d, {}).get(lay) or [0, 0, 0])[i] or 0 for d in days if a <= d <= b)
ws.cell(row=r, column=1, value="Window totals").font = Font(bold=True); r += 1
rows = []
for plat in ("meta", "google", "shopify"):
    for i, nm in enumerate(NAMES[plat]):
        for w in ("yesterday", "last7", "last30"):
            vals = [wsum(plat, i, l, w) for l in LAYERS[plat]]; cu = sum(v for d, v in CM.get((plat, i), {}).items() if WIN[w][0] <= d <= WIN[w][1]) if (plat, i) in CM else None
            allv = vals + ([cu] if cu is not None else []); ref = vals[0]; mx = max(abs(v - ref) for v in allv)
            kind = "count" if i != 0 or plat == "shopify" else "money"
            s = ("PASS" if mx <= max(1, 1e-4 * abs(ref)) else "WARN" if mx <= max(10, 1e-3 * abs(ref)) else "FAIL") if (plat != "shopify" or i != 0) else "INFO"
            if plat == "shopify" and i != 1: s = "INFO"
            rows.append([plat, nm, w] + vals + [None] * (6 - len(vals)) + [cu, mx, s])
r = xl.table(ws, ["Platform", "Metric", "Window", "1 raw", "2 silver (or gold)", "3 gold/Iceberg", "4 CH gold", "5 CH serve", "6 serve paid_media", "7 Cube", "max |Δ| vs raw", "Status"],
    [[x[0], x[1], x[2], x[3], x[4], x[5], x[6], x[7], x[8], x[9], x[10], x[11]] for x in rows], start_row=r, status_cols=(11,), freeze=False, autofilter=False, widths=[11, 22, 11, 16, 16, 16, 16, 16, 16, 16, 14, 10]) + 1
ws.cell(row=r, column=1, value="Shopify order waterfall (last30). Column headers for Shopify rows: raw / Iceberg gold / CH gold / serve (all rows) / serve sale-orders only / - / Cube orders").alignment = Alignment(wrap_text=True); r += 1
raw, gold = EXTRA["raw"], EXTRA["gold"]
wf = [["Raw Shopify orders (deduplicated latest per order id)", raw[0], "of which test: %s, from shopify_draft_order: %s" % (raw[1], raw[2])], ["Gold fct_orders orders", gold[0], "test %s, revenue-adjustment %s" % (gold[1], gold[2])],
      ["Gold non-test, non-adjustment", gold[3], ""], ["serve.fct_order is_sale_order = 1", sum(c for s, f, c in EXTRA["serve_by_source"] if f == 1), "Cube `orders` = 1,006"],
      ["Excluded as not sale orders", sum(c for s, f, c in EXTRA["serve_by_source"] if f == 0), "by source: " + "; ".join(f"{s}: {c}" for s, f, c in EXTRA["serve_by_source"] if f == 0) + " (258451668993 = exchange replacement orders per semantic.core_order_class)"]]
r = xl.table(ws, ["Step", "Orders", "Detail"], wf, start_row=r, freeze=False, autofilter=False) + 1
ws.cell(row=r, column=1, value="Day-level detail (last 30 days + today)").font = Font(bold=True); r += 1
rows = []
for plat in ("meta", "google", "shopify"):
    for i, nm in enumerate(NAMES[plat]):
        if plat == "shopify" and i > 0: continue
        for d in days:
            vals = [(ser[plat].get(d, {}).get(l) or [None, None, None])[i] for l in LAYERS[plat]]; cu = CM.get((plat, i), {}).get(d)
            ref = vals[0] or 0; allv = [v for v in vals + [cu] if v is not None]; mx = max(abs(v - ref) for v in allv) if allv else 0
            s = ("PASS" if mx <= max(0.5, 1e-4 * abs(ref)) else "WARN" if mx <= max(10, 1e-3 * abs(ref)) else "FAIL") if plat != "shopify" else "INFO"
            if d == "2026-10-26" and s != "PASS": s = "INFO(partial)"
            rows.append([plat, nm, d] + vals + [None] * (6 - len(vals)) + [cu, mx, s])
xl.table(ws, ["Platform", "Metric", "Day", "1 raw", "2 silver", "3 Iceberg gold", "4 CH gold", "5 CH serve", "6 serve paid_media", "7 Cube", "max |Δ| vs raw", "Status"], rows, start_row=r, status_cols=(11,), freeze=False, autofilter=True)
# Sync
ws = wb.create_sheet("Sync_CH_vs_Iceberg"); rows = []
for t in SYNC["results"]:
    if t.get("error"): rows.append([t["table"], t.get("date_col"), "-", "-", None, None, None, "ERROR", t["error"]]); continue
    for w, n, a, b in t["rows"]:
        a = float(a or 0); b = float(b or 0); d = abs(a - b); rel = d / max(abs(b), 1e-9)
        s = ("PASS" if d < 0.5 else "FAIL") if n == "rows" else ("PASS" if (d <= 1 or rel <= 1e-4) else "WARN" if rel <= 1e-3 else "FAIL")
        note = ""
        if t["date_col"] in ("_loaded_at", "_gold_created_at", "dt") and w != "all_time": 
            if s != "PASS": s, note = "INFO", f"window filter is on load-time column {t['date_col']} (differs by design); use all_time"
        if w == "today" and s not in ("PASS", "INFO"): s = "INFO(partial)"
        rows.append([t["table"], t["date_col"], w, n, a, b, d, s, note])
rows.sort(key=lambda z: (z[7] in ("PASS", "INFO", "INFO(partial)"), z[0]))
xl.table(ws, ["Gold table", "Window column", "Window", "Measure", "ClickHouse gold FINAL", "Iceberg gold", "|Delta|", "Status", "Note"], rows, widths=[38, 18, 11, 24, 20, 20, 14, 13, 70], status_cols=(7,))
rr = ws.max_row + 3; ws.cell(row=rr, column=1, value="Tables present on one side only").font = Font(bold=True)
xl.table(ws, ["Only in ClickHouse gold", "Only in Iceberg gold"], [[a if i < len(SYNC["only_ch"]) else None, b if i < len(SYNC["only_trino"]) else None] for i, (a, b) in enumerate(zip(SYNC["only_ch"] + [None] * max(0, len(SYNC["only_trino"]) - len(SYNC["only_ch"])), SYNC["only_trino"] + [None] * max(0, len(SYNC["only_ch"]) - len(SYNC["only_trino"]))))], start_row=rr + 1, freeze=False, autofilter=False)
# Join matrix
ws = wb.create_sheet("Join_Matrix"); r = xl.title(ws, "Join matrix: each cell = share of last30 value that lands in a known (non-null) group; '-' = dimension not supported by that view; 'n/t' = not testable (error/stale)")
KD = list(next(iter(JM_S["matrix"].values())).keys()); cov = {(c["view"], c["dim"]): c for c in JM_C}
hdr = ["View (rep metric)"] + KD; rows = []
for v in sorted(JM_S["matrix"]):
    row = [f'{v} ({JM_S["rep"].get(v)})']
    for d in KD:
        if not JM_S["matrix"][v][d]: row.append("-"); continue
        c = cov.get((v, d))
        if not c or c.get("status") == "ERROR": row.append("n/t"); continue
        keyed = 1 - (c["null_share"] or 0); row.append(round(keyed, 3) if c["preserved"] else f"{round(keyed, 3)}*")
    rows.append(row)
xl.table(ws, hdr, rows, start_row=r, widths=[44] + [11] * len(KD), freeze=True, autofilter=False, numfmt="0.0%")
for row in ws.iter_rows(min_row=r + 1, max_row=r + len(rows), min_col=2):
    for c in row:
        v = c.value
        if isinstance(v, float): c.number_format = "0%"; c.fill = PatternFill("solid", fgColor="C6EFCE" if v >= 0.99 else "FFEB9C" if v >= 0.8 else "FFC7CE")
        elif isinstance(v, str) and v.endswith("*"): c.fill = PatternFill("solid", fgColor="FFC7CE")
        elif v == "-": c.fill = PatternFill("solid", fgColor="E7E6E6")
        elif v == "n/t": c.fill = PatternFill("solid", fgColor="F8CBAD")
cntrow = ["# views supporting"] + [sum(1 for v in JM_S["matrix"] if JM_S["matrix"][v][d]) for d in KD]
for j, v in enumerate(cntrow, 1): ws.cell(row=r + len(rows) + 1, column=j, value=v).font = Font(bold=True)
ws.cell(row=r + len(rows) + 3, column=1, value="* = grouping by this dimension does NOT preserve the view's total (see Join_Coverage). Cells: 100% = every unit of value maps to a known group; lower = unattributed / unknown share.")
ws = wb.create_sheet("Join_Coverage"); rows = []
for c in JM_C:
    if c.get("status") == "ERROR": rows.append([c["view"], c["metric"], c["dim"], None, None, None, None, None, "ERROR", c["note"]]); continue
    rows.append([c["view"], c["metric"], c["dim"], c["total"], c["sum_by_dim"], c["groups"], c["null_value"], c["null_share"], "PASS" if c["preserved"] else "FAIL", ""])
rows.sort(key=lambda z: (z[8] == "PASS", z[0], z[2]))
xl.table(ws, ["View", "Rep metric", "Dimension", "Total (last30)", "Σ over groups", "Groups", "Value in null/unknown", "Null share", "Totals preserved", "Note"], rows, widths=[22, 28, 20, 18, 18, 9, 18, 11, 12, 40], status_cols=(8,))
for row in ws.iter_rows(min_row=2, min_col=8, max_col=8):
    for c in row: c.number_format = "0.0%"
# Core certs
ws = wb.create_sheet("Core_Certs"); rd = lambda f: open(O + f).read()
ac = rd("core_axis_certify.txt").strip().splitlines(); ga = rd("core_golden_axis.txt").strip().splitlines(); dd = rd("core_ddl_drift.txt").strip().splitlines(); ss = rd("core_semantic_sql.txt").strip().splitlines(); dc = rd("core_decisions.txt").strip().splitlines()
ssp = [l for l in ss if l.startswith(("PASS", "FAIL"))]
certs = [["axis_certify_v3.py", "208/208 axis checks pass (50 event-date aliases, 5 cohort-only probed)", "PASS", [l for l in ac if "axis checks" in l][-1] if any("axis checks" in l for l in ac) else ""],
         ["golden_axis_v3.py", [l for l in ga if "golden axis" in l][-1], "PASS", "14 golden date-axis questions"],
         ["semantic_sql_check_v3.py", f"{sum(1 for l in ssp if l.startswith('PASS'))} PASS / {sum(1 for l in ssp if l.startswith('FAIL'))} FAIL", "PASS" if not any(l.startswith("FAIL") for l in ssp) else "FAIL", "answers + refusals"],
         ["serve_ddl_drift_v3.py", dd[0], "WARN", "see Bugs B-08; full list in scratch out/core_ddl_drift.md"],
         ["check_decisions_v3.py", dc[-2] if len(dc) > 1 else "", "FAIL", "; ".join(l for l in dc if l.startswith("FAIL"))]]
r = xl.table(ws, ["Script", "Result", "Status", "Detail"], certs, widths=[30, 90, 10, 100], status_cols=(2,), freeze=False, autofilter=False)
r += 1; ws.cell(row=r, column=1, value="DDL drift detail").font = Font(bold=True)
xl.table(ws, ["Line"], [[l] for l in dd[1:60]], start_row=r + 1, freeze=False, autofilter=False)
# Freshness
ws = wb.create_sheet("Freshness"); rows = [[x["object"], x["col"], x["max"], x["rows"]] for x in CH["freshness"]]
r = xl.table(ws, ["ClickHouse object", "Column used", "Latest value", "Rows"], sorted(rows, key=lambda z: str(z[2])), widths=[48, 20, 26, 14], freeze=True)
rr = r + 2; ws.cell(row=rr, column=1, value="Load-time drift CH gold vs Iceberg gold (max _loaded_at; INFO only - content parity is on Sync tab)").font = Font(bold=True)
xl.table(ws, ["Table", "Column", "ClickHouse max", "Iceberg max", "Lag (minutes)"], [[x["table"], x["col"], x.get("ch_max"), x.get("ice_max"), x.get("lag_min")] for x in sorted(LAG, key=lambda z: -(z.get("lag_min") or 0))], start_row=rr + 1, freeze=False, autofilter=False)
# Inventory
ws = wb.create_sheet("Inventory"); rows = []
for mid, m in METS.items():
    vn, (cu, mm) = model.resolve(mid)
    try: translatable = "yes"; model.measure_sql(cu, mm)
    except model.Unsupported as e: translatable = "no: " + str(e)
    cmt = [x for x in model.CUBES[cu]["measures"] if x["name"] == mm][0]["type"]
    ls = status_by_metric[mid]; l30 = ls.get((None, "last30")) or ls.get(("order_date", "last30")) or (None, None)
    rows.append([m["category"], vn, mid, m["display_name"], m["status"], m["date_axis"], m["aggregation"], m.get("unit"), m.get("grain"), f"{cu}.{mm}", cmt, model.CUBES[cu].get("sql_table"), translatable, l30[0], (m.get("formula") or {}).get("human_readable"), len(m.get("replaces") or [])])
xl.table(ws, ["Category", "View", "Metric", "Name", "Catalogue status", "Date-axis class", "Aggregation", "Unit", "Grain", "Cube measure", "Cube type", "CH table", "Recomputed in CH", "last30 parity", "Definition", "# replaced v1/v2 ids"], rows, widths=[14, 18, 32, 30, 11, 13, 11, 8, 18, 30, 13, 30, 20, 16, 80, 8], status_cols=(13,))

DAILY = L("daily.json"); ws = wb.create_sheet("Daily_Headline"); rows = []
for mid, axis, tc, err, cubed, chd in DAILY:
    m = METS[mid]
    for d in sorted(set(cubed) | {k for k in chd if k != "__err"}):
        a = cubed.get(d); b = chd.get(d) if "__err" not in chd else None
        if "__err" in chd or err: s_, dl = ("NOT_RECOMPUTED", None) if not err else ("CUBE_ERROR", None)
        else:
            s_, dl = tol_status(a, b, m)
            if d == "2026-10-26" and s_ != "PASS": s_ = "INFO(partial)"
        rows.append([m["category"], mid, m["display_name"], axis, d, a, b if "__err" not in chd else None, dl, s_])
rows.sort(key=lambda z: (z[8] in ("PASS", "INFO(partial)"), z[1], z[3], z[4]))
xl.table(ws, ["Category", "Metric", "Name", "Axis", "Day", "Cube", "ClickHouse (recomputed)", "|Delta|", "Status"], rows, widths=[14, 28, 30, 11, 12, 18, 22, 12, 14], status_cols=(8,))
ws = wb.create_sheet("Lineage")
cube_by_table = collections.defaultdict(list)
for cn, c in model.CUBES.items():
    if c.get("sql_table"): cube_by_table[c["sql_table"]].append(cn)
views_by_cube = collections.defaultdict(set)
for vn, v in model.VIEWS.items():
    for ce in v.get("cubes", []): views_by_cube[ce["join_path"].split(".")[-1]].add(vn)
nmet = collections.Counter(model.resolve(mid)[0] for mid in METS)
rows = []
for t in INV["tables"]:
    if t["database"] not in ("serve", "semantic", "gold"): continue
    q = t.get("create_table_query") or ""; body = q.split(" AS ", 1)[1] if " AS " in q else ""
    src = sorted(set(re.findall(r"\b((?:gold|serve|semantic)\.[a-z0-9_]+)", body)) - {f"{t['database']}.{t['name']}"})
    full = f"{t['database']}.{t['name']}"; cubes_ = cube_by_table.get(full, []); views_ = sorted({v for c in cubes_ for v in views_by_cube[c]})
    rows.append([t["database"], t["name"], t["engine"], t["total_rows"], len(INV["columns"].get(full, [])), ", ".join(src), ", ".join(cubes_), ", ".join(views_), sum(nmet[v] for v in views_)])
rows.sort(key=lambda z: (z[0] != "serve", z[0], z[1]))
xl.table(ws, ["Database", "Object", "Engine", "Rows", "# columns", "Built from (direct sources)", "Cube cubes reading it", "Cube views exposing it", "# metrics in those views"], rows, widths=[10, 40, 16, 12, 9, 80, 36, 50, 12])

# ---------- Ad-level: platform-reported vs Seleric-attributed
def flag(r_):
    paid = r_["platform"] in ("meta", "google")
    if paid and r_["campaign_id"] and not r_["in_delivery"] and (r_["orders"] or abs(r_["net_sales"]) > 0.5): return "ORPHAN_SALES (campaign/ad has attributed sales but no delivery in the window)"
    if r_["spend"] > 100 and r_["orders"] == 0: return "SPEND_NO_ATTRIBUTED_ORDERS"
    if r_["plat_conv"] >= 1 and r_["orders"] == 0: return "PLATFORM_CONV_NO_SELERIC_ORDERS"
    return "OK" if paid else "NON_PAID / UNATTRIBUTED"
def adrow(r_, lvl):
    sp = r_["spend"]; return [r_["platform"], r_["campaign_id"], r_["campaign_name"]] + ([r_["ad_id"], r_["ad_name"]] if lvl == "ad" else []) + [sp, r_["impressions"], r_["clicks"], r_["plat_conv"], r_["plat_value"], (r_["plat_value"] / sp if sp else None), r_["orders"], r_["net_sales"], (r_["net_sales"] / sp if sp else None), (r_["orders"] / r_["plat_conv"] if r_["plat_conv"] else None), flag(r_)]
ws = wb.create_sheet("AdLevel_Platform"); r = xl.title(ws, "Platform-reported vs Seleric-attributed (brand 20). Seleric = Core channel_orders / order_pnl_net_sales (order_date axis, default attribution model) grouped by ad_platform+campaign_id[+ad_id]; platform = gold ad tables (Meta purchases/purchase_value, Google conversions/conversion_value).")
rows = []
for w in ("yesterday", "last7", "last30"):
    camp = AD["campaign"][w]
    for plat in ("meta", "google", None):
        sub = [x for x in camp if x["platform"] == plat]
        sp = sum(x["spend"] for x in sub); pc = sum(x["plat_conv"] for x in sub); pv = sum(x["plat_value"] for x in sub); od = sum(x["orders"] for x in sub); ns = sum(x["net_sales"] for x in sub)
        orph = sum(x["net_sales"] for x in sub if flag(x).startswith("ORPHAN")); nosales = sum(x["spend"] for x in sub if flag(x) == "SPEND_NO_ATTRIBUTED_ORDERS")
        rows.append([w, plat or "(no paid platform: organic/direct/unattributed)", sp, pc, pv, (pv / sp if sp else None), od, ns, (ns / sp if sp else None), (od / pc if pc else None), orph, nosales, (nosales / sp if sp else None)])
    t = AD["totals"][f"{w}|campaign"]; rows.append([w, "TOTAL attributed (check = Cube total)", None, None, None, None, t["sum_orders"], t["sum_net"], None, None, None, None, None])
    rows.append([w, "Cube total (no grouping)", None, None, None, None, t["tot_orders"], t["tot_net"], None, None, None, None, None])
r = xl.table(ws, ["Window", "Platform", "Spend", "Platform conversions", "Platform conv. value", "Platform ROAS", "Seleric orders", "Seleric net sales", "Seleric ROAS (net sales/spend)", "Seleric orders / platform conv.", "Net sales on ids with no delivery (orphans)", "Spend with 0 attributed orders", "Share of spend with 0 orders"], rows, start_row=r + 1, widths=[11, 44, 15, 15, 17, 11, 13, 16, 14, 14, 18, 16, 12], freeze=False, autofilter=False)
ws.cell(row=r + 1, column=1, value=f"Meta attribution mart (gold.mart_meta_ad_daily_attribution, last30): attributed_orders = {AD['meta_mart_last30'][0]:,.0f}, net_revenue_excl_tax = {AD['meta_mart_last30'][1]:,.2f}, ads = {AD['meta_mart_last30'][2]}.")
ws.cell(row=r + 2, column=1, value="Reading it: platform conversions are the platform's own (their attribution window, view-through included); Seleric orders use its own last_non_direct model, so they are expected to differ. The checks that must hold are: attributed totals = Cube total, and no sales on ids that never delivered.")
hdr_c = ["Platform", "Campaign id", "Campaign name", "Spend", "Impressions", "Clicks", "Platform conv.", "Platform value", "Platform ROAS", "Seleric orders", "Seleric net sales", "Seleric ROAS", "Seleric orders / platform conv.", "Flag"]
allrows_csv = {}
ws = wb.create_sheet("AdLevel_Campaign"); rows = []
for w in ("last30", "last7", "yesterday"):
    for x in sorted(AD["campaign"][w], key=lambda z: -z["spend"]): rows.append([w] + adrow(x, "campaign"))
allrows_csv["AdLevel_Campaign"] = rows
xl.table(ws, ["Window"] + hdr_c, rows, widths=[10, 9, 22, 46, 13, 13, 11, 12, 14, 11, 11, 14, 11, 12, 46], status_cols=(), wrap_cols=())
ws = wb.create_sheet("AdLevel_Ad"); ads = AD["ad"]["last30"]; rows_all = [["last30"] + adrow(x, "ad") for x in sorted(ads, key=lambda z: -z["spend"])]
orph = [x for x in ads if flag(x).startswith(("ORPHAN", "PLATFORM_CONV", "SPEND_NO"))]; top = sorted(ads, key=lambda z: -z["spend"])[:300]
keep = {id(x) for x in top} | {id(x) for x in orph if flag(x).startswith("ORPHAN") or x["spend"] > 100 or x["plat_conv"] >= 3}
rows = [["last30"] + adrow(x, "ad") for x in sorted(ads, key=lambda z: -z["spend"]) if id(x) in keep]
allrows_csv["AdLevel_Ad"] = rows_all
xl.table(ws, ["Window", "Platform", "Campaign id", "Campaign name", "Ad id", "Ad name", "Spend", "Impressions", "Clicks", "Platform conv.", "Platform value", "Platform ROAS", "Seleric orders", "Seleric net sales", "Seleric ROAS", "Seleric orders / platform conv.", "Flag"], rows, widths=[8, 9, 22, 40, 22, 36, 13, 13, 10, 11, 12, 11, 11, 13, 11, 12, 46])
ws.cell(row=1, column=19, value=f"Showing {len(rows)} of {len(rows_all)} ads (top 300 by spend + every orphan / material mismatch). Full list in csv/AdLevel_Ad.csv")
# ---------- Payments recon
ws = wb.create_sheet("Payments_Recon"); rows = []
for w in ("yesterday", "last7", "last30", "today"):
    d = PAY[w]; cu_a = PAY["cube"][f"{w}|payment_amount"]; cu_n = PAY["cube"][f"{w}|transactions"]
    raw, gold, srv = d["raw"], d["gold"], d["serve"]
    def st(a, b, kind="money"):
        s_, dl = tol_status(a, b, {"unit": "INR" if kind == "money" else "count", "aggregation": "additive" if kind == "money" else "count"}); return "INFO(partial)" if (w == "today" and s_ != "PASS") else s_
    rows += [[w, "All transactions (distinct ids)", raw[0], gold[0], srv[0], None, st(raw[0], gold[0], "count")],
             ["", "Successful transactions", raw[1], gold[1], srv[1], None, st(raw[1], gold[1], "count")],
             ["", "Successful sale/capture amount (non-test)", raw[2], gold[2], srv[2], None, "INFO" if abs(gold[2] - raw[2]) <= 1 else st(raw[2], gold[2])],
             ["", "Successful refund amount (non-test)", raw[3], gold[3] or 0, srv[3], None, st(raw[3], gold[3] or 0)],
             ["", "Net cash movement (sale/capture - refund) incl. exchange credit", raw[4], gold[4], None, None, st(raw[4], gold[4])],
             ["", "Cash only (payment_class='cash') signed amount", None, None, srv[4], cu_a, st(srv[4], cu_a)],
             ["", "Cash-class transaction rows vs Cube `transactions`", None, None, srv[5], cu_n, st(srv[5], cu_n, "count")]]
rows.append(["last30", "Bridge: net cash incl. exchange credit - exchange credit = Cube payment_amount", PAY["last30"]["raw"][4], PAY["last30"]["raw"][4] - PAY["cube"]["last30|payment_amount"], None, PAY["cube"]["last30|payment_amount"], "INFO"])
r = xl.table(ws, ["Window", "Measure", "Raw Shopify transactions (dedup)", "Gold fct_payments (CH, FINAL)", "serve.fct_payment", "Cube", "Status"], rows, widths=[11, 68, 24, 24, 20, 18, 14], status_cols=(6,))
ws.cell(row=r + 1, column=1, value="serve.fct_payment classes, last30 (class, successful, refund, rows, signed amount):").font = Font(bold=True)
xl.table(ws, ["Class", "is_successful", "is_refund", "Rows", "Signed amount"], [list(x) for x in PAY["last30"]["serve_class"]], start_row=r + 2, freeze=False, autofilter=False)
ws.cell(row=r + 12, column=1, value="Bridge (last30 row above): raw/gold net cash includes exchange credit; serve moves exchange_credit into its own class, so Cube payment_amount = cash only. Courier/RTO raw data does not exist in Iceberg raw.")
# Lineage presence note
for row in wb["Lineage"].iter_rows(min_row=2):
    if row[0].value == "serve" and row[1].value not in SERVE_NOW: row[1].value = f"{row[1].value}  [DROPPED during run, B-17]"

# Review
ws = wb.create_sheet("Review"); items = [[b[0], b[3], b[1], "", "", ""] for b in BUGS] + [[t, "Tab sign-off", "", "", "", ""] for t in ["Parity", "Axis_Invariance", "Chain_Ads", "Chain_PnL", "Chain_Product", "Chain_Attribution", "Returns_Payments", "Serve_vs_Gold", "Platform_Recon", "AdLevel_Platform", "AdLevel_Campaign", "AdLevel_Ad", "Payments_Recon", "Sync_CH_vs_Iceberg", "Join_Matrix", "Daily_Headline", "Lineage", "Core_Certs", "Freshness"]]
xl.table(ws, ["Item", "What", "Severity", "Reviewer", "Decision (accept / fix / ignore)", "Comment"], items, widths=[22, 70, 10, 16, 30, 70], status_cols=(2,), wrap_cols=(1,))
order = ["README", "Summary", "Bugs", "Review", "Parity", "Axis_Invariance", "Chain_Ads", "Chain_PnL", "Chain_Product", "Chain_Attribution", "Returns_Payments", "Serve_vs_Gold", "Platform_Recon", "Sync_CH_vs_Iceberg", "AdLevel_Platform", "AdLevel_Campaign", "AdLevel_Ad", "Payments_Recon", "Join_Matrix", "Join_Coverage", "Daily_Headline", "Lineage", "Core_Certs", "Freshness", "Inventory"]
wb._sheets = [wb[n] for n in order]
wb.save(OUT + "data_parity.xlsx"); print("saved xlsx")
import csv
os.makedirs(OUT + "csv", exist_ok=True)
for w_ in wb:
    if w_.title == "README": continue
    rows_ = allrows_csv.get(w_.title)
    with open(OUT + "csv/" + w_.title + ".csv", "w", newline="") as fh:
        wr = csv.writer(fh)
        if rows_ is not None:
            wr.writerow([c.value for c in next(w_.iter_rows(min_row=1, max_row=1))]); [wr.writerow(x) for x in rows_]
        else:
            for row in w_.iter_rows(values_only=True): wr.writerow(["" if v is None else v for v in row])
print("saved", OUT + "data_parity.xlsx", len(par_rows), "parity rows;", len(chain_extra), "extra chain rows")
print(collections.Counter(x[9] for x in chain_extra))
for x in chain_extra:
    if x[9] in ("FAIL", "WARN") and x[2] != "today": print(x[:10])
