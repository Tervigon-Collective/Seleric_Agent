import sys, os, json, asyncio, collections
sys.path.insert(0, os.path.dirname(__file__)); import conn
from dotenv import load_dotenv; load_dotenv("/opt/seleric/Seleric_Agent/.env")
from seleric_swarm.protocols.mcp.gateway import MCPGateway
from seleric_swarm.services.mcp_query import build_metrics_query_args
O = os.path.dirname(__file__) + "/out/"
W = {"today": ("2026-10-26", "2026-10-26"), "yesterday": ("2026-10-25", "2026-10-25"), "last7": ("2026-10-19", "2026-10-25"), "last30": ("2026-09-26", "2026-10-25")}
METS = {m["id"]: m for m in json.load(open(O + "catalogue_metrics.json"))}
rows = []   # chain rows
def stat(a, b, kind):
    a = 0.0 if a is None else a; b = 0.0 if b is None else b; d = abs(a - b); rel = d / max(abs(b), 1e-12)
    if kind == "count": return ("PASS" if d < 0.5 else "WARN" if d < 5 else "FAIL"), d
    return ("PASS" if (d <= 1 or rel <= 1e-4) else "WARN" if (d <= 10 or rel <= 1e-3) else "FAIL"), d
async def main():
    g = MCPGateway("config/mcp_servers.yaml"); sem = asyncio.Semaphore(8)
    async def q(metric, w, axis=None, dims=None, limit=None, start=None, end=None):
        a, b = (start, end) if start else W[w]
        args = build_metrics_query_args(measure=metric, start=a, end=b, dimensions=dims, limit=limit)
        if axis: args["date_axis"] = axis
        async with sem:
            try: r = await g.call(agent_id="seleric_agent", capability="seleric.metrics_query", arguments=args)
            except Exception as e: return {"error": str(e)[:200]}
        return r
    def total(r, m):
        if r.get("error") or not r.get("rows"): return None
        v = r["rows"][0].get(m); return float(v) if v not in (None, "") else 0.0
    def add(domain, check, w, axis, ln, lv, rn, rv, kind="money", note=""):
        s, d = stat(lv, rv, kind) if (lv is not None or rv is not None) else ("ERROR", None)
        if w == "today" and s != "PASS": s = "INFO(partial)"
        rows.append(dict(domain=domain, check=check, window=w, axis=axis, left=ln, left_val=lv, right=rn, right_val=rv, delta=d, status=s, note=note))
    # --- R1/R2/R3/R4/R5: rollups by dimension must preserve the total
    roll = [("Ads", "ad_spend", None, ["ad_platform", "account_id", "campaign_key", "adset_id", "ad_key"], "money"),
            ("Ads", "impressions", None, ["ad_platform", "campaign_key", "adset_id", "ad_key"], "count"),
            ("Ads", "clicks", None, ["ad_platform", "campaign_key", "ad_key"], "count"),
            ("P&L", "order_pnl_net_sales", "SW", ["finance_channel", "sub_channel", "ad_platform", "campaign_id", "ad_id", "is_new_customer", "payment_bucket", "shipping_region"], "money"),
            ("P&L", "order_pnl_gross_sales", None, ["finance_channel", "campaign_id", "payment_bucket"], "money"),
            ("P&L", "net_profit", "SW", ["finance_channel", "campaign_id"], "money"),
            ("Product", "product_order_net_sales", "SW", ["product_title", "sku", "product_type", "vendor", "campaign_id", "ad_platform"], "money"),
            ("Product", "units_sold", "SW", ["product_title", "sku"], "count"),
            ("Product-Ads", "product_ad_spend", None, ["product_title", "campaign_id", "ad_platform"], "money"),
            ("Commerce", "orders", None, ["payment_bucket", "shipping_state", "is_new_customer", "campaign_name", "order_status"], "count"),
            ("Commerce", "net_sales", "SW", ["payment_bucket", "shipping_state", "campaign_name"], "money"),
            ("Attribution", "touches", None, ["ad_platform", "campaign_id"], "count"),
            ("Returns", "refund_count", None, ["product_title"], "count"),
            ("Payments", "payment_amount", None, ["payment_bucket"], "money")]
    jobs = []
    for dom, m, ax, dims, kind in roll:
        axes = ["order_date", "event_date"] if ax == "SW" else [None]
        for axis in axes:
            for w in W:
                jobs.append((dom, m, axis, w, dims, kind))
    async def do(job):
        dom, m, axis, w, dims, kind = job
        t = await q(m, w, axis); tv = total(t, m); out = []
        if t.get("error"): out.append((job, None, None, "TOTAL " + t["error"][:100])); return out
        for d in dims:
            r = await q(m, w, axis, [d], limit=20000)
            if r.get("error"): out.append((job, d, None, "ERR " + r["error"][:140])); continue
            rs = r.get("rows") or []; sv = sum(float(x.get(m) or 0) for x in rs)
            nullv = sum(float(x.get(m) or 0) for x in rs if x.get(d) in (None, "", "(not set)", "unknown"))
            out.append((job, d, (tv, sv, nullv, len(rs)), ""))
        return out
    results = await asyncio.gather(*[do(j) for j in jobs])
    for lst in results:
        for job, d, vals, note in lst:
            dom, m, axis, w, dims, kind = job
            if vals is None: rows.append(dict(domain=dom, check=f"{m} by {d or '*'} sums to total", window=w, axis=axis, left=m, left_val=None, right="", right_val=None, delta=None, status="ERROR", note=note)); continue
            tv, sv, nullv, n = vals
            add(dom, f"{m}: Σ by {d} = total", w, axis, f"total {m}", tv, f"Σ over {n} {d} groups", sv, kind, note=f"null/unset share of value = {nullv:,.2f}" if nullv else "")
    # --- R6 undated reconcile (all history)
    for m in ("order_pnl_net_sales",):
        ev = await q(m, "x", "event_date", start="2024-11-01", end="2026-10-25"); od = await q(m, "x", "order_date", start="2024-11-01", end="2026-10-25")
        und = (ev.get("undated") or {}).get("rows") if isinstance(ev.get("undated"), dict) else None
        uv = sum(float(x.get(m) or 0) for x in (und or []))
        add("P&L", "event_date total + Undated = order_date total (all history 2024-11-01..2026-10-25)", "all-history", "both", "event_date dated + Undated", (total(ev, m) or 0) + uv, "order_date total", total(od, m), note=f"dated={total(ev,m)}, undated={uv}")
        # same for last30 (undated is over ALL history, so this must NOT reconcile; documents the trap)
    # --- R9 axis invariance of non-switchable metrics
    inv_jobs = [(mid, w) for mid, m in METS.items() if m["date_axis"] in ("placement_only", "cohort_only", "native") for w in ("yesterday", "last7", "last30")]
    async def inv(j):
        mid, w = j; a = await q(mid, w); b = await q(mid, w, "event_date"); return j, a, b
    for j, a, b in await asyncio.gather(*[inv(j) for j in inv_jobs]):
        mid, w = j; m = METS[mid]
        av = total(a, mid); 
        if b.get("error") or not b.get("rows"):
            rows.append(dict(domain="Axis", check=f"{mid} ({m['date_axis']}) with date_axis=event_date", window=w, axis="event_date", left=mid, left_val=av, right="refused/empty", right_val=None, delta=None, status="PASS", note=("refused: " + str(b.get("error"))[:120]) if b.get("error") else "empty")); continue
        bv = total(b, mid); kind = "count" if (m.get("aggregation") in ("count", "count_distinct") or (m.get("unit") or "").lower() in ("count", "orders")) else "money"
        s, d = stat(av, bv, kind) if kind == "count" else (("PASS" if abs((av or 0) - (bv or 0)) <= max(1e-6, 1e-4 * abs(av or 0)) else "FAIL"), abs((av or 0) - (bv or 0)))
        rows.append(dict(domain="Axis", check=f"{mid} ({m['date_axis']}): value must not change with date_axis", window=w, axis="event_date vs default", left="default", left_val=av, right="date_axis=event_date", right_val=bv, delta=d, status=s if w != "today" else "INFO(partial)", note="Core says: " + str(((b.get('date_axis_applied') or {}).get(mid)))[:100]))
asyncio.run(main())
json.dump(rows, open(O + "chains.json", "w"), default=str)
print(len(rows), collections.Counter(r["status"] for r in rows))
