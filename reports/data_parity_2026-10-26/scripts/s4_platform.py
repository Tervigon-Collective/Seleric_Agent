import sys, os, json, asyncio, collections, datetime as dt
sys.path.insert(0, os.path.dirname(__file__)); import conn
from dotenv import load_dotenv; load_dotenv("/opt/seleric/Seleric_Agent/.env")
from seleric_swarm.protocols.mcp.gateway import MCPGateway
O = os.path.dirname(__file__) + "/out/"
A, B = "2026-09-26", "2026-10-26"   # last30 complete days + today
T = lambda sql: conn.trino_q(sql)[1]
ch = conn.ch(); C = lambda sql: ch.query(sql).result_rows
out = {}   # key -> {day: {layer: value}}
def put(grp, layer, rows):
    for r in rows:
        d = str(r[0]); out.setdefault(grp, {}).setdefault(d, {})[layer] = [None if v is None else float(v) for v in r[1:]]
# ---- META: spend, impressions, clicks
mj = lambda k: f"json_extract_scalar(data,'$.{k}')"
put("meta", "1_raw (dedup latest/ad-day)", T(f"""
WITH r AS (SELECT {mj('date_start')} d, {mj('ad_id')} ad_id, TRY_CAST({mj('spend')} AS double) s, TRY_CAST({mj('impressions')} AS bigint) i, TRY_CAST({mj('clicks')} AS bigint) c,
  row_number() OVER (PARTITION BY {mj('ad_id')}, {mj('date_start')} ORDER BY extracted_at DESC) rn
  FROM iceberg.raw.meta_ads_insights_daily WHERE brand_id='20' AND dt >= DATE '2026-09-20')
SELECT d, sum(s), sum(i), sum(c) FROM r WHERE rn=1 AND d BETWEEN '{A}' AND '{B}' GROUP BY d"""))
put("meta", "2_silver", T(f"SELECT CAST(date_start AS varchar), sum(spend), sum(impressions), sum(clicks) FROM iceberg.silver.meta_ads__insights_daily WHERE brand_id=20 AND date_start BETWEEN DATE '{A}' AND DATE '{B}' GROUP BY 1"))
put("meta", "3_iceberg_gold", T(f"SELECT CAST(report_date AS varchar), sum(spend), sum(impressions), sum(clicks) FROM iceberg.gold.fct_meta_ads_daily WHERE brand_id=20 AND report_date BETWEEN DATE '{A}' AND DATE '{B}' GROUP BY 1"))
put("meta", "4_ch_gold", C(f"SELECT toString(report_date), sum(spend), sum(impressions), sum(clicks) FROM gold.fct_meta_ads_daily FINAL WHERE brand_id=20 AND report_date BETWEEN '{A}' AND '{B}' GROUP BY 1"))
put("meta", "5_ch_serve", C(f"SELECT toString(report_date), sum(spend), sum(impressions), sum(clicks) FROM serve.fct_ad_delivery WHERE brand_id=20 AND ad_platform='meta' AND report_date BETWEEN '{A}' AND '{B}' GROUP BY 1"))
put("meta", "6_ch_serve_paid_media", C(f"SELECT toString(report_date), sum(spend), sum(impressions), sum(clicks) FROM serve.fct_paid_media_daily WHERE brand_id=20 AND ad_platform='meta' AND row_type='delivery' AND report_date BETWEEN '{A}' AND '{B}' GROUP BY 1"))
# ---- GOOGLE: spend, impressions, clicks  (raw campaign performance, dedup latest per campaign-day-device-network)
gj = lambda k: f"json_extract_scalar(data,'$.{k}')"
put("google", "1_raw (dedup latest/camp-day-dev-net)", T(f"""
WITH r AS (SELECT {gj('segments.date')} d, TRY_CAST({gj('metrics.costMicros')} AS double)/1e6 s, TRY_CAST({gj('metrics.impressions')} AS bigint) i, TRY_CAST({gj('metrics.clicks')} AS bigint) c,
  row_number() OVER (PARTITION BY {gj('campaign.id')}, {gj('segments.date')}, {gj('segments.device')}, {gj('segments.adNetworkType')} ORDER BY extracted_at DESC) rn
  FROM iceberg.raw.google_ads_campaign_performance WHERE brand_id='20' AND dt >= DATE '2026-09-20')
SELECT d, sum(s), sum(i), sum(c) FROM r WHERE rn=1 AND d BETWEEN '{A}' AND '{B}' GROUP BY d"""))
put("google", "2_silver", T(f"SELECT CAST(report_date AS varchar), sum(cost), sum(impressions), sum(clicks) FROM iceberg.silver.google_ads__campaign_performance WHERE brand_id=20 AND report_date BETWEEN DATE '{A}' AND DATE '{B}' GROUP BY 1"))
put("google", "3_iceberg_gold", T(f"SELECT CAST(report_date AS varchar), sum(spend), sum(impressions), sum(clicks) FROM iceberg.gold.fct_google_ads_daily WHERE brand_id=20 AND report_date BETWEEN DATE '{A}' AND DATE '{B}' GROUP BY 1"))
put("google", "4_ch_gold", C(f"SELECT toString(report_date), sum(spend), sum(impressions), sum(clicks) FROM gold.fct_google_ads_daily FINAL WHERE brand_id=20 AND report_date BETWEEN '{A}' AND '{B}' GROUP BY 1"))
put("google", "5_ch_serve", C(f"SELECT toString(report_date), sum(spend), sum(impressions), sum(clicks) FROM serve.fct_ad_delivery WHERE brand_id=20 AND ad_platform='google' AND report_date BETWEEN '{A}' AND '{B}' GROUP BY 1"))
put("google", "6_ch_serve_paid_media", C(f"SELECT toString(report_date), sum(spend), sum(impressions), sum(clicks) FROM serve.fct_paid_media_daily WHERE brand_id=20 AND ad_platform='google' AND row_type='delivery' AND report_date BETWEEN '{A}' AND '{B}' GROUP BY 1"))
# ---- SHOPIFY orders: [orders, total_price(incl tax), non-test orders]
sj = lambda k: f"json_extract_scalar(data,'$.{k}')"
put("shopify", "1_raw (dedup latest/order; all)", T(f"""
WITH r AS (SELECT substr({sj('created_at')},1,10) d, {sj('id')} id, TRY_CAST({sj('total_price')} AS double) p, {sj('test')} tst,
  row_number() OVER (PARTITION BY {sj('id')} ORDER BY extracted_at DESC) rn FROM iceberg.raw.shopify_orders WHERE brand_id='20' AND dt >= DATE '2026-09-20')
SELECT d, count(*), sum(p), count_if(tst='false') FROM r WHERE rn=1 AND d BETWEEN '{A}' AND '{B}' GROUP BY d"""))
put("shopify", "3_iceberg_gold fct_orders (all)", T(f"SELECT CAST(order_date AS varchar), count(distinct order_id), sum(CAST(gross_revenue AS double)), count_if(NOT coalesce(is_test,false)) FROM iceberg.gold.fct_orders WHERE brand_id=20 AND order_date BETWEEN DATE '{A}' AND DATE '{B}' GROUP BY 1"))
put("shopify", "4_ch_gold fct_orders (all)", C(f"SELECT toString(order_date), uniqExact(order_id), sum(gross_revenue), countIf(NOT is_test) FROM gold.fct_orders FINAL WHERE brand_id=20 AND order_date BETWEEN '{A}' AND '{B}' GROUP BY 1"))
put("shopify", "5_ch_serve fct_order (sale orders only)", C(f"SELECT toString(order_date), uniqExact(order_id), sum(src_gross_revenue_incl_gst), uniqExactIf(order_id, is_sale_order) FROM serve.fct_order WHERE brand_id=20 AND order_date BETWEEN '{A}' AND '{B}' GROUP BY 1"))
# ---- Cube values per day via MCP
async def cube_series():
    from seleric_swarm.services.mcp_query import build_metrics_query_args
    g = MCPGateway("config/mcp_servers.yaml")
    async def q(metric, plat=None, axis=None):
        args = build_metrics_query_args(measure=metric, start=A, end=B, grain="day")
        args["granularity"] = "day"
        if plat: args["filters"] = (args.get("filters") or []) + [{"dimension": "ad_platform", "operator": "equals", "values": [plat]}]
        if axis: args["date_axis"] = axis
        return await g.call(agent_id="seleric_agent", capability="seleric.metrics_query", arguments=args)
    res = {}
    for plat in ("meta", "google"):
        for m in ("ad_spend", "impressions", "clicks"): res[(plat, m)] = await q(m, plat)
    res[("shopify", "orders")] = await q("orders"); res[("shopify", "gross_sales")] = await q("gross_sales"); res[("shopify", "order_value_incl_tax")] = await q("order_value_incl_tax")
    return res
cres = asyncio.run(cube_series())
def day_of(r):
    for k, v in r.items():
        if "date" in k.lower() or "day" in k.lower() or k.endswith("order_date"): return str(v)[:10]
for (plat, m), r in cres.items():
    rows = r.get("rows") or []
    if rows and not any(k for k in rows[0] if k not in (m,)): pass
    print(plat, m, len(rows), list(rows[0].items())[:3] if rows else r.get("error") or r.get("warnings"))
json.dump({"series": out, "cube": {f"{p}|{m}": r.get("rows") for (p, m), r in cres.items()}}, open(O + "platform_recon.json", "w"), default=str)
print("saved")
