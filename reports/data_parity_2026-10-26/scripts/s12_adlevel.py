import sys, os, json, asyncio, collections; sys.path.insert(0, os.path.dirname(__file__)); import conn
from dotenv import load_dotenv; load_dotenv("/opt/seleric/Seleric_Agent/.env")
from seleric_swarm.protocols.mcp.gateway import MCPGateway
from seleric_swarm.services.mcp_query import build_metrics_query_args
O = os.path.dirname(__file__) + "/out/"; ch = conn.ch(); Q = lambda s: ch.query(s).result_rows
W = {"yesterday": ("2026-10-25", "2026-10-25"), "last7": ("2026-10-19", "2026-10-25"), "last30": ("2026-09-26", "2026-10-25")}
def delivery(w, level):
    a, b = W[w]; keys = {"campaign": "campaign_id", "ad": "campaign_id, ad_id"}[level]; nm = {"campaign": "any(campaign_name)", "ad": "any(campaign_name), any(ad_name)"}[level]
    out = {}
    for plat, tab, pc, pv in (("meta", "gold.fct_meta_ads_daily", "purchases", "purchase_value"), ("google", "gold.fct_google_ads_daily", "conversions", "conversion_value")):
        for r in Q(f"SELECT {keys}, {nm}, sum(spend), sum(impressions), sum(clicks), sum({pc}), sum({pv}) FROM {tab} FINAL WHERE brand_id=20 AND report_date BETWEEN '{a}' AND '{b}' GROUP BY {keys}"):
            if level == "campaign": k = (plat, r[0], None); names = (r[1], None); vals = r[2:]
            else: k = (plat, r[0], r[1]); names = (r[2], r[3]); vals = r[4:]
            out[k] = dict(platform=plat, campaign_id=k[1], ad_id=k[2], campaign_name=names[0], ad_name=names[1], spend=float(vals[0] or 0), impressions=float(vals[1] or 0), clicks=float(vals[2] or 0), plat_conv=float(vals[3] or 0), plat_value=float(vals[4] or 0))
    return out
async def attributed():
    g = MCPGateway("config/mcp_servers.yaml"); res = {}
    async def q(m, w, dims, axis=None):
        a, b = W[w]; args = build_metrics_query_args(measure=m, start=a, end=b, dimensions=dims, limit=50000)
        if axis: args["date_axis"] = axis
        return await g.call(agent_id="seleric_agent", capability="seleric.metrics_query", arguments=args)
    for w in W:
        for level, dims in (("campaign", ["ad_platform", "campaign_id"]), ("ad", ["ad_platform", "campaign_id", "ad_id"])):
            if level == "ad" and w != "last30": continue
            o = await q("channel_orders", w, dims); n = await q("order_pnl_net_sales", w, dims, "order_date")
            tot_o = await q("channel_orders", w, None); tot_n = await q("order_pnl_net_sales", w, None, "order_date")
            res[(w, level)] = dict(orders=o.get("rows") or [], net=n.get("rows") or [], err=o.get("error") or n.get("error"), tot_orders=float((tot_o.get("rows") or [{}])[0].get("channel_orders") or 0), tot_net=float((tot_n.get("rows") or [{}])[0].get("order_pnl_net_sales") or 0))
    return res
att = asyncio.run(attributed()); out = {"campaign": {}, "ad": {}, "totals": {}}
for (w, level), v in att.items():
    if v["err"]: print("ERR", w, level, v["err"]); continue
    dl = delivery(w, level); rows = {}
    for k, d in dl.items(): rows[k] = dict(d, orders=0.0, net_sales=0.0, in_delivery=True)
    for r in v["orders"]:
        k = (r.get("ad_platform"), r.get("campaign_id"), r.get("ad_id") if level == "ad" else None)
        rows.setdefault(k, dict(platform=k[0], campaign_id=k[1], ad_id=k[2], campaign_name=None, ad_name=None, spend=0.0, impressions=0.0, clicks=0.0, plat_conv=0.0, plat_value=0.0, orders=0.0, net_sales=0.0, in_delivery=False))["orders"] += float(r.get("channel_orders") or 0)
    for r in v["net"]:
        k = (r.get("ad_platform"), r.get("campaign_id"), r.get("ad_id") if level == "ad" else None)
        rows.setdefault(k, dict(platform=k[0], campaign_id=k[1], ad_id=k[2], campaign_name=None, ad_name=None, spend=0.0, impressions=0.0, clicks=0.0, plat_conv=0.0, plat_value=0.0, orders=0.0, net_sales=0.0, in_delivery=False))["net_sales"] += float(r.get("order_pnl_net_sales") or 0)
    out[level][w] = list(rows.values()); out["totals"][f"{w}|{level}"] = dict(tot_orders=v["tot_orders"], tot_net=v["tot_net"], sum_orders=sum(r["orders"] for r in rows.values()), sum_net=sum(r["net_sales"] for r in rows.values()))
# ClickHouse cross-check of attributed orders: meta attribution mart
a, b = W["last30"]
out["meta_mart_last30"] = Q(f"SELECT sum(attributed_orders), sum(net_revenue_excl_tax), uniqExact(ad_id) FROM gold.mart_meta_ad_daily_attribution FINAL WHERE brand_id=20 AND report_date BETWEEN '{a}' AND '{b}'")[0]
json.dump(out, open(O + "adlevel.json", "w"), default=str)
for k, v in out["totals"].items(): print(k, v)
print("meta mart", out["meta_mart_last30"])
